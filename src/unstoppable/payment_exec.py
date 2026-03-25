from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass

import requests

from unstoppable.config import BTC_MEMPOOL_API


@dataclass
class ExecutionResult:
    txid: str
    status: str
    signer: str
    meta: dict


class PaymentExecutionError(Exception):
    pass


class PaymentExecutor:
    def execute(self, intent: dict, payment: dict) -> ExecutionResult:
        raise NotImplementedError

    def tx_status(self, txid: str) -> dict:
        return {"status": "unknown", "txid": txid}


class MockPaymentExecutor(PaymentExecutor):
    def execute(self, intent: dict, payment: dict) -> ExecutionResult:
        intent_id = str(intent.get("id", "no-intent"))
        return ExecutionResult(
            txid=f"sim-{intent_id}",
            status="submitted",
            signer="mock-signer",
            meta={"mode": "mock"},
        )


class CommandPaymentExecutor(PaymentExecutor):
    def __init__(self, command: str) -> None:
        self.command = command.strip()
        if not self.command:
            raise PaymentExecutionError("command executor requires command")

    def execute(self, intent: dict, payment: dict) -> ExecutionResult:
        payload = json.dumps({"intent": intent, "payment": payment})
        proc = subprocess.run(
            shlex.split(self.command),
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if proc.returncode != 0:
            raise PaymentExecutionError(
                f"executor command failed rc={proc.returncode} stderr={proc.stderr.strip()}"
            )

        try:
            out = json.loads(proc.stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise PaymentExecutionError("executor output is not valid JSON") from exc

        txid = str(out.get("txid", "")).strip()
        status = str(out.get("status", "submitted")).strip() or "submitted"
        signer = (
            str(out.get("signer", "command-executor")).strip() or "command-executor"
        )
        if not txid:
            raise PaymentExecutionError("executor output missing txid")

        meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
        return ExecutionResult(txid=txid, status=status, signer=signer, meta=meta)

    def tx_status(self, txid: str) -> dict:
        return {"status": "external", "txid": txid}


class BitcoinMempoolStatus:
    def __init__(self, base_url: str | None = None, timeout: int = 8) -> None:
        self.base_url = (base_url or BTC_MEMPOOL_API).rstrip("/")
        self.timeout = timeout

    def fetch(self, txid: str) -> dict:
        url = f"{self.base_url}/tx/{txid}"
        res = requests.get(url, timeout=self.timeout)
        if res.status_code == 404:
            return {
                "txid": txid,
                "status": "not_found",
                "confirmed": False,
                "confirmations": 0,
            }
        res.raise_for_status()
        data = res.json()
        confirmed = bool(data.get("status", {}).get("confirmed", False))
        block_height = data.get("status", {}).get("block_height")

        conf = 0
        if confirmed and block_height:
            tip = requests.get(
                f"{self.base_url}/blocks/tip/height", timeout=self.timeout
            )
            tip.raise_for_status()
            tip_height = int(tip.text.strip())
            conf = max(tip_height - int(block_height) + 1, 1)

        return {
            "txid": txid,
            "status": "confirmed" if confirmed else "pending",
            "confirmed": confirmed,
            "confirmations": conf,
        }


class HybridPaymentExecutor(PaymentExecutor):
    def __init__(
        self, executor: PaymentExecutor, btc_status: BitcoinMempoolStatus | None = None
    ) -> None:
        self.executor = executor
        self.btc_status = btc_status or BitcoinMempoolStatus()

    def execute(self, intent: dict, payment: dict) -> ExecutionResult:
        return self.executor.execute(intent, payment)

    def tx_status(self, txid: str) -> dict:
        if txid.startswith("sim-"):
            return {"txid": txid, "status": "simulated", "confirmations": 0}
        try:
            return self.btc_status.fetch(txid)
        except Exception as exc:
            return {
                "txid": txid,
                "status": "error",
                "error": str(exc),
                "confirmations": 0,
            }
