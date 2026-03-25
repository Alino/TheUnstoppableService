from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

import uvicorn

from unstoppable.autonomy import AutonomyController
from unstoppable.brain import evaluate_once
from unstoppable.config import (
    DB_PATH,
    POLICY_CONFIG_PATH,
    REVENUE_CONFIG_PATH,
    validate_runtime_secrets,
)
from unstoppable.crawler import crawl
from unstoppable.indexer import rebuild_all
from unstoppable.monetization import RevenueService
from unstoppable.policy import PolicyService
from unstoppable.runtime import build_payment_executor
from unstoppable.search_api import app
from unstoppable.services import (
    run_brain_worker,
    run_crawler_worker,
    run_indexer_worker,
)
from unstoppable.storage import connect, init_schema
from unstoppable.treasury import TreasuryService


def _build_controller(args: argparse.Namespace) -> AutonomyController:
    policy = PolicyService(POLICY_CONFIG_PATH)
    payment_executor = build_payment_executor()
    return AutonomyController(
        db_path=DB_PATH,
        seed_file=Path(args.seed_file),
        treasury_state_file=Path(args.state_file),
        interval_seconds=args.interval_seconds,
        max_pages_per_cycle=args.max_pages_per_cycle,
        delay_seconds=args.delay_seconds,
        wallet_sync=args.wallet_sync,
        btc_address=args.btc_address,
        policy_service=policy,
        payment_executor=payment_executor,
        receipts_db_path=DB_PATH,
    )


def _cmd_crawl(args: argparse.Namespace) -> None:
    conn = connect(DB_PATH)
    init_schema(conn)

    seed_file = Path(args.seed_file)
    seeds = [
        line.strip() for line in seed_file.read_text().splitlines() if line.strip()
    ]
    result = crawl(
        conn, seeds, max_pages=args.max_pages, delay_seconds=args.delay_seconds
    )
    print(json.dumps(result.__dict__, indent=2))


def _cmd_index(args: argparse.Namespace) -> None:
    conn = connect(DB_PATH)
    init_schema(conn)
    result = rebuild_all(conn)
    print(json.dumps(result, indent=2))


def _cmd_serve(args: argparse.Namespace) -> None:
    conn = connect(DB_PATH)
    init_schema(conn)
    conn.close()

    controller = _build_controller(args)
    policy = PolicyService(POLICY_CONFIG_PATH)
    payment_executor = build_payment_executor()
    app.state.controller = controller
    app.state.policy = policy
    app.state.treasury_service = TreasuryService(
        Path(args.state_file),
        policy_service=policy,
        payment_executor=payment_executor,
        receipts_db_path=DB_PATH,
    )
    app.state.revenue = RevenueService(REVENUE_CONFIG_PATH)
    if args.autonomy:
        controller.start()

    uvicorn.run(app, host=args.host, port=args.port)


