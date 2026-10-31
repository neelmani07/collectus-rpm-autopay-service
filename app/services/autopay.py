"""Autopay module — simulated charge executor (swap for real API later)."""

from __future__ import annotations

import uuid
from typing import Any

from app.models import AutopayResult, DecisionAction


def execute_charge(
    *,
    customer: dict[str, Any],
    amount: float,
    action: DecisionAction,
    idempotency_key: str,
) -> AutopayResult:
    if action == DecisionAction.SKIP or amount <= 0:
        return AutopayResult(
            status="skipped",
            transaction_id=None,
            message="No autocharge executed",
            installments_settled=[],
        )

    if float(customer["pm_health"]) < 0.2:
        return AutopayResult(
            status="failed",
            transaction_id=None,
            message="Simulated issuer decline — payment method unhealthy",
            installments_settled=[],
        )

    tx_id = f"tx_{idempotency_key[-8:]}_{uuid.uuid4().hex[:6]}"
    return AutopayResult(
        status="success",
        transaction_id=tx_id,
        message=f"Autocharged {customer.get('currency', 'SAR')} {amount:.2f} (simulated)",
        installments_settled=[],
    )
