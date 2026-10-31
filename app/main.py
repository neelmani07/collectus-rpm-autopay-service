from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

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
    description="Collectus Smart Autocharge — shop, installments, cron Smart Autopay",
    version="0.4.0",
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
def pay(installment_id: str, body: PayInstallmentRequest) -> ManualPayResult:
    return pay_installment(installment_id, body.customer_id)


@app.post("/api/cron/collections", response_model=CronRunResult)
async def cron_collections(body: CronRequest) -> CronRunResult:
    """Simulate the daily collections cron (e.g. 09:00). Smart Autocharge only."""
    return await run_collections_cron(
        customer_id=body.customer_id,
        scheduled_for=body.scheduled_for,
    )


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
