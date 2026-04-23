"""
database.py
------------
Handles all local SQLite database operations.
Stores usage counts and costs so the dashboard loads instantly.
"""

import sqlite3
import json
from datetime import datetime
from typing import Optional


DB_PATH = "ghl_dashboard.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn


def init_db():
    """Create all tables if they don't exist yet."""
    conn = get_connection()
    c = conn.cursor()

    # Stores message counts per service per day
    c.execute("""
        CREATE TABLE IF NOT EXISTS usage_daily (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,          -- YYYY-MM-DD
            service       TEXT NOT NULL,          -- e.g. 'whatsapp_marketing'
            message_count INTEGER DEFAULT 0,
            cost          REAL    DEFAULT 0.0,
            updated_at    TEXT    DEFAULT (datetime('now')),
            UNIQUE(date, service)
        )
    """)

    # Stores pre-computed monthly totals per service
    c.execute("""
        CREATE TABLE IF NOT EXISTS usage_monthly (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            month         TEXT NOT NULL,          -- YYYY-MM
            service       TEXT NOT NULL,          -- e.g. 'whatsapp_marketing'
            message_count INTEGER DEFAULT 0,
            cost          REAL    DEFAULT 0.0,
            updated_at    TEXT    DEFAULT (datetime('now')),
            UNIQUE(month, service)
        )
    """)

    # Stores top-up / purchase transactions
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id           TEXT PRIMARY KEY,        -- GHL transaction id
            amount       REAL,
            currency     TEXT,
            status       TEXT,
            created_at   TEXT,
            raw_json     TEXT                     -- full GHL response
        )
    """)

    # Tracks which conversations we've already processed
    c.execute("""
        CREATE TABLE IF NOT EXISTS processed_conversations (
            conversation_id  TEXT PRIMARY KEY,
            last_processed   TEXT,               -- ISO datetime
            message_count    INTEGER DEFAULT 0
        )
    """)

    # Stores last fetch timestamp so we only pull new data
    c.execute("""
        CREATE TABLE IF NOT EXISTS fetch_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized.")


# ----------------------------------------------------------
# USAGE: write daily counts
# ----------------------------------------------------------
def upsert_daily_usage(date: str, service: str,
                       message_count: int, cost: float):
    """Insert or update usage for a service on a given day."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO usage_daily (date, service, message_count, cost)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, service) DO UPDATE SET
            message_count = excluded.message_count,
            cost          = excluded.cost,
            updated_at    = datetime('now')
    """, (date, service, message_count, cost))
    conn.commit()
    conn.close()


def rebuild_monthly_from_daily():
    """Recompute monthly totals from daily data."""
    conn = get_connection()
    conn.execute("DELETE FROM usage_monthly")
    conn.execute("""
        INSERT INTO usage_monthly (month, service, message_count, cost)
        SELECT
            substr(date, 1, 7) AS month,
            service,
            SUM(message_count),
            SUM(cost)
        FROM usage_daily
        GROUP BY month, service
    """)
    conn.commit()
    conn.close()


# ----------------------------------------------------------
# USAGE: read monthly totals for dashboard
# ----------------------------------------------------------
def get_monthly_summary(month: str) -> list:
    """
    Returns list of {service, message_count, cost} for a given month.
    month format: 'YYYY-MM'
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT service, message_count, cost
        FROM usage_monthly
        WHERE month = ?
        ORDER BY cost DESC
    """, (month,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_months() -> list:
    """Returns list of months that have data, most recent first."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT month
        FROM usage_monthly
        ORDER BY month DESC
    """).fetchall()
    conn.close()
    return [r["month"] for r in rows]


def get_total_for_month(month: str) -> float:
    """Returns total spend for a given month."""
    conn = get_connection()
    row = conn.execute("""
        SELECT COALESCE(SUM(cost), 0) as total
        FROM usage_monthly
        WHERE month = ?
    """, (month,)).fetchone()
    conn.close()
    return row["total"] if row else 0.0


# ----------------------------------------------------------
# TRANSACTIONS
# ----------------------------------------------------------
def upsert_transaction(txn: dict):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO transactions
            (id, amount, currency, status, created_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        txn.get("_id") or txn.get("id"),
        txn.get("amount", 0),
        txn.get("currency", "USD"),
        txn.get("status", ""),
        txn.get("createdAt", ""),
        json.dumps(txn),
    ))
    conn.commit()
    conn.close()


# ----------------------------------------------------------
# FETCH STATE: remember last run time
# ----------------------------------------------------------
def get_last_fetch(key: str = "conversations") -> Optional[str]:
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM fetch_state WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def set_last_fetch(key: str = "conversations", value: str = None):
    if value is None:
        value = datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO fetch_state (key, value)
        VALUES (?, ?)
    """, (key, value))
    conn.commit()
    conn.close()


# ----------------------------------------------------------
# PROCESSED CONVERSATIONS
# ----------------------------------------------------------
def mark_conversation_processed(conversation_id: str, msg_count: int):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO processed_conversations
            (conversation_id, last_processed, message_count)
        VALUES (?, datetime('now'), ?)
    """, (conversation_id, msg_count))
    conn.commit()
    conn.close()


def get_processed_conversation_ids() -> set:
    conn = get_connection()
    rows = conn.execute(
        "SELECT conversation_id FROM processed_conversations"
    ).fetchall()
    conn.close()
    return {r["conversation_id"] for r in rows}


# ----------------------------------------------------------
# Quick test
# ----------------------------------------------------------
if __name__ == "__main__":
    init_db()

    # Insert some dummy data to verify it works
    upsert_daily_usage("2026-04-23", "whatsapp_marketing", 10, 0.769)
    upsert_daily_usage("2026-04-23", "whatsapp_utility",    2, 0.024)
    upsert_daily_usage("2026-04-22", "whatsapp_marketing",  5, 0.385)
    upsert_daily_usage("2026-04-22", "email",              50, 0.034)

    rebuild_monthly_from_daily()

    months = get_available_months()
    print(f"\nAvailable months: {months}")

    summary = get_monthly_summary("2026-04")
    print(f"\nApril 2026 breakdown:")
    for row in summary:
        print(f"  {row['service']:30s} "
              f"{row['message_count']:>6} msgs   "
              f"${row['cost']:.4f}")

    total = get_total_for_month("2026-04")
    print(f"\n  Total: ${total:.4f}")
    print("\n✅ Database working correctly.")
