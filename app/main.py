from contextlib import asynccontextmanager
import uuid
from typing import AsyncIterator, Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import (
    get_customer,
    init_db,
    list_customers,
    list_decisions,
    list_installments,
    list_orders,
    list_payment_history,
    list_products,
    refresh_installment_statuses,
    reset_seed_data,
)
from app.models import (
    CheckoutRequest,
    CheckoutResult,
    CronRunResult,
    CustomerOut,
    HealthResponse,
    ManualPayResult,
    PayInstallmentRequest,
    PaymentDetailResponse,
    PaymentHistoryItem,
)
from app.services.insights import model_info, month_backtest, playground_decide
from app.services.orchestrator import (
    checkout,
    pay_installment,
    payment_detail,
    run_collections_cron,
)


class CronRequest(BaseModel):
    customer_id: Optional[str] = None
    scheduled_for: str = Field(default="09:00")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    refresh_installment_statuses()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Collectus — manual pay, event Smart Autocharge, cron Normal Autocharge",
    version="0.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        mock_ai=settings.use_mock_ai,
        ai_decision_url=settings.ai_decision_url,
    )


@app.get("/api/customers")
def customers() -> dict:
    """Login directory only — UI must not mix accounts after login."""
    return {
        "customers": [
            CustomerOut.model_validate(
                {
                    **c,
                    "notes": "",  # hide internal scenario notes from login list
                    "scenario": "",
                }
            )
            for c in list_customers()
        ]
    }


@app.get("/api/customers/{customer_id}", response_model=CustomerOut)
def customer_me(customer_id: str) -> CustomerOut:
    row = get_customer(customer_id)
    if not row:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Customer not found")
    # Strip internal demo labels from customer-facing payload
    safe = {**row, "notes": "", "scenario": ""}
    return CustomerOut.model_validate(safe)


@app.get("/api/products")
def products() -> dict:
    return {"products": list_products()}


@app.post("/api/checkout", response_model=CheckoutResult)
def create_order(body: CheckoutRequest) -> CheckoutResult:
    return checkout(body.customer_id, body.product_id, body.plan)


@app.get("/api/customers/{customer_id}/orders")
def customer_orders(customer_id: str) -> dict:
    if not get_customer(customer_id):
        return {"orders": []}
    return {"orders": list_orders(customer_id)}


@app.get("/api/customers/{customer_id}/installments")
def customer_installments(customer_id: str) -> dict:
    if not get_customer(customer_id):
        return {"installments": []}
    return {"installments": list_installments(customer_id)}


@app.get("/api/customers/{customer_id}/payment-history")
def customer_payment_history(customer_id: str) -> dict:
    if not get_customer(customer_id):
        return {"history": []}
    return {
        "history": [
            PaymentHistoryItem.model_validate(item)
            for item in list_payment_history(customer_id)
        ]
    }


@app.get("/api/payment-events/{event_id}", response_model=PaymentDetailResponse)
def payment_event_detail(event_id: str) -> PaymentDetailResponse:
    return payment_detail(event_id)


@app.post("/api/installments/{installment_id}/pay", response_model=ManualPayResult)
async def pay(installment_id: str, body: PayInstallmentRequest) -> ManualPayResult:
    return await pay_installment(installment_id, body.customer_id)


@app.post("/api/cron/collections", response_model=CronRunResult)
async def cron_collections(body: CronRequest) -> CronRunResult:
    """Simulate daily Normal Autocharge cron (e.g. 09:00). Overdue only, no AI."""
    return await run_collections_cron(
        customer_id=body.customer_id,
        scheduled_for=body.scheduled_for,
    )


@app.get("/api/insights/backtest")
async def insights_backtest(refresh: bool = False) -> dict:
    """30-day backtest from the AI service: cron vs always-charge vs simple rule vs AI."""
    return await month_backtest(settings, refresh=refresh)


class PlaygroundRequest(BaseModel):
    """Any customer profile; nothing is charged or stored."""

    event_type: Literal["repayment.succeeded", "schedule.due"] = "repayment.succeeded"
    overdue_amount: float = Field(gt=0, le=20000)
    amount_paid: float = Field(ge=0, le=20000)
    days_overdue: int = Field(ge=0, le=365)
    pm_health: float = Field(ge=0, le=1)
    fails_7d: int = Field(ge=0, le=10)
    recent_success: float = Field(ge=0, le=1)
    declines_left: int = Field(ge=0, le=6)
    card_network: Literal["mada", "visa", "mastercard"] = "mada"
    card_token: Literal["plastic", "apple_pay"] = "plastic"
    card_funding: Literal["debit", "credit", "prepaid"] = "debit"


@app.post("/api/playground/decide")
async def playground(body: PlaygroundRequest) -> dict:
    """Try the AI with your own input. Read-only: nothing is charged or saved."""
    repayment = body.event_type == "repayment.succeeded"
    payload = {
        "event_id": f"evt_play_{uuid.uuid4().hex[:16]}",
        "customer_id": "playground",
        "event_type": body.event_type,
        "amount_paid": body.amount_paid if repayment else 0.0,
        "overdue_amount": body.overdue_amount,
        "currency": "SAR",
        "features": {
            "days_overdue": body.days_overdue,
            "pm_health": body.pm_health,
            "fails_7d": body.fails_7d,
            "recent_success": body.recent_success,
            "repayment_strength": min(1.0, body.amount_paid / body.overdue_amount) if repayment else 0.0,
            "declines_left": body.declines_left,
            "card_network": body.card_network,
            "card_token": body.card_token,
            "card_funding": body.card_funding,
        },
    }
    return await playground_decide(settings, payload)


@app.get("/api/insights/model")
async def insights_model() -> dict:
    """What the live model learned: coefficients and quality metrics."""
    return await model_info(settings)


@app.get("/api/decisions")
def decisions(limit: int = 20) -> dict:
    return {"decisions": list_decisions(limit=limit)}


@app.post("/api/admin/reset")
def reset() -> dict:
    reset_seed_data()
    return {
        "status": "reset",
        "customers": list_customers(),
        "products": list_products(),
    }
