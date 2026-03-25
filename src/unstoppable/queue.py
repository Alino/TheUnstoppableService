from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_urls(
    conn, urls: list[str], priority: int = 100, discovered_from: str | None = None
) -> int:
    inserted = 0
    for url in urls:
        url = url.strip()
        if not url:
            continue
        row = conn.execute(
            "SELECT status FROM crawl_queue WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO crawl_queue(url, status, priority, discovered_from, enqueued_at)
                VALUES(?, 'pending', ?, ?, ?)
                """,
                (url, priority, discovered_from, _now()),
            )
            inserted += 1
        elif row["status"] == "failed":
            conn.execute(
                """
                UPDATE crawl_queue
                SET status = 'pending', priority = ?, discovered_from = ?,
                    last_error = NULL, claimed_at = NULL, completed_at = NULL,
                    enqueued_at = ?
                WHERE url = ?
                """,
                (priority, discovered_from, _now(), url),
            )
    conn.commit()
    return inserted


def claim_urls(conn, limit: int, worker_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT id, url FROM crawl_queue
        WHERE status = 'pending'
        ORDER BY priority DESC, enqueued_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return []

    urls = []
    claimed_at = _now()
    for row in rows:
        conn.execute(
            """
            UPDATE crawl_queue
            SET status = 'in_progress', claimed_at = ?, last_error = ?
            WHERE id = ? AND status = 'pending'
            """,
            (claimed_at, f"claimed by {worker_id}", row["id"]),
        )
        changed = int(conn.execute("SELECT changes() AS c").fetchone()["c"])
        if changed > 0:
            urls.append(row["url"])
    conn.commit()
    return urls


def mark_done(conn, url: str) -> None:
    conn.execute(
        """
        UPDATE crawl_queue
        SET status = 'done', completed_at = ?, last_error = NULL
        WHERE url = ?
        """,
        (_now(), url),
    )
    conn.commit()


def mark_failed(conn, url: str, error: str, max_attempts: int = 3) -> None:
    row = conn.execute(
        "SELECT attempt_count FROM crawl_queue WHERE url = ?",
        (url,),
    ).fetchone()
    attempts = int(row["attempt_count"]) + 1 if row else 1
    status = "failed" if attempts >= max_attempts else "pending"
    conn.execute(
        """
        UPDATE crawl_queue
        SET status = ?, attempt_count = ?, last_error = ?, claimed_at = NULL
        WHERE url = ?
        """,
        (status, attempts, error[:800], url),
    )
    conn.commit()


def queue_stats(conn) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM crawl_queue GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: int(r["c"]) for r in rows}
    for status in ["pending", "in_progress", "done", "failed"]:
        by_status.setdefault(status, 0)
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
    }