def _cmd_run(args: argparse.Namespace) -> None:
    conn = connect(DB_PATH)
    init_schema(conn)
    page_count = int(conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"])
    conn.close()

    controller = _build_controller(args)
    policy = PolicyService(POLICY_CONFIG_PATH)
    payment_executor = build_payment_executor()
    app.state.controller = controller
    app.state.policy = policy
    app.state.treasury_service = TreasuryService(
        Path(args.state_file),
        policy_service=policy,
        payment_executor=payment_executor,
        receipts_db_path=DB_PATH,
    )
    app.state.revenue = RevenueService(REVENUE_CONFIG_PATH)

    if page_count == 0:
        print("No pages indexed yet. Running bootstrap cycle...")
        print(json.dumps(controller.run_cycle(), indent=2))

    if args.autonomy:
        controller.start()

    uvicorn.run(app, host=args.host, port=args.port)


def _cmd_brain_once(args: argparse.Namespace) -> None:
    decision = evaluate_once(Path(args.state_file))
    print(json.dumps(decision.__dict__, indent=2))


def _cmd_run_crawler_worker(args: argparse.Namespace) -> None:
    run_crawler_worker(
        worker_id=args.worker_id,
        seed_file=Path(args.seed_file),
        batch_size=args.batch_size,
        interval_seconds=args.interval_seconds,
        delay_seconds=args.delay_seconds,
    )


def _cmd_run_indexer(args: argparse.Namespace) -> None:
    run_indexer_worker(interval_seconds=args.interval_seconds)


def _cmd_run_brain_worker(args: argparse.Namespace) -> None:
    run_brain_worker(
        treasury_state_file=Path(args.state_file),
        interval_seconds=args.interval_seconds,
        wallet_sync=args.wallet_sync,
        btc_address=args.btc_address,
    )


def _cmd_run_phase2(args: argparse.Namespace) -> None:
    conn = connect(DB_PATH)
    init_schema(conn)
    conn.close()

    crawler_thread = threading.Thread(
        target=run_crawler_worker,
        kwargs={
            "worker_id": args.worker_id,
            "seed_file": Path(args.seed_file),
            "batch_size": args.batch_size,
            "interval_seconds": args.crawler_interval_seconds,
            "delay_seconds": args.delay_seconds,
        },
        daemon=True,
    )
    indexer_thread = threading.Thread(
        target=run_indexer_worker,
        kwargs={"interval_seconds": args.indexer_interval_seconds},
        daemon=True,
    )
    brain_thread = threading.Thread(
        target=run_brain_worker,
        kwargs={
            "treasury_state_file": Path(args.state_file),
            "interval_seconds": args.brain_interval_seconds,
            "wallet_sync": args.wallet_sync,
            "btc_address": args.btc_address,
        },
        daemon=True,
    )
    crawler_thread.start()
    indexer_thread.start()
    brain_thread.start()

    controller = _build_controller(args)
    policy = PolicyService(POLICY_CONFIG_PATH)
    payment_executor = build_payment_executor()
    app.state.controller = controller
    app.state.policy = policy
    app.state.treasury_service = TreasuryService(
        Path(args.state_file),
        policy_service=policy,
        payment_executor=payment_executor,
        receipts_db_path=DB_PATH,
    )
    app.state.revenue = RevenueService(REVENUE_CONFIG_PATH)
    uvicorn.run(app, host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The Unstoppable Service Phase 0 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl_cmd = subparsers.add_parser("crawl", help="Run crawler")
    crawl_cmd.add_argument("--seed-file", default="seeds.txt")
    crawl_cmd.add_argument("--max-pages", type=int, default=100)
    crawl_cmd.add_argument("--delay-seconds", type=float, default=0.25)
    crawl_cmd.set_defaults(func=_cmd_crawl)

    index_cmd = subparsers.add_parser("index", help="Rebuild search index")
    index_cmd.set_defaults(func=_cmd_index)

    serve_cmd = subparsers.add_parser("serve", help="Run search API")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--seed-file", default="seeds.txt")
    serve_cmd.add_argument("--state-file", default="treasury_state.json")
    serve_cmd.add_argument("--interval-seconds", type=int, default=120)
    serve_cmd.add_argument("--max-pages-per-cycle", type=int, default=25)
    serve_cmd.add_argument("--delay-seconds", type=float, default=0.1)
    serve_cmd.add_argument(
        "--wallet-sync", action=argparse.BooleanOptionalAction, default=False
    )
    serve_cmd.add_argument("--btc-address", default=None)
    serve_cmd.add_argument(
        "--autonomy", action=argparse.BooleanOptionalAction, default=False
    )
    serve_cmd.set_defaults(func=_cmd_serve)

    run_cmd = subparsers.add_parser("run", help="Start full local product")
    run_cmd.add_argument("--host", default="0.0.0.0")
    run_cmd.add_argument("--port", type=int, default=8080)
    run_cmd.add_argument("--seed-file", default="seeds.txt")
    run_cmd.add_argument("--state-file", default="treasury_state.json")
    run_cmd.add_argument("--interval-seconds", type=int, default=120)
    run_cmd.add_argument("--max-pages-per-cycle", type=int, default=25)
    run_cmd.add_argument("--delay-seconds", type=float, default=0.1)
    run_cmd.add_argument(
        "--wallet-sync", action=argparse.BooleanOptionalAction, default=False
    )
    run_cmd.add_argument("--btc-address", default=None)
    run_cmd.add_argument(
        "--autonomy", action=argparse.BooleanOptionalAction, default=True
    )
    run_cmd.set_defaults(func=_cmd_run)

    brain_cmd = subparsers.add_parser("brain-once", help="Run one brain cycle")
    brain_cmd.add_argument("--state-file", default="treasury_state.json")
    brain_cmd.set_defaults(func=_cmd_brain_once)

    crawler_worker_cmd = subparsers.add_parser(
        "run-crawler-worker", help="Run distributed crawler worker"
    )
    crawler_worker_cmd.add_argument("--worker-id", default="crawler-1")
    crawler_worker_cmd.add_argument("--seed-file", default="seeds.txt")
    crawler_worker_cmd.add_argument("--batch-size", type=int, default=10)
    crawler_worker_cmd.add_argument("--interval-seconds", type=int, default=20)
    crawler_worker_cmd.add_argument("--delay-seconds", type=float, default=0.1)
    crawler_worker_cmd.set_defaults(func=_cmd_run_crawler_worker)

    indexer_worker_cmd = subparsers.add_parser(
        "run-indexer-worker", help="Run distributed indexer worker"
    )
    indexer_worker_cmd.add_argument("--interval-seconds", type=int, default=30)
    indexer_worker_cmd.set_defaults(func=_cmd_run_indexer)

    brain_worker_cmd = subparsers.add_parser(
        "run-brain-worker", help="Run distributed brain worker"
    )
    brain_worker_cmd.add_argument("--state-file", default="treasury_state.json")
    brain_worker_cmd.add_argument("--interval-seconds", type=int, default=30)
    brain_worker_cmd.add_argument(
        "--wallet-sync", action=argparse.BooleanOptionalAction, default=False
    )
    brain_worker_cmd.add_argument("--btc-address", default=None)
    brain_worker_cmd.set_defaults(func=_cmd_run_brain_worker)

    phase2_cmd = subparsers.add_parser(
        "run-phase2", help="Run Phase 2 stack in one process"
    )
    phase2_cmd.add_argument("--host", default="0.0.0.0")
    phase2_cmd.add_argument("--port", type=int, default=8080)
    phase2_cmd.add_argument("--seed-file", default="seeds.txt")
    phase2_cmd.add_argument("--state-file", default="treasury_state.json")
    phase2_cmd.add_argument(
        "--wallet-sync", action=argparse.BooleanOptionalAction, default=False
    )
    phase2_cmd.add_argument("--btc-address", default=None)
    phase2_cmd.add_argument("--worker-id", default="crawler-1")
    phase2_cmd.add_argument("--batch-size", type=int, default=10)
    phase2_cmd.add_argument("--delay-seconds", type=float, default=0.1)
    phase2_cmd.add_argument("--crawler-interval-seconds", type=int, default=20)
    phase2_cmd.add_argument("--indexer-interval-seconds", type=int, default=30)
    phase2_cmd.add_argument("--brain-interval-seconds", type=int, default=30)
    phase2_cmd.add_argument("--interval-seconds", type=int, default=30)
    phase2_cmd.add_argument("--max-pages-per-cycle", type=int, default=25)
    phase2_cmd.set_defaults(func=_cmd_run_phase2)

    return parser


def main() -> None:
    validate_runtime_secrets()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
