"""Checkout, manual repayment, event Smart Autocharge, cron Normal Autocharge."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.config import get_settings
from app.db import (
    add_payment_history,
    apply_autocharge_to_overdue,
    create_checkout,
    get_customer,
    get_decision,
    get_installment,
    get_payment_history_by_event,
    list_customers,
    list_overdue_installments,
    mark_installment_paid,
    save_decision,
    save_event,
    touch_customer_activity,
)
from app.models import (
    AutopayResult,
    CheckoutResult,
    CronRunResult,
    DecisionAction,
    DecisionRequest,
    FeatureSet,
    ManualPayResult,
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


def _days_overdue(items: list[dict[str, Any]]) -> int:
    today = date.today()
    days = 0
    for item in items:
        due = date.fromisoformat(item["due_date"])
        days = max(days, (today - due).days)
    return max(0, days)


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
        touch_customer_activity(customer_id)
    return CheckoutResult(
        order=OrderOut.model_validate(order),
        first_payment_amount=first_amount,
        message=(
            f"Paid first installment {order['currency']} {first_amount:.2f}. "
            f"{plan - 1} upcoming payments created."
        ),
    )


async def _run_smart_autocharge(
    *,
    customer: dict[str, Any],
    amount_paid: float,
    currency: str,
    exclude_installment_id: Optional[str],
    parent_repay_event_id: str,
) -> Optional[OrchestrationResult]:
    """Event + AI Smart Autocharge — only against OVERDUE installments."""
    customer_id = customer["customer_id"]
    overdue_items = list_overdue_installments(
        customer_id,
        exclude_id=exclude_installment_id,
        statuses=("overdue",),
    )
    overdue_before = round(sum(float(i["amount"]) for i in overdue_items), 2)
    if overdue_before <= 0:
        return None

    event_id = f"evt_smart_{uuid.uuid4().hex[:16]}"
    days_overdue = _days_overdue(overdue_items)

    features = FeatureSet(
        days_overdue=days_overdue,
        pm_health=float(customer["pm_health"]),
        fails_7d=int(customer["fails_7d"]),
        recent_success=float(customer["recent_success"]),
        repayment_strength=_repayment_strength(amount_paid, overdue_before),
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

    save_event(
        event_id=event_id,
        customer_id=customer_id,
        event_type="smart_autocharge.decision",
        amount=decision.amount if decision.action != DecisionAction.SKIP else 0.0,
        currency=currency,
    )

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
            exclude_id=exclude_installment_id,
            statuses=("overdue",),
        )
        remaining = list_overdue_installments(
            customer_id,
            exclude_id=exclude_installment_id,
            statuses=("overdue",),
        )
        overdue_after = round(sum(float(i["amount"]) for i in remaining), 2)
        charge_result = AutopayResult(
            status=charge_result.status,
            transaction_id=charge_result.transaction_id,
            message=charge_result.message,
            installments_settled=settled,
        )
        smart_status = "succeeded"
        smart_amount = decision.amount
    else:
        if decision.action == DecisionAction.SKIP:
            smart_status = "skipped_by_ai"
        elif charge_result.status == "failed":
            smart_status = "failed"
        else:
            smart_status = "skipped"
        smart_amount = 0.0
        charge_result = AutopayResult(
            status="skipped" if decision.action == DecisionAction.SKIP else charge_result.status,
            transaction_id=None,
            message=charge_result.message or "Smart Autocharge did not charge",
            installments_settled=[],
        )

    add_payment_history(
        customer_id=customer_id,
        event_id=event_id,
        payment_type="smart_autocharge",
        title="Smart autocharge",
        amount=smart_amount if smart_status == "succeeded" else overdue_before,
        currency=currency,
        status=smart_status,
        details={
            "description": (
                "Triggered by repayment event. AI decided whether Autopay should "
                "collect other OVERDUE installments only (never upcoming)."
            ),
            "trigger": "event",
            "parent_repay_event_id": parent_repay_event_id,
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
        scenario=customer.get("scenario", ""),
        amount_paid=amount_paid,
        paid_installment_id=exclude_installment_id,
        overdue_before=overdue_before,
        currency=currency,
        decision=decision,
        autopay=charge_result,
        overdue_after=overdue_after,
        created_at=datetime.now(timezone.utc),
        order_snapshot=None,
        trigger="event",
        scheduled_for=None,
    )
    save_decision(result)
    return result


async def pay_installment(installment_id: str, customer_id: str) -> ManualPayResult:
    """Customer pays their installment, then event-based Smart Autocharge may run on OVERDUE only."""
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

    paid = mark_installment_paid(installment_id)
    amount_paid = float(paid["amount"])
    currency = paid["currency"]
    product_name = paid.get("product_name", "")
    merchant = paid.get("merchant", "")

    repay_event_id = f"evt_repay_{uuid.uuid4().hex[:16]}"
    save_event(
        event_id=repay_event_id,
        customer_id=customer_id,
        event_type="repayment.succeeded",
        amount=amount_paid,
        currency=currency,
    )
    touch_customer_activity(customer_id)

    add_payment_history(
        customer_id=customer_id,
        event_id=repay_event_id,
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
            "description": "Customer paid this installment.",
        },
    )

    # Smart Autocharge: event + AI, overdue only (never upcoming)
    await _run_smart_autocharge(
        customer=customer,
        amount_paid=amount_paid,
        currency=currency,
        exclude_installment_id=installment_id,
        parent_repay_event_id=repay_event_id,
    )

    return ManualPayResult(
        event_id=repay_event_id,
        customer_id=customer_id,
        installment_id=installment_id,
        amount_paid=amount_paid,
        currency=currency,
        product_name=product_name,
        message=f"Payment successful · {currency} {amount_paid:.2f}",
        created_at=datetime.now(timezone.utc),
    )


async def run_collections_cron(
    *,
    customer_id: Optional[str] = None,
    scheduled_for: str = "09:00",
) -> CronRunResult:
    """
    Normal autocharge — cron / schedule based.
    Charges OVERDUE installments only. No AI. Never charges upcoming.
    """
    targets = [get_customer(customer_id)] if customer_id else list_customers()
    targets = [c for c in targets if c]

    results: list[OrchestrationResult] = []
    scanned = 0
    charged = 0
    skipped = 0

    for customer in targets:
        cid = customer["customer_id"]
        overdue_items = list_overdue_installments(cid, statuses=("overdue",))
        scanned += 1
        if not overdue_items:
            skipped += 1
            continue

        overdue_before = round(sum(float(i["amount"]) for i in overdue_items), 2)
        currency = customer.get("currency", "SAR")
        event_id = f"evt_cron_{uuid.uuid4().hex[:16]}"

        save_event(
            event_id=event_id,
            customer_id=cid,
            event_type="collections.cron",
            amount=overdue_before,
            currency=currency,
        )

        # Normal autocharge = full overdue, no AI gate
        charge_result = autopay.execute_charge(
            customer=customer,
            amount=overdue_before,
            action=DecisionAction.CHARGE,
            idempotency_key=event_id,
        )

        overdue_after = overdue_before
        if charge_result.status == "success":
            settled = apply_autocharge_to_overdue(
                cid,
                overdue_before,
                statuses=("overdue",),
            )
            remaining = list_overdue_installments(cid, statuses=("overdue",))
            overdue_after = round(sum(float(i["amount"]) for i in remaining), 2)
            charge_result = AutopayResult(
                status=charge_result.status,
                transaction_id=charge_result.transaction_id,
                message=charge_result.message,
                installments_settled=settled,
            )
            charged += 1
            status = "succeeded"
            amount = overdue_before - overdue_after
        else:
            skipped += 1
            status = "failed"
            amount = overdue_before

        add_payment_history(
            customer_id=cid,
            event_id=event_id,
            payment_type="normal_autocharge",
            title=f"Normal autocharge · {scheduled_for}",
            amount=amount if status == "succeeded" else overdue_before,
            currency=currency,
            status=status,
            details={
                "description": (
                    "Scheduled cron autocharge. Collects OVERDUE installments only — "
                    "never upcoming. No AI decision."
                ),
                "trigger": "cron",
                "scheduled_for": scheduled_for,
                "overdue_before": overdue_before,
                "overdue_after": overdue_after,
                "autopay_status": charge_result.status,
                "autopay_message": charge_result.message,
                "installments_settled": charge_result.installments_settled,
            },
        )

        # Store a synthetic decision for detail view (always charge)
        from app.models import DecisionResponse

        decision = DecisionResponse(
            action=DecisionAction.CHARGE,
            amount=overdue_before,
            score=1.0,
            reason="Normal cron autocharge — no AI; charge all overdue.",
            baseline_action=DecisionAction.CHARGE,
            source="cron",
        )
        result = OrchestrationResult(
            event_id=event_id,
            customer_id=cid,
            customer_name=customer["name"],
            scenario=customer.get("scenario", ""),
            amount_paid=0.0,
            paid_installment_id=None,
            overdue_before=overdue_before,
            currency=currency,
            decision=decision,
            autopay=charge_result,
            overdue_after=overdue_after,
            created_at=datetime.now(timezone.utc),
            order_snapshot=None,
            trigger="cron",
            scheduled_for=scheduled_for,
        )
        save_decision(result)
        results.append(result)

    return CronRunResult(
        scheduled_for=scheduled_for,
        customers_scanned=scanned,
        customers_charged=charged,
        customers_skipped=skipped,
        results=results,
        message=(
            f"Normal autocharge cron {scheduled_for}: scanned {scanned}, "
            f"charged {charged}, skipped {skipped}"
        ),
    )


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
