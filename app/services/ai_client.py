"""AI decision client — remote teammate service with local mock fallback."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.models import DecisionAction, DecisionRequest, DecisionResponse


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def mock_decide(payload: DecisionRequest) -> DecisionResponse:
    """Option A-lite scorecard so Backend+Frontend never block on AI."""
    features = payload.features

    if payload.overdue_amount <= 0:
        return DecisionResponse(
            action=DecisionAction.SKIP,
            amount=0,
            score=0.0,
            reason="No eligible overdue balance — skip",
            baseline_action=DecisionAction.CHARGE,
            source="mock",
        )

    if features.pm_health < 0.35 or features.fails_7d >= 3:
        return DecisionResponse(
            action=DecisionAction.SKIP,
            amount=0,
            score=round(_clamp(0.25 - 0.05 * features.fails_7d), 4),
            reason=(
                f"Hard gate: unhealthy PM ({features.pm_health:.2f}) "
                f"or recent fails ({features.fails_7d}) — skip"
            ),
            baseline_action=DecisionAction.CHARGE,
            source="mock",
        )

    score = _clamp(
        0.35 * features.repayment_strength
        + 0.25 * features.pm_health
        + 0.20 * features.recent_success
        + 0.10 * _clamp(features.days_overdue / 30)
        - 0.10 * _clamp(features.fails_7d / 5)
    )

    if score >= 0.65:
        amount = round(payload.overdue_amount, 2)
        action = DecisionAction.CHARGE
        reason = (
            f"Score {score:.2f}: strong repayment + healthy PM — "
            f"full charge {payload.currency} {amount:.2f}"
        )
    elif score >= 0.45:
        amount = round(min(payload.overdue_amount, payload.amount_paid * 0.5), 2)
        amount = max(amount, min(50.0, payload.overdue_amount))
        action = DecisionAction.PARTIAL
        reason = (
            f"Score {score:.2f}: mid confidence — "
            f"partial charge {payload.currency} {amount:.2f}"
        )
    else:
        amount = 0.0
        action = DecisionAction.SKIP
        reason = f"Score {score:.2f}: low success probability — skip/defer"

    return DecisionResponse(
        action=action,
        amount=amount,
        score=round(score, 4),
        reason=reason,
        baseline_action=DecisionAction.CHARGE,
        source="mock",
    )


async def decide(payload: DecisionRequest, settings: Settings) -> DecisionResponse:
    if settings.use_mock_ai:
        return mock_decide(payload)

    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                settings.ai_decision_url,
                json=payload.model_dump(mode="json"),
            )
            response.raise_for_status()
            data = response.json()
            decision = DecisionResponse.model_validate(data)
            decision.source = "remote"
            return decision
    except Exception as exc:  # noqa: BLE001 — demo resilience
        fallback = mock_decide(payload)
        fallback.reason = f"[AI unreachable: {exc}] {fallback.reason}"
        fallback.source = "mock-fallback"
        return fallback
