from __future__ import annotations

import json
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_response(conn, scope: str, idem_key: str) -> tuple[int, dict] | None:
    row = conn.execute(
        """
        SELECT status_code, response_json
        FROM idempotency_keys
        WHERE scope = ? AND idem_key = ?
        """,
        (scope, idem_key),
    ).fetchone()
    if row is None:
        return None
    return int(row["status_code"]), json.loads(row["response_json"])


def reserve_or_get(
    conn, scope: str, idem_key: str
) -> tuple[str, tuple[int, dict] | None]:
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO idempotency_keys(scope, idem_key, status_code, response_json, created_at, updated_at)
            VALUES(?, ?, 102, ?, ?, ?)
            ON CONFLICT(scope, idem_key) DO NOTHING
            """,
            (
                scope,
                idem_key,
                json.dumps({"pending": True}, ensure_ascii=True),
                now,
                now,
            ),
        )
        changed = int(conn.execute("SELECT changes() AS c").fetchone()["c"])
        row = conn.execute(
            "SELECT status_code, response_json FROM idempotency_keys WHERE scope=? AND idem_key=?",
            (scope, idem_key),
        ).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if changed > 0:
        return "reserved", None
    if row is None:
        return "missing", None
    status_code = int(row["status_code"])
    payload = json.loads(row["response_json"])
    if status_code == 102:
        return "in_progress", (status_code, payload)
    return "done", (status_code, payload)


def store_response(
    conn, scope: str, idem_key: str, status_code: int, response: dict
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO idempotency_keys(scope, idem_key, status_code, response_json, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, idem_key)
        DO UPDATE SET status_code=excluded.status_code, response_json=excluded.response_json, updated_at=excluded.updated_at
        """,
        (
            scope,
            idem_key,
            int(status_code),
            json.dumps(response, ensure_ascii=True),
            now,
            now,
        ),
    )
    conn.commit()
