import sqlite3
from pathlib import Path

from unstoppable.apikeys import init_schema as init_api_key_schema


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            content TEXT,
            last_crawled TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS page_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL,
            target_url TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 100,
            discovered_from TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            enqueued_at TEXT NOT NULL,
            claimed_at TEXT,
            completed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_crawl_queue_status_priority
        ON crawl_queue(status, priority DESC, enqueued_at ASC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            intent_id TEXT,
            payment_id TEXT,
            txid TEXT,
            status TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_retry_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            payment_id TEXT,
            intent_id TEXT,
            reason TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_attempt_at TEXT,
            last_error TEXT,
            dead_lettered_at TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(payment_retry_jobs)").fetchall()
    }
    if "max_attempts" not in cols:
        conn.execute(
            "ALTER TABLE payment_retry_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5"
        )
    if "last_error" not in cols:
        conn.execute("ALTER TABLE payment_retry_jobs ADD COLUMN last_error TEXT")
    if "dead_lettered_at" not in cols:
        conn.execute("ALTER TABLE payment_retry_jobs ADD COLUMN dead_lettered_at TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_retry_jobs_status
        ON payment_retry_jobs(status, next_attempt_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_nonces (
            nonce TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            idem_key TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(scope, idem_key)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_receipts_payment_id
        ON payment_receipts(payment_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_receipts_txid
        ON payment_receipts(txid)
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS page_fts
        USING fts5(url UNINDEXED, title, content, last_crawled UNINDEXED)
        """
    )
    init_api_key_schema(conn)
    conn.commit()
