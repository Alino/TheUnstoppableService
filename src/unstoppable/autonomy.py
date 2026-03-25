from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from unstoppable.brain import BrainDecision, evaluate_state
from unstoppable.crawler import CrawlResult, crawl
from unstoppable.indexer import rebuild_all
from unstoppable.payment_exec import PaymentExecutor
from unstoppable.policy import PolicyService
from unstoppable.storage import connect, init_schema
from unstoppable.treasury import TreasuryService
from unstoppable.wallets import PublicApiWalletAdapter, WalletAdapter


@dataclass
class CycleSummary:
    started_at: str
    finished_at: str
    crawl: dict
    indexed_pages: int
    brain: dict
    treasury: dict
    payment: dict


class AutonomyController:
    def __init__(
        self,
        db_path: Path,
        seed_file: Path,
        treasury_state_file: Path,
        interval_seconds: int = 120,
        max_pages_per_cycle: int = 25,
        delay_seconds: float = 0.1,
        wallet_sync: bool = False,
        btc_address: str | None = None,
        policy_service: PolicyService | None = None,
        payment_executor: PaymentExecutor | None = None,
        receipts_db_path: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.seed_file = seed_file
        self.treasury_state_file = treasury_state_file
        self.interval_seconds = interval_seconds
        self.max_pages_per_cycle = max_pages_per_cycle
        self.delay_seconds = delay_seconds
        self.treasury = TreasuryService(
            treasury_state_file,
            policy_service=policy_service,
            payment_executor=payment_executor,
            receipts_db_path=receipts_db_path,
        )

        self.wallet_adapter: WalletAdapter | None = None
        if wallet_sync:
            self.wallet_adapter = PublicApiWalletAdapter(btc_address=btc_address)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()
        self._status_lock = threading.Lock()

        self._status: dict = {
            "running": False,
            "last_cycle": None,
            "last_error": None,
            "cycles_completed": 0,
            "config": {
                "interval_seconds": self.interval_seconds,
                "max_pages_per_cycle": self.max_pages_per_cycle,
                "delay_seconds": self.delay_seconds,
                "seed_file": str(self.seed_file),
                "treasury_state_file": str(self.treasury_state_file),
                "wallet_sync": wallet_sync,
                "btc_address": btc_address,
            },
        }

    def _load_seeds(self) -> list[str]:
        return [
            line.strip()
            for line in self.seed_file.read_text().splitlines()
            if line.strip()
        ]

    def _cycle_page_budget(self, brain: BrainDecision) -> int:
        if brain.mode == "growth":
            return max(10, self.max_pages_per_cycle * 2)
        if brain.mode == "stable":
            return self.max_pages_per_cycle
        if brain.mode == "conservation":
            return max(10, self.max_pages_per_cycle // 2)
        return max(5, self.max_pages_per_cycle // 4)

    def add_donation(
        self,
        coin: str,
        amount_usd: float,
        source: str = "manual",
        txid: str | None = None,
    ) -> dict:
        return self.treasury.add_donation(
            coin=coin, amount_usd=amount_usd, source=source, txid=txid
        )

    def payments(self, limit: int = 20) -> list[dict]:
        return self.treasury.payments(limit=limit)

    def payment_intents(self, limit: int = 20) -> list[dict]:
        return self.treasury.payment_intents(limit=limit)

    def pay_now(self, amount_usd: float | None = None, provider: str = "akash") -> dict:
        if amount_usd is None:
            amount_usd = float(self.treasury.state["infra"]["accrued_hosting_due_usd"])
        return self.treasury.execute_hosting_payment(
            amount_usd=amount_usd, provider=provider, reason="manual-now"
        )

    def create_payment_intent(
        self, amount_usd: float, provider: str, reason: str
    ) -> dict:
        return self.treasury.create_payment_intent(
            amount_usd=amount_usd,
            provider=provider,
            reason=reason,
        )

    def execute_payment_intent(self, intent_id: str) -> dict:
        return self.treasury.execute_payment_intent(intent_id=intent_id)

    def run_cycle(self) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            return {"message": "cycle already running"}

        started = datetime.now(timezone.utc).isoformat()
        try:
            seeds = self._load_seeds()
            self.treasury.refresh_from_wallets(self.wallet_adapter)
            pre_state = self.treasury.snapshot()
            pre_brain = evaluate_state(pre_state)
            pages_to_crawl = self._cycle_page_budget(pre_brain)

            conn = connect(self.db_path)
            init_schema(conn)

            crawl_result: CrawlResult = crawl(
                conn,
                seed_urls=seeds,
                max_pages=pages_to_crawl,
                delay_seconds=self.delay_seconds,
            )
            indexed = rebuild_all(conn)["indexed_pages"]
            conn.close()

            self.treasury.accrue_cycle_cost(interval_seconds=self.interval_seconds)
            payment = self.treasury.maybe_autopay_hosting()
            post_state = self.treasury.snapshot()
            post_brain = evaluate_state(post_state)
            finished = datetime.now(timezone.utc).isoformat()

            summary = CycleSummary(
                started_at=started,
                finished_at=finished,
                crawl=asdict(crawl_result),
                indexed_pages=indexed,
                brain=asdict(post_brain),
                treasury=post_state,
                payment=payment,
            )
            payload = asdict(summary)

            with self._status_lock:
                self._status["last_cycle"] = payload
                self._status["last_error"] = None
                self._status["cycles_completed"] += 1

            return payload
        except Exception as exc:
            with self._status_lock:
                self._status["last_error"] = str(exc)
            return {"error": str(exc)}
        finally:
            self._cycle_lock.release()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_cycle()
            self._stop_event.wait(self.interval_seconds)

    def start(self) -> dict:
        with self._status_lock:
            if self._status["running"]:
                return {"message": "already running"}
            self._status["running"] = True
            self._stop_event.clear()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return {"message": "autonomy loop started"}

    def stop(self) -> dict:
        with self._status_lock:
            if not self._status["running"]:
                return {"message": "already stopped"}
            self._status["running"] = False

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        return {"message": "autonomy loop stopped"}

    def status(self) -> dict:
        with self._status_lock:
            payload = json.loads(json.dumps(self._status))
        payload["treasury"] = self.treasury.snapshot()
        payload["recent_payments"] = self.treasury.payments(limit=5)
        return payload
