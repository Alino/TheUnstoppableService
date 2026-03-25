from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_POLICY = {
    "payments": {
        "enabled": True,
        "max_single_payment_usd": 50.0,
        "max_daily_payment_usd": 150.0,
        "min_treasury_buffer_usd": 25.0,
        "allow_autopay": True,
        "retry_max_attempts": 5,
        "retry_base_delay_seconds": 30,
        "retry_max_delay_seconds": 1800,
    }
}


class PolicyService:
    def __init__(self, config_file: Path) -> None:
        self.config_file = config_file
        self.config = self._load_or_create()

    def _load_or_create(self) -> dict:
        if not self.config_file.exists():
            self.config_file.write_text(json.dumps(DEFAULT_POLICY, indent=2))
            return json.loads(json.dumps(DEFAULT_POLICY))

        raw = json.loads(self.config_file.read_text())
        merged = json.loads(json.dumps(DEFAULT_POLICY))
        merged["payments"] = {**merged.get("payments", {}), **raw.get("payments", {})}
        if merged != raw:
            self.config_file.write_text(json.dumps(merged, indent=2))
        return merged

    def get(self) -> dict:
        self.config = self._load_or_create()
        return self.config

    def update(self, patch: dict) -> dict:
        current = self.get()
        if "payments" in patch and isinstance(patch["payments"], dict):
            current["payments"] = {**current["payments"], **patch["payments"]}
        self.config = current
        self.config_file.write_text(json.dumps(current, indent=2))
        return current

    def evaluate_payment(
        self,
        amount_usd: float,
        treasury_usd: float,
        paid_today_usd: float,
        reason: str,
    ) -> dict:
        cfg = self.get().get("payments", {})
        if not bool(cfg.get("enabled", True)):
            return {"allowed": False, "reason": "payments disabled by policy"}

        if reason == "autopay" and not bool(cfg.get("allow_autopay", True)):
            return {"allowed": False, "reason": "autopay disabled by policy"}

        max_single = float(cfg.get("max_single_payment_usd", 50.0))
        if amount_usd > max_single:
            return {
                "allowed": False,
                "reason": "payment exceeds max_single_payment_usd",
            }

        max_daily = float(cfg.get("max_daily_payment_usd", 150.0))
        if paid_today_usd + amount_usd > max_daily:
            return {"allowed": False, "reason": "payment exceeds max_daily_payment_usd"}

        min_buffer = float(cfg.get("min_treasury_buffer_usd", 25.0))
        if treasury_usd - amount_usd < min_buffer:
            return {
                "allowed": False,
                "reason": "payment violates min_treasury_buffer_usd",
            }

        return {
            "allowed": True,
            "reason": "policy approved",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
