from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from app.config import get_settings
from app.models import CustomerSeed, OrchestrationResult


SEED_CUSTOMERS: list[CustomerSeed] = [
    CustomerSeed(
        customer_id="c_charge_001",
        name="Aisha Al-Harbi",
        overdue_amount=250.0,
        days_overdue=12,
        pm_health=0.9,
        fails_7d=0,
        recent_success=0.85,
        scenario="clear_charge",
        notes="Strong repayment + healthy payment method → expect FULL CHARGE",
    ),
    CustomerSeed(
        customer_id="c_skip_001",
        name="Omar Nasser",
        overdue_amount=400.0,
        days_overdue=28,
        pm_health=0.25,
        fails_7d=4,
        recent_success=0.15,
        scenario="clear_skip",
        notes="Fragile customer / unhealthy PM / recent fails → expect SKIP",
    ),
    CustomerSeed(
        customer_id="c_partial_001",
        name="Layla Hassan",
        overdue_amount=500.0,
        days_overdue=18,
        pm_health=0.6,
        fails_7d=1,
        recent_success=0.55,
        scenario="partial",
        notes="Mid score → expect PARTIAL charge",
    ),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    settings = get_settings()
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                overdue_amount REAL NOT NULL,
                days_overdue INTEGER NOT NULL,
                pm_health REAL NOT NULL,
                fails_7d INTEGER NOT NULL,
                recent_success REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'SAR',
                scenario TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decisions (
                event_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        if count == 0:
            for customer in SEED_CUSTOMERS:
                conn.execute(
                    """
                    INSERT INTO customers (
                        customer_id, name, overdue_amount, days_overdue,
                        pm_health, fails_7d, recent_success, currency, scenario, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        customer.customer_id,
                        customer.name,
                        customer.overdue_amount,
                        customer.days_overdue,
                        customer.pm_health,
                        customer.fails_7d,
                        customer.recent_success,
                        customer.currency,
                        customer.scenario,
                        customer.notes,
                    ),
                )


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def list_customers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY scenario").fetchall()
        return [dict(row) for row in rows]


def get_customer(customer_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        return dict(row) if row else None


def event_exists(event_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return row is not None


def save_event(event_id: str, customer_id: str, event_type: str, amount: float, currency: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (event_id, customer_id, event_type, amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, customer_id, event_type, amount, currency, _utc_now()),
        )


def update_overdue(customer_id: str, new_overdue: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE customers SET overdue_amount = ? WHERE customer_id = ?",
            (max(0.0, new_overdue), customer_id),
        )


def save_decision(result: OrchestrationResult) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO decisions (event_id, customer_id, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                result.event_id,
                result.customer_id,
                result.model_dump_json(),
                result.created_at.isoformat(),
            ),
        )


def list_decisions(limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT payload_json FROM decisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]


def get_decision(event_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload_json FROM decisions WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None


def reset_seed_data() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM decisions")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM customers")
    init_db()
