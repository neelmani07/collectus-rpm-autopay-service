"""Checkout + installment repayment → RPM event → AI → Autopay."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException

from app.config import get_settings
from app.db import (
    add_payment_history,
    apply_autocharge_to_overdue,
    create_checkout,
    get_customer,
    get_decision,
    get_installment,
    get_order,
    get_payment_history_by_event,
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
    PaymentDetailResponse,
    PaymentHistoryItem,
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
    if first:
        add_payment_history(
            customer_id=customer_id,
            payment_type="manual_repayment",
            title=f"First payment · {order['product_name']}",
            amount=first_amount,
            currency=order["currency"],
            status="succeeded",
            product_name=order["product_name"],
            merchant=order["merchant"],
            installment_id=first["installment_id"],
            details={"source": "checkout", "sequence": 1, "plan": plan},
        )
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
    product_name = paid.get("product_name", "")
    merchant = paid.get("merchant", "")

    # Customer-facing history: three payment kinds
    add_payment_history(
        customer_id=customer_id,
        event_id=event_id,
        payment_type="manual_repayment",
        title=f"Manual repayment · {product_name}",
        amount=amount_paid,
        currency=currency,
        status="succeeded",
        product_name=product_name,
        merchant=merchant,
        installment_id=installment_id,
        details={
            "sequence": paid.get("sequence"),
            "plan_size": paid.get("plan_size"),
            "description": "Customer chose to pay this installment.",
        },
    )

    # Normal (cron-style) autocharge baseline — what would run without AI
    add_payment_history(
        customer_id=customer_id,
        event_id=event_id,
        payment_type="normal_autocharge",
        title="Normal autocharge (cron-style)",
        amount=overdue_before,
        currency=currency,
        status="baseline_not_executed" if overdue_before > 0 else "nothing_due",
        product_name=product_name,
        merchant=merchant,
        details={
            "description": (
                "Blind schedule-based autocharge would attempt the full remaining overdue "
                "with no AI check."
                if overdue_before > 0
                else "No other overdue — normal autocharge would also do nothing."
            ),
            "would_charge_amount": overdue_before,
        },
    )

    smart_status = "skipped"
    smart_amount = 0.0
    if charge_result.status == "success" and decision.amount > 0:
        smart_status = "succeeded"
        smart_amount = decision.amount
    elif decision.action == DecisionAction.SKIP:
        smart_status = "skipped_by_ai"
    elif charge_result.status == "failed":
        smart_status = "failed"
    elif overdue_before <= 0:
        smart_status = "nothing_due"

    add_payment_history(
        customer_id=customer_id,
        event_id=event_id,
        payment_type="smart_autocharge",
        title="Smart autocharge",
        amount=smart_amount if smart_status == "succeeded" else overdue_before,
        currency=currency,
        status=smart_status,
        product_name=product_name,
        merchant=merchant,
        details={
            "description": "AI decided whether Autopay should collect other overdue after your repayment.",
            "ai_action": decision.action.value,
            "ai_score": decision.score,
            "ai_reason": decision.reason,
            "autopay_status": charge_result.status,
            "autopay_message": charge_result.message,
            "installments_settled": charge_result.installments_settled,
            "overdue_before": overdue_before,
            "overdue_after": overdue_after,
        },
    )

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


def payment_detail(event_id: str) -> PaymentDetailResponse:
    history = get_payment_history_by_event(event_id)
    if not history:
        raise HTTPException(status_code=404, detail="Payment event not found")
    orchestration_raw = get_decision(event_id)
    orchestration = (
        OrchestrationResult.model_validate(orchestration_raw) if orchestration_raw else None
    )
    return PaymentDetailResponse(
        event_id=event_id,
        history=[PaymentHistoryItem.model_validate(item) for item in history],
        orchestration=orchestration,
    )
