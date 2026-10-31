"""Checkout + installment repayment → RPM event → AI → Autopay."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException

from app.config import get_settings
from app.db import (
    apply_autocharge_to_overdue,
    create_checkout,
    get_customer,
    get_installment,
    get_order,
    list_overdue_installments,
    mark_installment_paid,
    save_decision,
    save_event,
)
from app.models import (
    AutopayResult,
    CheckoutResult,
    DecisionAction,
    DecisionRequest,
    FeatureSet,
    OrderOut,
    OrchestrationResult,
)
from app.services import ai_client, autopay


def _repayment_strength(amount_paid: float, overdue_amount: float) -> float:
    if overdue_amount <= 0:
        return 1.0
    return max(0.0, min(1.0, amount_paid / max(overdue_amount, 1.0)))


def checkout(customer_id: str, product_id: str, plan: int) -> CheckoutResult:
    try:
        order = create_checkout(customer_id, product_id, plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    first = next((i for i in order["installments"] if i["sequence"] == 1), None)
    first_amount = float(first["amount"]) if first else 0.0
    return CheckoutResult(
        order=OrderOut.model_validate(order),
        first_payment_amount=first_amount,
        message=(
            f"Paid first installment {order['currency']} {first_amount:.2f}. "
            f"{plan - 1} upcoming payments created."
        ),
    )


async def pay_installment(installment_id: str, customer_id: str) -> OrchestrationResult:
    customer = get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Unknown customer_id: {customer_id}")

    installment = get_installment(installment_id)
    if not installment:
        raise HTTPException(status_code=404, detail="Installment not found")
    if installment["customer_id"] != customer_id:
        raise HTTPException(status_code=403, detail="Installment does not belong to customer")
    if installment["status"] == "paid":
        raise HTTPException(status_code=400, detail="Installment already paid")

    # 1) Customer pays this installment (RPM repayment success)
    paid = mark_installment_paid(installment_id)
    amount_paid = float(paid["amount"])
    currency = paid["currency"]

    # 2) Remaining overdue → Smart Autocharge candidates
    overdue_items = list_overdue_installments(customer_id, exclude_id=installment_id)
    overdue_before = round(sum(float(i["amount"]) for i in overdue_items), 2)
    days_overdue = 0
    today = date.today()
    for item in overdue_items:
        due = date.fromisoformat(item["due_date"])
        days_overdue = max(days_overdue, (today - due).days)

    event_id = f"evt_{uuid.uuid4().hex}"
    save_event(
        event_id=event_id,
        customer_id=customer_id,
        event_type="repayment.succeeded",
        amount=amount_paid,
        currency=currency,
    )

    features = FeatureSet(
        days_overdue=days_overdue,
        pm_health=float(customer["pm_health"]),
        fails_7d=int(customer["fails_7d"]),
        recent_success=float(customer["recent_success"]),
        repayment_strength=_repayment_strength(amount_paid, overdue_before or amount_paid),
        overdue_amount=overdue_before,
        amount_paid=amount_paid,
    )

    decision_payload = DecisionRequest(
        event_id=event_id,
        customer_id=customer_id,
        event_type="repayment.succeeded",
        amount_paid=amount_paid,
        overdue_amount=overdue_before,
        currency=currency,
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
    if (
        charge_result.status == "success"
        and decision.action != DecisionAction.SKIP
        and decision.amount > 0
    ):
        settled = apply_autocharge_to_overdue(
            customer_id,
            decision.amount,
            exclude_id=installment_id,
        )
        remaining = list_overdue_installments(customer_id, exclude_id=installment_id)
        overdue_after = round(sum(float(i["amount"]) for i in remaining), 2)
        charge_result = AutopayResult(
            status=charge_result.status,
            transaction_id=charge_result.transaction_id,
            message=charge_result.message,
            installments_settled=settled,
        )
    elif overdue_before <= 0:
        charge_result = AutopayResult(
            status="skipped",
            transaction_id=None,
            message="No additional overdue installments to autocharge",
            installments_settled=[],
        )

    order_snapshot = get_order(paid["order_id"])
    result = OrchestrationResult(
        event_id=event_id,
        customer_id=customer_id,
        customer_name=customer["name"],
        scenario=customer["scenario"],
        amount_paid=amount_paid,
        paid_installment_id=installment_id,
        overdue_before=overdue_before,
        currency=currency,
        decision=decision,
        autopay=charge_result,
        overdue_after=overdue_after,
        created_at=datetime.now(timezone.utc),
        order_snapshot=OrderOut.model_validate(order_snapshot) if order_snapshot else None,
    )
    save_decision(result)
    return result
