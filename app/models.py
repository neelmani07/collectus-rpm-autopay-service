from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DecisionAction(str, Enum):
    CHARGE = "charge"
    PARTIAL = "partial"
    SKIP = "skip"


class CustomerSeed(BaseModel):
    customer_id: str
    name: str
    overdue_amount: float
    days_overdue: int
    pm_health: float = Field(ge=0, le=1)
    fails_7d: int = 0
    recent_success: float = Field(ge=0, le=1, default=0.5)
    currency: str = "SAR"
    scenario: str
    notes: str = ""


class RepayRequest(BaseModel):
    customer_id: str
    amount: float = Field(gt=0)
    currency: str = "SAR"


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
    source: str = "mock"  # mock | remote


class AutopayResult(BaseModel):
    status: str
    transaction_id: Optional[str] = None
    message: str = ""


class OrchestrationResult(BaseModel):
    event_id: str
    customer_id: str
    customer_name: str
    scenario: str
    amount_paid: float
    overdue_before: float
    currency: str
    decision: DecisionResponse
    autopay: Optional[AutopayResult] = None
    overdue_after: float
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    service: str
    mock_ai: bool
    ai_decision_url: str


class ErrorBody(BaseModel):
    detail: str
    extra: Optional[dict[str, Any]] = None
