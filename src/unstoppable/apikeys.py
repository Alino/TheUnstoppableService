from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timezone


PLAN_CONFIG = {
    "free": {"daily_limit": 100, "price_per_query_usd": 0.0, "default_credit_usd": 0.0},
    "builder": {
        "daily_limit": 2000,
        "price_per_query_usd": 0.002,
        "default_credit_usd": 10.0,
    },
    "pro": {
        "daily_limit": 10000,
        "price_per_query_usd": 0.001,
        "default_credit_usd": 50.0,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day() -> str:
    return date.today().isoformat()


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def init_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            key_hint TEXT NOT NULL,
            name TEXT NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            credit_usd REAL NOT NULL DEFAULT 0,
            total_spent_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL,
            day TEXT NOT NULL,
            queries INTEGER NOT NULL DEFAULT 0,
            spend_usd REAL NOT NULL DEFAULT 0,
            UNIQUE(key_hash, day)
        )
        """
    )
    conn.commit()


def create_key(conn, name: str, plan: str) -> dict:
    if plan not in PLAN_CONFIG:
        raise ValueError(f"unsupported plan: {plan}")

    raw = f"us_{plan}_{secrets.token_urlsafe(18)}"
    key_hash = _hash_key(raw)
    key_hint = raw[:12]
    credit = float(PLAN_CONFIG[plan]["default_credit_usd"])
    now = _now()
    conn.execute(
        """
        INSERT INTO api_keys(key_hash, key_hint, name, plan, status, credit_usd, total_spent_usd, created_at, updated_at)
        VALUES(?, ?, ?, ?, 'active', ?, 0, ?, ?)
        """,
        (key_hash, key_hint, name, plan, credit, now, now),
    )
    conn.commit()
    return {
        "api_key": raw,
        "name": name,
        "plan": plan,
        "credit_usd": round(credit, 2),
        "daily_limit": PLAN_CONFIG[plan]["daily_limit"],
        "price_per_query_usd": PLAN_CONFIG[plan]["price_per_query_usd"],
    }


def topup_key(conn, raw_key: str, amount_usd: float) -> dict:
    key_hash = _hash_key(raw_key)
    row = conn.execute(
        "SELECT key_hint, credit_usd, status FROM api_keys WHERE key_hash = ?",
        (key_hash,),
    ).fetchone()
    if row is None:
        return {"error": "api key not found"}
    if row["status"] != "active":
        return {"error": "api key inactive"}

    conn.execute(
        "UPDATE api_keys SET credit_usd = credit_usd + ?, updated_at = ? WHERE key_hash = ?",
        (amount_usd, _now(), key_hash),
    )
    conn.commit()
    updated = conn.execute(
        "SELECT key_hint, credit_usd, plan FROM api_keys WHERE key_hash = ?",
        (key_hash,),
    ).fetchone()
    return {
        "key_hint": updated["key_hint"],
        "plan": updated["plan"],
        "credit_usd": round(float(updated["credit_usd"]), 2),
    }


def authorize_and_record(conn, raw_key: str | None) -> dict:
    if not raw_key:
        return {
            "allowed": True,
            "plan": "anonymous",
            "charged_usd": 0.0,
            "remaining_daily": None,
        }

    key_hash = _hash_key(raw_key)
    today = _day()

    conn.execute("BEGIN IMMEDIATE")
    try:
        key = conn.execute(
            "SELECT key_hint, plan, status, credit_usd FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if key is None:
            conn.rollback()
            return {"allowed": False, "error": "invalid api key"}
        if key["status"] != "active":
            conn.rollback()
            return {"allowed": False, "error": "api key inactive"}

        plan = key["plan"]
        conf = PLAN_CONFIG.get(plan)
        if conf is None:
            conn.rollback()
            return {"allowed": False, "error": "invalid plan"}

        usage = conn.execute(
            "SELECT queries FROM api_usage_daily WHERE key_hash = ? AND day = ?",
            (key_hash, today),
        ).fetchone()
        queries = int(usage["queries"]) if usage else 0
        if queries >= int(conf["daily_limit"]):
            conn.rollback()
            return {
                "allowed": False,
                "error": "daily limit reached",
                "daily_limit": conf["daily_limit"],
            }

        charge = float(conf["price_per_query_usd"])
        if charge > 0:
            updated = conn.execute(
                """
                UPDATE api_keys
                SET credit_usd = credit_usd - ?,
                    total_spent_usd = total_spent_usd + ?,
                    updated_at = ?
                WHERE key_hash = ? AND credit_usd >= ?
                """,
                (charge, charge, _now(), key_hash, charge),
            )
            if updated.rowcount == 0:
                credit_row = conn.execute(
                    "SELECT credit_usd FROM api_keys WHERE key_hash = ?", (key_hash,)
                ).fetchone()
                conn.rollback()
                return {
                    "allowed": False,
                    "error": "insufficient key credit",
                    "credit_usd": round(float(credit_row["credit_usd"]), 4),
                }

        if usage:
            conn.execute(
                "UPDATE api_usage_daily SET queries = queries + 1, spend_usd = spend_usd + ? WHERE key_hash = ? AND day = ?",
                (charge, key_hash, today),
            )
        else:
            conn.execute(
                "INSERT INTO api_usage_daily(key_hash, day, queries, spend_usd) VALUES(?, ?, 1, ?)",
                (key_hash, today, charge),
            )

        current_credit = conn.execute(
            "SELECT credit_usd FROM api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        conn.commit()
        remaining = int(conf["daily_limit"]) - (queries + 1)
        return {
            "allowed": True,
            "plan": plan,
            "charged_usd": round(charge, 4),
            "remaining_daily": remaining,
            "key_hint": key["key_hint"],
            "credit_usd": round(float(current_credit["credit_usd"]), 4),
        }
    except Exception:
        conn.rollback()
        raise


def list_keys(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT key_hint, name, plan, status, credit_usd, total_spent_usd, created_at, updated_at
        FROM api_keys
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [
        {
            "key_hint": r["key_hint"],
            "name": r["name"],
            "plan": r["plan"],
            "status": r["status"],
            "credit_usd": round(float(r["credit_usd"]), 4),
            "total_spent_usd": round(float(r["total_spent_usd"]), 4),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def usage_for_key(conn, raw_key: str) -> dict:
    key_hash = _hash_key(raw_key)
    key = conn.execute(
        "SELECT key_hint, name, plan, status, credit_usd, total_spent_usd FROM api_keys WHERE key_hash = ?",
        (key_hash,),
    ).fetchone()
    if key is None:
        return {"error": "api key not found"}

    rows = conn.execute(
        "SELECT day, queries, spend_usd FROM api_usage_daily WHERE key_hash = ? ORDER BY day DESC LIMIT 30",
        (key_hash,),
    ).fetchall()
    return {
        "key": {
            "key_hint": key["key_hint"],
            "name": key["name"],
            "plan": key["plan"],
            "status": key["status"],
            "credit_usd": round(float(key["credit_usd"]), 4),
            "total_spent_usd": round(float(key["total_spent_usd"]), 4),
        },
        "daily": [
            {
                "day": r["day"],
                "queries": int(r["queries"]),
                "spend_usd": round(float(r["spend_usd"]), 4),
            }
            for r in rows
        ],
    }
