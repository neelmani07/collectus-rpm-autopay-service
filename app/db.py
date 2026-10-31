from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from app.config import get_settings
from app.models import InstallmentStatus, OrchestrationResult

CUSTOMERS = [
    {
        "customer_id": "c_charge_001",
        "name": "Aisha Al-Harbi",
        "pm_health": 0.9,
        "fails_7d": 0,
        "recent_success": 0.85,
        "currency": "SAR",
        "scenario": "clear_charge",
        "notes": "Healthy payment method — Smart Autocharge should recover overdue",
    },
    {
        "customer_id": "c_skip_001",
        "name": "Omar Nasser",
        "pm_health": 0.25,
        "fails_7d": 4,
        "recent_success": 0.15,
        "currency": "SAR",
        "scenario": "clear_skip",
        "notes": "Fragile PM / recent fails — Smart Autocharge should SKIP",
    },
    {
        "customer_id": "c_partial_001",
        "name": "Layla Hassan",
        "pm_health": 0.6,
        "fails_7d": 1,
        "recent_success": 0.55,
        "currency": "SAR",
        "scenario": "partial",
        "notes": "Mid confidence — Smart Autocharge may PARTIAL charge",
    },
]

PRODUCTS = [
    {
        "product_id": "p_airpods",
        "name": "AirPods Pro 2",
        "merchant": "Extra",
        "category": "Electronics",
        "price": 999.0,
        "badge": "Popular",
        "accent": "#9600F1",
        "description": "Active noise cancellation. Split into easy payments.",
    },
    {
        "product_id": "p_nike",
        "name": "Nike Pegasus 41",
        "merchant": "Namshi",
        "category": "Fashion",
        "price": 520.0,
        "badge": "New",
        "accent": "#5300BA",
        "description": "Everyday running shoes — pay your way with Tamara.",
    },
    {
        "product_id": "p_dyson",
        "name": "Dyson V8 Absolute",
        "merchant": "Jarir",
        "category": "Home",
        "price": 1899.0,
        "badge": "Pay in 4",
        "accent": "#7A1CFF",
        "description": "Home upgrade without paying all at once.",
    },
    {
        "product_id": "p_ipad",
        "name": "iPad 10th Gen",
        "merchant": "Apple Store",
        "category": "Electronics",
        "price": 1499.0,
        "badge": "Trending",
        "accent": "#CEA4FF",
        "description": "Create more. Pay in installments — Sharia compliant.",
    },
    {
        "product_id": "p_perfume",
        "name": "Lattafa Yara",
        "merchant": "Faces",
        "category": "Beauty",
        "price": 180.0,
        "badge": "",
        "accent": "#E5CFFF",
        "description": "Beauty finds — split the cost, keep the glow.",
    },
    {
        "product_id": "p_ps5",
        "name": "PlayStation 5 Slim",
        "merchant": "Noon",
        "category": "Gaming",
        "price": 2099.0,
        "badge": "Hot",
        "accent": "#9600F1",
        "description": "Own your dream setup. Pay over time with no late fees.",
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return date.today()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                pm_health REAL NOT NULL,
                fails_7d INTEGER NOT NULL,
                recent_success REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'SAR',
                scenario TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                last_activity_at TEXT
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                merchant TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'SAR',
                badge TEXT NOT NULL DEFAULT '',
                accent TEXT NOT NULL DEFAULT '#9600F1',
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                total_amount REAL NOT NULL,
                currency TEXT NOT NULL,
                plan INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS installments (
                installment_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL,
                paid_at TEXT
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

            CREATE TABLE IF NOT EXISTS payment_history (
                history_id TEXT PRIMARY KEY,
                event_id TEXT,
                customer_id TEXT NOT NULL,
                payment_type TEXT NOT NULL,
                title TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                product_name TEXT NOT NULL DEFAULT '',
                merchant TEXT NOT NULL DEFAULT '',
                installment_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        # Ensure column exists for upgrades
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(customers)").fetchall()}
        if "last_activity_at" not in cols:
            conn.execute("ALTER TABLE customers ADD COLUMN last_activity_at TEXT")

        if not version or version["value"] != "4":
            conn.execute("DELETE FROM payment_history")
            conn.execute("DELETE FROM decisions")
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM installments")
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM customers")
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '4')"
            )
            _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    for customer in CUSTOMERS:
        conn.execute(
            """
            INSERT INTO customers (
                customer_id, name, pm_health, fails_7d, recent_success,
                currency, scenario, notes, last_activity_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                customer["customer_id"],
                customer["name"],
                customer["pm_health"],
                customer["fails_7d"],
                customer["recent_success"],
                customer["currency"],
                customer["scenario"],
                customer["notes"],
            ),
        )

    for product in PRODUCTS:
        conn.execute(
            """
            INSERT INTO products (
                product_id, name, merchant, category, price, currency,
                badge, accent, description
            ) VALUES (?, ?, ?, ?, ?, 'SAR', ?, ?, ?)
            """,
            (
                product["product_id"],
                product["name"],
                product["merchant"],
                product["category"],
                product["price"],
                product["badge"],
                product["accent"],
                product["description"],
            ),
        )

    # Seed overdue installments so Smart Autocharge has something to decide on
    _seed_overdue_order(
        conn,
        customer_id="c_charge_001",
        product_id="p_perfume",
        plan=4,
        overdue_sequences=(2, 3),
    )
    _seed_overdue_order(
        conn,
        customer_id="c_skip_001",
        product_id="p_nike",
        plan=4,
        overdue_sequences=(2, 3),
    )
    _seed_overdue_order(
        conn,
        customer_id="c_partial_001",
        product_id="p_airpods",
        plan=4,
        overdue_sequences=(2, 3),
    )


def _split_amount(total: float, plan: int) -> list[float]:
    cents = int(round(total * 100))
    base, rem = divmod(cents, plan)
    parts = [base / 100.0] * plan
    parts[-1] = (base + rem) / 100.0
    return parts


def _seed_overdue_order(
    conn: sqlite3.Connection,
    *,
    customer_id: str,
    product_id: str,
    plan: int,
    overdue_sequences: tuple[int, ...],
) -> None:
    product = conn.execute(
        "SELECT * FROM products WHERE product_id = ?", (product_id,)
    ).fetchone()
    if not product:
        return
    order_id = f"ord_seed_{customer_id}_{product_id}"
    amounts = _split_amount(float(product["price"]), plan)
    created = (_today() - timedelta(days=70)).isoformat()
    conn.execute(
        """
        INSERT INTO orders (order_id, customer_id, product_id, total_amount, currency, plan, created_at)
        VALUES (?, ?, ?, ?, 'SAR', ?, ?)
        """,
        (order_id, customer_id, product_id, float(product["price"]), plan, created),
    )
    for idx, amount in enumerate(amounts, start=1):
        due = _today() - timedelta(days=40 - (idx - 1) * 14)
        if idx == 1:
            status = InstallmentStatus.PAID.value
            paid_at = (due - timedelta(days=1)).isoformat()
        elif idx in overdue_sequences:
            status = InstallmentStatus.OVERDUE.value
            paid_at = None
            due = _today() - timedelta(days=12 + idx)
        else:
            status = InstallmentStatus.UPCOMING.value
            paid_at = None
            due = _today() + timedelta(days=14 * idx)
        conn.execute(
            """
            INSERT INTO installments (
                installment_id, order_id, customer_id, sequence, amount,
                currency, due_date, status, paid_at
            ) VALUES (?, ?, ?, ?, ?, 'SAR', ?, ?, ?)
            """,
            (
                f"ins_{order_id}_{idx}",
                order_id,
                customer_id,
                idx,
                amount,
                due.isoformat(),
                status,
                paid_at,
            ),
        )


def reset_seed_data() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM payment_history")
        conn.execute("DELETE FROM decisions")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM installments")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM customers")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '4')"
        )
        _seed(conn)


def touch_customer_activity(customer_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE customers SET last_activity_at = ? WHERE customer_id = ?",
            (_utc_now(), customer_id),
        )


def add_payment_history(
    *,
    customer_id: str,
    payment_type: str,
    title: str,
    amount: float,
    currency: str,
    status: str,
    event_id: Optional[str] = None,
    product_name: str = "",
    merchant: str = "",
    installment_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    history_id = f"pay_{uuid.uuid4().hex[:12]}"
    created_at = _utc_now()
    payload = details or {}
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO payment_history (
                history_id, event_id, customer_id, payment_type, title, amount,
                currency, status, product_name, merchant, installment_id,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history_id,
                event_id,
                customer_id,
                payment_type,
                title,
                amount,
                currency,
                status,
                product_name,
                merchant,
                installment_id,
                json.dumps(payload),
                created_at,
            ),
        )
    return get_payment_history_item(history_id)  # type: ignore[return-value]


def get_payment_history_item(history_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM payment_history WHERE history_id = ?",
            (history_id,),
        ).fetchone()
        return _history_row(row) if row else None


def list_payment_history(customer_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM payment_history
            WHERE customer_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (customer_id, limit),
        ).fetchall()
        return [_history_row(row) for row in rows]


def get_payment_history_by_event(event_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM payment_history
            WHERE event_id = ?
            ORDER BY created_at ASC
            """,
            (event_id,),
        ).fetchall()
        return [_history_row(row) for row in rows]


def _history_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "history_id": row["history_id"],
        "event_id": row["event_id"],
        "customer_id": row["customer_id"],
        "payment_type": row["payment_type"],
        "title": row["title"],
        "amount": row["amount"],
        "currency": row["currency"],
        "status": row["status"],
        "product_name": row["product_name"],
        "merchant": row["merchant"],
        "installment_id": row["installment_id"],
        "details": json.loads(row["details_json"] or "{}"),
        "created_at": row["created_at"],
    }


def list_customers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY scenario").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            overdue = _overdue_summary(conn, item["customer_id"])
            item["overdue_amount"] = overdue["amount"]
            item["days_overdue"] = overdue["days"]
            result.append(item)
        return result


def get_customer(customer_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        overdue = _overdue_summary(conn, customer_id)
        item["overdue_amount"] = overdue["amount"]
        item["days_overdue"] = overdue["days"]
        return item


def list_products() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY price DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_product(product_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return dict(row) if row else None


def _overdue_summary(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT amount, due_date FROM installments
        WHERE customer_id = ? AND status IN ('due', 'overdue')
        """,
        (customer_id,),
    ).fetchall()
    amount = sum(float(r["amount"]) for r in rows)
    days = 0
    today = _today()
    for row in rows:
        due = date.fromisoformat(row["due_date"])
        days = max(days, (today - due).days)
    return {"amount": round(amount, 2), "days": max(0, days)}


def refresh_installment_statuses(customer_id: Optional[str] = None) -> None:
    today = _today().isoformat()
    with get_connection() as conn:
        if customer_id:
            conn.execute(
                """
                UPDATE installments
                SET status = 'overdue'
                WHERE customer_id = ?
                  AND status IN ('upcoming', 'due')
                  AND due_date < ?
                  AND paid_at IS NULL
                """,
                (customer_id, today),
            )
            conn.execute(
                """
                UPDATE installments
                SET status = 'due'
                WHERE customer_id = ?
                  AND status = 'upcoming'
                  AND due_date <= ?
                  AND paid_at IS NULL
                """,
                (customer_id, today),
            )
        else:
            conn.execute(
                """
                UPDATE installments
                SET status = 'overdue'
                WHERE status IN ('upcoming', 'due')
                  AND due_date < date('now')
                  AND paid_at IS NULL
                """
            )
            conn.execute(
                """
                UPDATE installments
                SET status = 'due'
                WHERE status = 'upcoming'
                  AND due_date <= date('now')
                  AND paid_at IS NULL
                """
            )


def create_checkout(customer_id: str, product_id: str, plan: int) -> dict[str, Any]:
    customer = get_customer(customer_id)
    product = get_product(product_id)
    if not customer:
        raise ValueError("Unknown customer")
    if not product:
        raise ValueError("Unknown product")

    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    amounts = _split_amount(float(product["price"]), plan)
    created_at = _utc_now()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO orders (
                order_id, customer_id, product_id, total_amount, currency, plan, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                customer_id,
                product_id,
                float(product["price"]),
                product["currency"],
                plan,
                created_at,
            ),
        )
        for idx, amount in enumerate(amounts, start=1):
            if idx == 1:
                status = InstallmentStatus.PAID.value
                due = _today()
                paid_at = created_at
            else:
                status = InstallmentStatus.UPCOMING.value
                due = _today() + timedelta(days=30 * (idx - 1))
                paid_at = None
            conn.execute(
                """
                INSERT INTO installments (
                    installment_id, order_id, customer_id, sequence, amount,
                    currency, due_date, status, paid_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ins_{order_id}_{idx}",
                    order_id,
                    customer_id,
                    idx,
                    amount,
                    product["currency"],
                    due.isoformat(),
                    status,
                    paid_at,
                ),
            )

    return get_order(order_id)  # type: ignore[return-value]


def _installment_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    order = conn.execute(
        "SELECT product_id, plan FROM orders WHERE order_id = ?", (row["order_id"],)
    ).fetchone()
    product = conn.execute(
        "SELECT name, merchant FROM products WHERE product_id = ?",
        (order["product_id"],),
    ).fetchone()
    return {
        "installment_id": row["installment_id"],
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "product_name": product["name"] if product else "",
        "merchant": product["merchant"] if product else "",
        "sequence": row["sequence"],
        "plan_size": order["plan"] if order else 0,
        "amount": row["amount"],
        "currency": row["currency"],
        "due_date": row["due_date"],
        "status": row["status"],
        "paid_at": row["paid_at"],
    }


def get_order(order_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if not order:
            return None
        product = conn.execute(
            "SELECT name, merchant, accent FROM products WHERE product_id = ?",
            (order["product_id"],),
        ).fetchone()
        installment_rows = conn.execute(
            """
            SELECT * FROM installments
            WHERE order_id = ?
            ORDER BY sequence
            """,
            (order_id,),
        ).fetchall()
        return {
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "product_id": order["product_id"],
            "product_name": product["name"] if product else "",
            "merchant": product["merchant"] if product else "",
            "total_amount": order["total_amount"],
            "currency": order["currency"],
            "plan": order["plan"],
            "created_at": order["created_at"],
            "accent": product["accent"] if product else "#9600F1",
            "installments": [
                _installment_row_to_dict(conn, row) for row in installment_rows
            ],
        }


def list_orders(customer_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT order_id FROM orders
            WHERE customer_id = ?
            ORDER BY created_at DESC
            """,
            (customer_id,),
        ).fetchall()
    return [get_order(row["order_id"]) for row in rows]  # type: ignore[misc]


def list_installments(customer_id: str) -> list[dict[str, Any]]:
    refresh_installment_statuses(customer_id)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM installments
            WHERE customer_id = ?
            ORDER BY due_date ASC, sequence ASC
            """,
            (customer_id,),
        ).fetchall()
        return [_installment_row_to_dict(conn, row) for row in rows]


def get_installment(installment_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM installments WHERE installment_id = ?",
            (installment_id,),
        ).fetchone()
        if not row:
            return None
        return _installment_row_to_dict(conn, row)


def mark_installment_paid(installment_id: str) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM installments WHERE installment_id = ?",
            (installment_id,),
        ).fetchone()
        if not row:
            raise ValueError("Installment not found")
        if row["status"] == InstallmentStatus.PAID.value:
            raise ValueError("Installment already paid")
        conn.execute(
            """
            UPDATE installments
            SET status = 'paid', paid_at = ?
            WHERE installment_id = ?
            """,
            (_utc_now(), installment_id),
        )
    return get_installment(installment_id)  # type: ignore[return-value]


def list_overdue_installments(customer_id: str, exclude_id: Optional[str] = None) -> list[dict[str, Any]]:
    refresh_installment_statuses(customer_id)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM installments
            WHERE customer_id = ?
              AND status IN ('due', 'overdue')
            ORDER BY due_date ASC
            """,
            (customer_id,),
        ).fetchall()
        items = [_installment_row_to_dict(conn, row) for row in rows]
        if exclude_id:
            items = [i for i in items if i["installment_id"] != exclude_id]
        return items


def apply_autocharge_to_overdue(
    customer_id: str,
    charge_amount: float,
    exclude_id: Optional[str] = None,
) -> list[str]:
    """Apply successful autocharge across oldest overdue installments."""
    remaining = round(charge_amount, 2)
    settled: list[str] = []
    overdue = list_overdue_installments(customer_id, exclude_id=exclude_id)
    for item in overdue:
        if remaining <= 0:
            break
        amount = float(item["amount"])
        if remaining + 1e-6 >= amount:
            mark_installment_paid(item["installment_id"])
            settled.append(item["installment_id"])
            remaining = round(remaining - amount, 2)
        else:
            # Partial: reduce installment amount and leave as overdue/due
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE installments
                    SET amount = ?
                    WHERE installment_id = ?
                    """,
                    (round(amount - remaining, 2), item["installment_id"]),
                )
            settled.append(item["installment_id"])
            remaining = 0.0
    return settled


def event_exists(event_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None


def save_event(
    event_id: str, customer_id: str, event_type: str, amount: float, currency: str
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (event_id, customer_id, event_type, amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, customer_id, event_type, amount, currency, _utc_now()),
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
