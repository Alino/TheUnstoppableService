from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path

from unstoppable.config import DB_PATH
from unstoppable.payment_exec import MockPaymentExecutor, PaymentExecutor
from unstoppable.policy import PolicyService
from unstoppable.wallets import WalletAdapter, WalletAdapterError


SECONDS_PER_MONTH = 30 * 24 * 60 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payment_id() -> str:
    return f"pay-{uuid.uuid4().hex}"


def _iso_after(seconds: int) -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + seconds, tz=timezone.utc
    ).isoformat()


class TreasuryService:
    def __init__(
        self,
        state_file: Path,
        policy_service: PolicyService | None = None,
        payment_executor: PaymentExecutor | None = None,
        receipts_db_path: Path | None = None,
    ) -> None:
        self.state_file = state_file
        self.policy_service = policy_service
        self.payment_executor = payment_executor or MockPaymentExecutor()
        self.receipts_db_path = receipts_db_path or DB_PATH
        self._thread_lock = threading.RLock()
        self._lock_depth = threading.local()
        self._lock_file_path = self.state_file.with_suffix(
            self.state_file.suffix + ".lock"
        )
        self.state = self._load_state()

    @contextmanager
    def _state_lock(self):
        depth = getattr(self._lock_depth, "depth", 0)
        if depth > 0:
            self._lock_depth.depth = depth + 1
            try:
                yield
            finally:
                self._lock_depth.depth -= 1
            return

        with self._thread_lock:
            self._lock_file_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fh = open(self._lock_file_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                self._lock_depth.depth = 1
                try:
                    yield
                finally:
                    self._lock_depth.depth = 0
            finally:
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_fh.close()

    def _record_receipt(self, kind: str, payload: dict) -> None:
        if not self.receipts_db_path:
            return
        self.receipts_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.receipts_db_path)
        try:
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
                INSERT INTO payment_receipts(recorded_at, kind, intent_id, payment_id, txid, status, payload_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now(),
                    kind,
                    payload.get("intent_id"),
                    payload.get("payment_id"),
                    payload.get("txid"),
                    payload.get("status"),
                    json.dumps(payload, ensure_ascii=True),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _enqueue_retry_job(
        self,
        *,
        reason: str,
        payload: dict,
        payment_id: str | None = None,
        intent_id: str | None = None,
        delay_seconds: int = 60,
        max_attempts: int = 5,
        last_error: str | None = None,
    ) -> None:
        if not self.receipts_db_path:
            return
        self.receipts_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.receipts_db_path)
        try:
            now = _utc_now()
            conn.execute(
                """
                INSERT INTO payment_retry_jobs(
                    created_at, updated_at, status, payment_id, intent_id, reason,
                    attempts, max_attempts, next_attempt_at, last_error, dead_lettered_at, payload_json
                ) VALUES(?, ?, 'pending', ?, ?, ?, 0, ?, ?, ?, NULL, ?)
                """,
                (
                    now,
                    now,
                    payment_id,
                    intent_id,
                    reason,
                    max_attempts,
                    _iso_after(delay_seconds),
                    last_error,
                    json.dumps(payload, ensure_ascii=True),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _read_state_with_retry(self, retries: int = 5, delay: float = 0.05) -> dict:
        for _ in range(retries):
            try:
                if not self.state_file.exists():
                    return self._default_state()
                text = self.state_file.read_text()
                if not text.strip():
                    raise json.JSONDecodeError("empty", text, 0)
                return json.loads(text)
            except json.JSONDecodeError:
                time.sleep(delay)
        if not self.state_file.exists():
            return self._default_state()
        raise RuntimeError(
            f"treasury state is unreadable: {self.state_file}; refusing to overwrite"
        )

    def _atomic_write(self, payload: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.state_file.parent),
            prefix=f"{self.state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(json.dumps(payload, indent=2))
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.state_file)

    def _default_state(self) -> dict:
        return {
            "balances_usd": {"btc": 0.0, "xmr": 0.0, "zec": 0.0, "usdc": 0.0},
            "monthly_burn_usd": 150.0,
            "monthly_donation_income_usd": 0.0,
            "infra": {
                "monthly_target_cost_usd": 150.0,
                "accrued_hosting_due_usd": 0.0,
                "autopay_threshold_usd": 10.0,
            },
            "donations": [],
            "payments": [],
            "swaps": [],
            "payment_intents": [],
            "wallet_sync": {
                "enabled": False,
                "source": "none",
                "last_sync": None,
                "last_error": None,
            },
            "updated_at": _utc_now(),
        }

    def _normalize_state(self, state: dict) -> dict:
        defaults = self._default_state()
        merged = {**defaults, **state}

        balances = {**defaults["balances_usd"], **state.get("balances_usd", {})}
        merged["balances_usd"] = {k: float(v) for k, v in balances.items()}

        infra = {**defaults["infra"], **state.get("infra", {})}
        merged["infra"] = {
            "monthly_target_cost_usd": float(
                infra.get("monthly_target_cost_usd", 150.0)
            ),
            "accrued_hosting_due_usd": float(infra.get("accrued_hosting_due_usd", 0.0)),
            "autopay_threshold_usd": float(infra.get("autopay_threshold_usd", 10.0)),
        }

        merged["monthly_burn_usd"] = float(
            merged.get("monthly_burn_usd", merged["infra"]["monthly_target_cost_usd"])
        )
        merged["monthly_donation_income_usd"] = float(
            merged.get("monthly_donation_income_usd", 0.0)
        )
        merged["donations"] = list(merged.get("donations", []))[-1000:]
        merged["payments"] = list(merged.get("payments", []))[-1000:]
        merged["swaps"] = list(merged.get("swaps", []))[-1000:]
        merged["payment_intents"] = list(merged.get("payment_intents", []))[-1000:]

        for payment in merged["payments"]:
            payment.setdefault("tx_status", "unknown")
            payment.setdefault("confirmations", 0)
            payment.setdefault("last_status_check", None)

        wallet_sync = {**defaults["wallet_sync"], **state.get("wallet_sync", {})}
        merged["wallet_sync"] = wallet_sync
        merged["updated_at"] = state.get("updated_at") or _utc_now()
        return merged

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            state = self._default_state()
            self._atomic_write(state)
            return state
        raw = self._read_state_with_retry()
        normalized = self._normalize_state(raw)
        self._atomic_write(normalized)
        return normalized

    def _reload_from_disk(self) -> None:
        if not self.state_file.exists():
            return
        raw = self._read_state_with_retry()
        self.state = self._normalize_state(raw)

    def _save(self) -> None:
        self.state["updated_at"] = _utc_now()
        self._atomic_write(self.state)

    def snapshot(self) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            balances = self.state["balances_usd"]
            treasury = float(sum(balances.values()))
            monthly_burn = float(self.state["monthly_burn_usd"])
            monthly_income = float(self.state["monthly_donation_income_usd"])
            net_burn = max(monthly_burn - monthly_income, 0.01)
            runway = treasury / net_burn
            return {
                "balances_usd": {k: round(v, 2) for k, v in balances.items()},
                "treasury_usd": round(treasury, 2),
                "monthly_burn_usd": round(monthly_burn, 2),
                "monthly_donation_income_usd": round(monthly_income, 2),
                "net_burn_usd": round(net_burn, 2),
                "runway_months": round(runway, 2),
                "infra": self.state["infra"],
                "policy": self.policy_service.get() if self.policy_service else None,
                "wallet_sync": self.state["wallet_sync"],
                "updated_at": self.state["updated_at"],
            }

    def _paid_today_usd(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        for payment in self.state.get("payments", []):
            ts = str(payment.get("timestamp", ""))
            if ts.startswith(today):
                total += float(payment.get("amount_usd", 0.0))
        return total

    def add_donation(
        self,
        coin: str,
        amount_usd: float,
        source: str = "manual",
        txid: str | None = None,
    ) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            coin_key = coin.lower()
            if coin_key not in self.state["balances_usd"]:
                self.state["balances_usd"][coin_key] = 0.0
            self.state["balances_usd"][coin_key] += amount_usd
            event = {
                "timestamp": _utc_now(),
                "coin": coin_key,
                "amount_usd": round(amount_usd, 2),
                "source": source,
                "txid": txid,
            }
            self.state["donations"].append(event)
            self.state["monthly_donation_income_usd"] = float(
                self.state.get("monthly_donation_income_usd", 0.0)
            ) + float(amount_usd)
            self._save()
            return event

    def record_income(
        self, amount_usd: float, source: str, note: str | None = None
    ) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            if amount_usd <= 0:
                return {"message": "no income recorded"}

            self.state["balances_usd"]["usdc"] = float(
                self.state["balances_usd"].get("usdc", 0.0)
            ) + float(amount_usd)
            self.state["monthly_donation_income_usd"] = float(
                self.state.get("monthly_donation_income_usd", 0.0)
            ) + float(amount_usd)
            event = {
                "timestamp": _utc_now(),
                "coin": "usdc",
                "amount_usd": round(float(amount_usd), 4),
                "source": source,
                "txid": note,
            }
            self.state["donations"].append(event)
            self._save()
            return event

    def refresh_from_wallets(self, adapter: WalletAdapter | None) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            if adapter is None:
                return {"message": "wallet sync disabled"}
            try:
                snapshot = adapter.fetch_balances_usd()
                for coin, value in snapshot.balances_usd.items():
                    self.state["balances_usd"][coin] = float(value)
                self.state["wallet_sync"] = {
                    "enabled": True,
                    "source": snapshot.source,
                    "last_sync": _utc_now(),
                    "last_error": None,
                }
                self._save()
                return {
                    "message": "wallet sync ok",
                    "source": snapshot.source,
                    "balances": snapshot.balances_usd,
                }
            except WalletAdapterError as exc:
                self.state["wallet_sync"]["enabled"] = True
                self.state["wallet_sync"]["last_error"] = str(exc)
                self.state["wallet_sync"]["last_sync"] = _utc_now()
                self._save()
                return {"error": str(exc)}

    def _shift_to_usdc(self, required_usd: float, fee_rate: float = 0.005) -> dict:
        self._reload_from_disk()
        moved_total = 0.0
        moved = []
        for coin in ("btc", "xmr", "zec"):
            if moved_total >= required_usd:
                break
            available = float(self.state["balances_usd"].get(coin, 0.0))
            if available <= 0:
                continue

            need = required_usd - moved_total
            gross = min(available, need / (1.0 - fee_rate))
            net = gross * (1.0 - fee_rate)
            self.state["balances_usd"][coin] -= gross
            self.state["balances_usd"]["usdc"] += net
            moved_total += net
            moved.append(
                {"coin": coin, "gross_usd": round(gross, 2), "net_usd": round(net, 2)}
            )

        if moved:
            self.state["swaps"].append(
                {"timestamp": _utc_now(), "to": "usdc", "moves": moved}
            )
            self._save()
        return {"moved_usd": round(moved_total, 2), "legs": moved}

    def accrue_cycle_cost(self, interval_seconds: int) -> float:
        with self._state_lock():
            self._reload_from_disk()
            monthly_target = float(self.state["infra"]["monthly_target_cost_usd"])
            cycle_cost = monthly_target * (interval_seconds / SECONDS_PER_MONTH)
            self.state["infra"]["accrued_hosting_due_usd"] += cycle_cost
            self.state["monthly_burn_usd"] = monthly_target
            self._save()
            return round(cycle_cost, 6)

    def execute_hosting_payment(
        self, amount_usd: float, provider: str = "akash", reason: str = "manual"
    ) -> dict:
        intent = self.create_payment_intent(
            amount_usd=amount_usd,
            provider=provider,
            reason=reason,
        )
        if intent.get("status") == "rejected":
            return intent
        if "error" in intent:
            return intent
        return self.execute_payment_intent(intent_id=intent["id"])

    def create_payment_intent(
        self,
        amount_usd: float,
        provider: str = "akash",
        reason: str = "manual",
    ) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            if amount_usd < 0.01:
                return {"message": "nothing to pay"}

            treasury_usd = float(sum(self.state["balances_usd"].values()))
            paid_today = self._paid_today_usd()
            policy_decision = {
                "allowed": True,
                "reason": "no policy configured",
            }
            if self.policy_service is not None:
                policy_decision = self.policy_service.evaluate_payment(
                    amount_usd=float(amount_usd),
                    treasury_usd=treasury_usd,
                    paid_today_usd=paid_today,
                    reason=reason,
                )

            intent = {
                "id": _payment_id(),
                "timestamp": _utc_now(),
                "provider": provider,
                "reason": reason,
                "amount_usd": round(float(amount_usd), 4),
                "status": "approved" if policy_decision.get("allowed") else "rejected",
                "policy": policy_decision,
            }
            self.state["payment_intents"].append(intent)
            self._save()
            self._record_receipt(
                "payment_intent",
                {
                    "intent_id": intent["id"],
                    "status": intent["status"],
                    "provider": provider,
                    "amount_usd": intent["amount_usd"],
                },
            )
            return intent

    def execute_payment_intent(
        self, intent_id: str, signer: str = "mock-signer"
    ) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            intent = next(
                (
                    x
                    for x in self.state.get("payment_intents", [])
                    if x.get("id") == intent_id
                ),
                None,
            )
            if intent is None:
                return {"error": "payment intent not found"}
            if intent.get("status") == "rejected":
                return {"error": "payment intent rejected by policy", "intent": intent}
            if intent.get("status") == "executed":
                return {"message": "intent already executed", "intent": intent}

            amount_usd = float(intent.get("amount_usd", 0.0))
            provider = str(intent.get("provider", "akash"))

            usdc_balance = float(self.state["balances_usd"].get("usdc", 0.0))
            if usdc_balance < amount_usd:
                self._shift_to_usdc(amount_usd - usdc_balance)

            usdc_balance = float(self.state["balances_usd"].get("usdc", 0.0))
            if usdc_balance < amount_usd:
                self._enqueue_retry_job(
                    reason="insufficient_treasury",
                    payload={
                        "intent_id": intent_id,
                        "needed_usd": round(amount_usd, 2),
                        "usdc_usd": round(usdc_balance, 2),
                    },
                    intent_id=intent_id,
                    delay_seconds=300,
                    last_error="insufficient treasury",
                )
                return {
                    "error": "insufficient treasury for hosting payment",
                    "needed_usd": round(amount_usd, 2),
                    "usdc_usd": round(usdc_balance, 2),
                }

            payment = {
                "id": _payment_id(),
                "timestamp": _utc_now(),
                "provider": provider,
                "amount_usd": round(amount_usd, 2),
                "status": "paid",
                "intent_id": intent_id,
                "signer": signer,
                "txid": "",
                "tx_status": "unknown",
                "confirmations": 0,
                "last_status_check": None,
            }

            try:
                exec_result = self.payment_executor.execute(
                    intent=intent, payment=payment
                )
            except Exception as exc:
                self._enqueue_retry_job(
                    reason="executor_error",
                    payload={
                        "error": str(exc),
                        "intent_id": intent_id,
                        "payment": payment,
                    },
                    payment_id=payment["id"],
                    intent_id=intent_id,
                    delay_seconds=120,
                    last_error=str(exc),
                )
                return {
                    "error": "payment executor failed",
                    "detail": str(exc),
                    "payment_id": payment["id"],
                    "intent_id": intent_id,
                }

            self.state["balances_usd"]["usdc"] -= amount_usd
            self.state["infra"]["accrued_hosting_due_usd"] = max(
                float(self.state["infra"]["accrued_hosting_due_usd"]) - amount_usd,
                0.0,
            )

            payment["txid"] = exec_result.txid
            payment["tx_status"] = exec_result.status
            payment["signer"] = exec_result.signer
            payment["executor_meta"] = exec_result.meta
            payment["last_status_check"] = _utc_now()
            self.state["payments"].append(payment)
            intent["status"] = "executed"
            intent["executed_at"] = _utc_now()
            intent["payment_id"] = payment["id"]
            self._save()
            self._record_receipt(
                "payment_executed",
                {
                    "intent_id": intent_id,
                    "payment_id": payment["id"],
                    "txid": payment.get("txid"),
                    "status": payment.get("tx_status"),
                    "amount_usd": payment.get("amount_usd"),
                },
            )
            return payment

    def refresh_payment_status(self, payment_id: str | None = None) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            payments = self.state.get("payments", [])
            if payment_id:
                payments = [p for p in payments if p.get("id") == payment_id]
                if not payments:
                    return {"error": "payment not found"}

            updated = 0
            for payment in payments:
                txid = str(payment.get("txid", "")).strip()
                if not txid:
                    continue
                status = self.payment_executor.tx_status(txid)
                payment["tx_status"] = status.get(
                    "status", payment.get("tx_status", "unknown")
                )
                payment["confirmations"] = int(
                    status.get("confirmations", payment.get("confirmations", 0))
                )
                payment["last_status_check"] = _utc_now()
                payment["tx_status_meta"] = status
                updated += 1
                self._record_receipt(
                    "payment_status_refresh",
                    {
                        "payment_id": payment.get("id"),
                        "intent_id": payment.get("intent_id"),
                        "txid": txid,
                        "status": payment.get("tx_status"),
                        "confirmations": payment.get("confirmations"),
                    },
                )

            self._save()
            return {"updated": updated, "payments": payments}

    def apply_webhook_update(
        self, payload: dict, source: str = "executor-webhook"
    ) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            payment_id = str(payload.get("payment_id", "")).strip()
            txid = str(payload.get("txid", "")).strip()

            target = None
            for payment in self.state.get("payments", []):
                if payment_id and payment.get("id") == payment_id:
                    target = payment
                    break
                if txid and payment.get("txid") == txid:
                    target = payment
                    break

            if target is None:
                return {
                    "error": "payment not found",
                    "payment_id": payment_id,
                    "txid": txid,
                }

            if txid:
                target["txid"] = txid
            if "status" in payload:
                target["tx_status"] = str(payload.get("status"))
            if "confirmations" in payload:
                target["confirmations"] = int(payload.get("confirmations") or 0)
            target["last_status_check"] = _utc_now()
            target["webhook_source"] = source
            target["webhook_payload"] = payload

            self._save()
            self._record_receipt(
                "payment_webhook_update",
                {
                    "payment_id": target.get("id"),
                    "intent_id": target.get("intent_id"),
                    "txid": target.get("txid"),
                    "status": target.get("tx_status"),
                    "confirmations": target.get("confirmations"),
                    "source": source,
                },
            )
            return {"updated": True, "payment": target}

    def maybe_autopay_hosting(self) -> dict:
        with self._state_lock():
            self._reload_from_disk()
            due = float(self.state["infra"]["accrued_hosting_due_usd"])
            threshold = float(self.state["infra"]["autopay_threshold_usd"])
            if due < threshold:
                return {"message": "below autopay threshold", "due_usd": round(due, 6)}
            return self.execute_hosting_payment(due, provider="akash", reason="autopay")

    def payments(self, limit: int = 20) -> list[dict]:
        with self._state_lock():
            self._reload_from_disk()
            return list(self.state["payments"][-limit:])

    def payment_intents(self, limit: int = 20) -> list[dict]:
        with self._state_lock():
            self._reload_from_disk()
            return list(self.state["payment_intents"][-limit:])

    def retry_jobs(self, limit: int = 20) -> list[dict]:
        if not self.receipts_db_path:
            return []
        conn = sqlite3.connect(self.receipts_db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, created_at, updated_at, status, payment_id, intent_id, reason,
                       attempts, max_attempts, next_attempt_at, last_error, dead_lettered_at, payload_json
                FROM payment_retry_jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "status": r["status"],
                    "payment_id": r["payment_id"],
                    "intent_id": r["intent_id"],
                    "reason": r["reason"],
                    "attempts": int(r["attempts"]),
                    "max_attempts": int(r["max_attempts"]),
                    "next_attempt_at": r["next_attempt_at"],
                    "last_error": r["last_error"],
                    "dead_lettered_at": r["dead_lettered_at"],
                    "payload": json.loads(r["payload_json"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def process_retry_jobs(self, max_jobs: int = 5) -> dict:
        if not self.receipts_db_path:
            return {"processed": 0, "results": []}
        now = _utc_now()
        conn = sqlite3.connect(self.receipts_db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            jobs = conn.execute(
                """
                SELECT id, intent_id, attempts, max_attempts
                FROM payment_retry_jobs
                WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id ASC
                LIMIT ?
                """,
                (now, max_jobs),
            ).fetchall()

            for job in jobs:
                conn.execute(
                    "UPDATE payment_retry_jobs SET status='in_progress', updated_at=? WHERE id=? AND status='pending'",
                    (_utc_now(), int(job["id"])),
                )
            conn.commit()

            results = []
            for job in jobs:
                job_id = int(job["id"])
                intent_id = job["intent_id"]
                attempts = int(job["attempts"]) + 1
                max_attempts = int(job["max_attempts"])
                if self.policy_service is not None:
                    p = self.policy_service.get().get("payments", {})
                    max_attempts = int(p.get("retry_max_attempts", max_attempts))
                    base_delay = int(p.get("retry_base_delay_seconds", 30))
                    max_delay = int(p.get("retry_max_delay_seconds", 1800))
                else:
                    base_delay = 30
                    max_delay = 1800

                if not intent_id:
                    conn.execute(
                        "UPDATE payment_retry_jobs SET status='dead_letter', attempts=?, updated_at=?, dead_lettered_at=? WHERE id=?",
                        (attempts, _utc_now(), _utc_now(), job_id),
                    )
                    results.append(
                        {
                            "job_id": job_id,
                            "status": "dead_letter",
                            "reason": "missing intent_id",
                        }
                    )
                    continue

                outcome = self.execute_payment_intent(intent_id)
                if "error" in outcome:
                    status = "dead_letter" if attempts >= max_attempts else "pending"
                    delay = min((2**attempts) * base_delay, max_delay)
                    conn.execute(
                        """
                        UPDATE payment_retry_jobs
                        SET status=?, attempts=?, updated_at=?, next_attempt_at=?, payload_json=?, last_error=?, dead_lettered_at=?
                        WHERE id=?
                        """,
                        (
                            status,
                            attempts,
                            _utc_now(),
                            _iso_after(delay),
                            json.dumps({"last_error": outcome}, ensure_ascii=True),
                            str(outcome.get("error", "retry failed"))[:500],
                            _utc_now() if status == "dead_letter" else None,
                            job_id,
                        ),
                    )
                    results.append(
                        {"job_id": job_id, "status": status, "outcome": outcome}
                    )
                else:
                    conn.execute(
                        "UPDATE payment_retry_jobs SET status='completed', attempts=?, updated_at=?, next_attempt_at=NULL WHERE id=?",
                        (attempts, _utc_now(), job_id),
                    )
                    results.append(
                        {"job_id": job_id, "status": "completed", "outcome": outcome}
                    )

            conn.commit()
            return {"processed": len(results), "results": results}
        finally:
            conn.close()

    def requeue_dead_letter_jobs(
        self, job_ids: list[int] | None = None, limit: int = 10
    ) -> dict:
        if not self.receipts_db_path:
            return {"requeued": 0, "ids": []}
        conn = sqlite3.connect(self.receipts_db_path)
        conn.row_factory = sqlite3.Row
        try:
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                rows = conn.execute(
                    f"SELECT id FROM payment_retry_jobs WHERE status='dead_letter' AND id IN ({placeholders})",
                    tuple(job_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM payment_retry_jobs WHERE status='dead_letter' ORDER BY id ASC LIMIT ?",
                    (limit,),
                ).fetchall()

            ids = [int(r["id"]) for r in rows]
            if not ids:
                return {"requeued": 0, "ids": []}

            for jid in ids:
                conn.execute(
                    """
                    UPDATE payment_retry_jobs
                    SET status='pending', updated_at=?, next_attempt_at=?, dead_lettered_at=NULL, last_error=NULL
                    WHERE id=?
                    """,
                    (_utc_now(), _iso_after(5), jid),
                )
            conn.commit()
            return {"requeued": len(ids), "ids": ids}
        finally:
            conn.close()

    def dismiss_dead_letter_jobs(
        self,
        *,
        job_ids: list[int] | None = None,
        limit: int = 10,
        note: str | None = None,
        actor: str = "admin",
    ) -> dict:
        if not self.receipts_db_path:
            return {"dismissed": 0, "ids": []}
        conn = sqlite3.connect(self.receipts_db_path)
        conn.row_factory = sqlite3.Row
        try:
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                rows = conn.execute(
                    f"SELECT id, payload_json FROM payment_retry_jobs WHERE status='dead_letter' AND id IN ({placeholders})",
                    tuple(job_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, payload_json FROM payment_retry_jobs WHERE status='dead_letter' ORDER BY id ASC LIMIT ?",
                    (limit,),
                ).fetchall()

            ids = [int(r["id"]) for r in rows]
            if not ids:
                return {"dismissed": 0, "ids": []}

            for row in rows:
                jid = int(row["id"])
                payload = json.loads(row["payload_json"])
                payload["triage"] = {
                    "action": "dismiss",
                    "actor": actor,
                    "note": note,
                    "at": _utc_now(),
                }
                conn.execute(
                    """
                    UPDATE payment_retry_jobs
                    SET status='dismissed', updated_at=?, payload_json=?
                    WHERE id=?
                    """,
                    (_utc_now(), json.dumps(payload, ensure_ascii=True), jid),
                )
            conn.commit()
            return {"dismissed": len(ids), "ids": ids}
        finally:
            conn.close()

    def consume_webhook_nonce(
        self, nonce: str, source: str, ttl_seconds: int = 3600
    ) -> bool:
        if not self.receipts_db_path:
            return False
        now = datetime.now(timezone.utc)
        cutoff = datetime.fromtimestamp(
            now.timestamp() - ttl_seconds, tz=timezone.utc
        ).isoformat()

        conn = sqlite3.connect(self.receipts_db_path)
        try:
            conn.execute("DELETE FROM webhook_nonces WHERE created_at < ?", (cutoff,))
            conn.execute(
                "INSERT INTO webhook_nonces(nonce, created_at, source) VALUES(?, ?, ?)",
                (nonce, _utc_now(), source),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
