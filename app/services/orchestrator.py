"""RPM → AI → Autopay orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import get_settings
from app.db import (
    event_exists,
    get_customer,
    get_decision,
    save_decision,
    save_event,
    update_overdue,
)
from app.models import (
    DecisionAction,
    DecisionRequest,
    FeatureSet,
    OrchestrationResult,
    RepayRequest,
)
from app.services import ai_client, autopay


def _repayment_strength(amount_paid: float, overdue_amount: float) -> float:
    if overdue_amount <= 0:
        return 1.0
    return max(0.0, min(1.0, amount_paid / max(overdue_amount, 1.0)))


async def process_repayment(request: RepayRequest) -> OrchestrationResult:
    customer = get_customer(request.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Unknown customer_id: {request.customer_id}")

    event_id = f"evt_{uuid.uuid4().hex}"
    if event_exists(event_id):
        existing = get_decision(event_id)
        if existing:
            return OrchestrationResult.model_validate(existing)

    overdue_before = float(customer["overdue_amount"])
    features = FeatureSet(
        days_overdue=int(customer["days_overdue"]),
        pm_health=float(customer["pm_health"]),
        fails_7d=int(customer["fails_7d"]),
        recent_success=float(customer["recent_success"]),
        repayment_strength=_repayment_strength(request.amount, overdue_before),
        overdue_amount=overdue_before,
        amount_paid=request.amount,
    )

    save_event(
        event_id=event_id,
        customer_id=request.customer_id,
        event_type="repayment.succeeded",
        amount=request.amount,
        currency=request.currency,
    )

    decision_payload = DecisionRequest(
        event_id=event_id,
        customer_id=request.customer_id,
        event_type="repayment.succeeded",
        amount_paid=request.amount,
        overdue_amount=overdue_before,
        currency=request.currency,
        features=features,
    )

    settings = get_settings()
    decision = await ai_client.decide(decision_payload, settings)

    charge_result = autopay.execute_charge(
        customer=customer,
        amount=decision.amount,
        action=decision.action,
        idempotency_key=event_id,
    )

    overdue_after = overdue_before
    if charge_result.status == "success" and decision.action != DecisionAction.SKIP:
        overdue_after = max(0.0, overdue_before - decision.amount)
        update_overdue(request.customer_id, overdue_after)

    result = OrchestrationResult(
        event_id=event_id,
        customer_id=request.customer_id,
        customer_name=customer["name"],
        scenario=customer["scenario"],
        amount_paid=request.amount,
        overdue_before=overdue_before,
        currency=request.currency,
        decision=decision,
        autopay=charge_result,
        overdue_after=overdue_after,
        created_at=datetime.now(timezone.utc),
    )
    save_decision(result)
    return result
