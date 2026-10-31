from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import (
    get_customer,
    init_db,
    list_customers,
    list_decisions,
    list_installments,
    list_orders,
    list_products,
    refresh_installment_statuses,
    reset_seed_data,
)
from app.models import (
    CheckoutRequest,
    CheckoutResult,
    HealthResponse,
    OrchestrationResult,
    PayInstallmentRequest,
)
from app.services.orchestrator import checkout, pay_installment


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    refresh_installment_statuses()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Collectus Smart Autocharge — shop, installments, RPM + Autopay",
    version="0.2.0",
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
    return {"customers": list_customers()}


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


@app.post("/api/installments/{installment_id}/pay", response_model=OrchestrationResult)
async def pay(installment_id: str, body: PayInstallmentRequest) -> OrchestrationResult:
    return await pay_installment(installment_id, body.customer_id)


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
