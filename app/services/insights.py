"""Insights: the AI service's 30-day backtest, cached for the demo app."""

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import HTTPException

from app.config import Settings

_cache: dict[str, Any] = {}


def _ai_url(settings: Settings, path: str) -> str:
    return settings.ai_decision_url.rsplit("/v1/", 1)[0] + path


def _backtest_url(settings: Settings) -> str:
    return _ai_url(settings, "/v1/backtest/month")


async def _ai_call(settings: Settings, method: str, path: str, body: Optional[dict] = None) -> Any:
    if settings.use_mock_ai:
        raise HTTPException(status_code=503, detail="This needs the AI decision service (USE_MOCK_AI=false)")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.request(method, _ai_url(settings, path), json=body)
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001 — surface a friendly message to the UI
        raise HTTPException(
            status_code=503, detail=f"AI service is waking up — try again in a minute ({exc})"
        ) from exc


async def model_info(settings: Settings) -> dict[str, Any]:
    """Coefficients and metrics of the live model (cached; the model only changes on deploy)."""
    if "model" not in _cache:
        _cache["model"] = await _ai_call(settings, "GET", "/v1/model")
    return _cache["model"]


async def playground_decide(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    """Ask the AI about any customer profile — for judges testing unfamiliar inputs."""
    return await _ai_call(settings, "POST", "/v1/decide", body)


async def month_backtest(settings: Settings, refresh: bool = False) -> dict[str, Any]:
    """2,000 synthetic customers, 30 days, mixed cards — same inputs every time, so cache it."""
    if not refresh and "month" in _cache:
        return _cache["month"]
    if settings.use_mock_ai:
        raise HTTPException(status_code=503, detail="Insights need the AI decision service (USE_MOCK_AI=false)")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(_backtest_url(settings), json={"n": 2000, "seed": 2026})
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — surface a friendly message to the UI
        raise HTTPException(
            status_code=503, detail=f"AI service is waking up — try again in a minute ({exc})"
        ) from exc
    data: Optional[dict[str, Any]] = response.json()
    _cache["month"] = data
    return data
