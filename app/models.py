from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DecisionAction(str, Enum):
    CHARGE = "charge"
    PARTIAL = "partial"
    SKIP = "skip"


class InstallmentStatus(str, Enum):
    PAID = "paid"
    UPCOMING = "upcoming"
    DUE = "due"
    OVERDUE = "overdue"


class CustomerOut(BaseModel):
    customer_id: str
    name: str
    pm_health: float
    fails_7d: int
    recent_success: float
    currency: str = "SAR"
    scenario: str
    notes: str = ""
    overdue_amount: float = 0
    days_overdue: int = 0


class ProductOut(BaseModel):
    product_id: str
    name: str
    merchant: str
    category: str
    price: float
    currency: str = "SAR"
    badge: str = ""
    accent: str = "#9600F1"
    description: str = ""


class CheckoutRequest(BaseModel):
    customer_id: str
    product_id: str
    plan: int = Field(default=4, ge=2, le=6)


class PayInstallmentRequest(BaseModel):
    customer_id: str


class FeatureSet(BaseModel):
    days_overdue: int
    pm_health: float
    fails_7d: int
    recent_success: float
    repayment_strength: float
    overdue_amount: float
    amount_paid: float


class DecisionRequest(BaseModel):
    event_id: str
    customer_id: str
    event_type: str = "repayment.succeeded"
    amount_paid: float
    overdue_amount: float
    currency: str = "SAR"
    features: FeatureSet


class DecisionResponse(BaseModel):
    action: DecisionAction
    amount: float
    score: float
    reason: str
    baseline_action: DecisionAction = DecisionAction.CHARGE
    source: str = "mock"


class AutopayResult(BaseModel):
    status: str
    transaction_id: Optional[str] = None
    message: str = ""
    installments_settled: list[str] = Field(default_factory=list)


class InstallmentOut(BaseModel):
    installment_id: str
    order_id: str
    customer_id: str
    product_name: str
    merchant: str
    sequence: int
    plan_size: int
    amount: float
    currency: str
    due_date: str
    status: InstallmentStatus
    paid_at: Optional[str] = None


class OrderOut(BaseModel):
    order_id: str
    customer_id: str
    product_id: str
    product_name: str
    merchant: str
    total_amount: float
    currency: str
    plan: int
    created_at: str
    accent: str = "#9600F1"
    installments: list[InstallmentOut] = Field(default_factory=list)


class CheckoutResult(BaseModel):
    order: OrderOut
    first_payment_amount: float
    message: str


class OrchestrationResult(BaseModel):
    event_id: str
    customer_id: str
    customer_name: str
    scenario: str
    amount_paid: float
    paid_installment_id: Optional[str] = None
    overdue_before: float
    currency: str
    decision: DecisionResponse
    autopay: Optional[AutopayResult] = None
    overdue_after: float
    created_at: datetime
    order_snapshot: Optional[OrderOut] = None


class HealthResponse(BaseModel):
    status: str
    service: str
    mock_ai: bool
    ai_decision_url: str


class ErrorBody(BaseModel):
    detail: str
    extra: Optional[dict[str, Any]] = None
