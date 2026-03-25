from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from unstoppable.brain import evaluate_state
from unstoppable.config import DB_PATH, POLICY_CONFIG_PATH
from unstoppable.crawler import crawl_queue_batch
from unstoppable.indexer import rebuild_all
from unstoppable.policy import PolicyService
from unstoppable.queue import enqueue_urls, queue_stats
from unstoppable.runtime import build_payment_executor
from unstoppable.storage import connect, init_schema
from unstoppable.treasury import TreasuryService
from unstoppable.wallets import PublicApiWalletAdapter


def _load_seeds(seed_file: Path) -> list[str]:
    return [line.strip() for line in seed_file.read_text().splitlines() if line.strip()]


def run_crawler_worker(
    worker_id: str,
    seed_file: Path,
    batch_size: int,
    interval_seconds: int,
    delay_seconds: float,
) -> None:
    print(f"crawler-worker starting id={worker_id}")
    while True:
        conn = connect(DB_PATH)
        init_schema(conn)
        seeds = _load_seeds(seed_file)
        enqueue_urls(conn, seeds, priority=100)
        result = crawl_queue_batch(
            conn,
            worker_id=worker_id,
            batch_size=batch_size,
            delay_seconds=delay_seconds,
        )
        stats = queue_stats(conn)
        conn.close()
        print(
            json.dumps(
                {"service": "crawler", "result": asdict(result), "queue": stats},
                indent=2,
            )
        )
        time.sleep(interval_seconds)


def run_indexer_worker(interval_seconds: int) -> None:
    print("indexer-worker starting")
    while True:
        conn = connect(DB_PATH)
        init_schema(conn)
        index_result = rebuild_all(conn)
        stats = queue_stats(conn)
        conn.close()
        print(
            json.dumps(
                {
                    "service": "indexer",
                    "indexed_pages": index_result["indexed_pages"],
                    "external": index_result["external"],
                    "queue": stats,
                },
                indent=2,
            )
        )
        time.sleep(interval_seconds)


def run_brain_worker(
    treasury_state_file: Path,
    interval_seconds: int,
    wallet_sync: bool,
    btc_address: str | None,
) -> None:
    print("brain-worker starting")
    policy = PolicyService(POLICY_CONFIG_PATH)
    treasury = TreasuryService(
        treasury_state_file,
        policy_service=policy,
        payment_executor=build_payment_executor(),
        receipts_db_path=DB_PATH,
    )
    adapter = PublicApiWalletAdapter(btc_address=btc_address) if wallet_sync else None

    while True:
        if wallet_sync:
            treasury.refresh_from_wallets(adapter)
        treasury.accrue_cycle_cost(interval_seconds=interval_seconds)
        payment = treasury.maybe_autopay_hosting()
        state = treasury.snapshot()
        decision = asdict(evaluate_state(state))
        print(
            json.dumps(
                {
                    "service": "brain",
                    "decision": decision,
                    "payment": payment,
                    "treasury": state,
                },
                indent=2,
            )
        )
        time.sleep(interval_seconds)
