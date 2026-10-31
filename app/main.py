from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db, list_customers, list_decisions, reset_seed_data
from app.models import HealthResponse, OrchestrationResult, RepayRequest
from app.services.orchestrator import process_repayment


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Collectus Smart Autocharge — RPM + Autopay orchestration (hackathon prototype)",
    version="0.1.0",
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


@app.post("/api/rpm/repay", response_model=OrchestrationResult)
async def repay(body: RepayRequest) -> OrchestrationResult:
    return await process_repayment(body)


@app.get("/api/decisions")
def decisions(limit: int = 20) -> dict:
    return {"decisions": list_decisions(limit=limit)}


@app.post("/api/admin/reset")
def reset() -> dict:
    reset_seed_data()
    return {"status": "reset", "customers": list_customers()}
