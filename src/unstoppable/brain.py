from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BrainDecision:
    treasury_usd: float
    net_burn_usd: float
    runway_months: float
    mode: str
    recommendation: str


def _mode_from_runway(runway_months: float) -> str:
    if runway_months > 12:
        return "growth"
    if runway_months >= 3:
        return "stable"
    if runway_months >= 1:
        return "conservation"
    return "survival"


def evaluate_state(state: dict) -> BrainDecision:
    balances_usd = state.get("balances_usd", {})
    treasury_usd = float(sum(float(v) for v in balances_usd.values()))
    monthly_burn = float(state.get("monthly_burn_usd", 0.0))
    monthly_income = float(state.get("monthly_donation_income_usd", 0.0))

    net_burn = max(monthly_burn - monthly_income, 0.01)
    runway = treasury_usd / net_burn
    mode = _mode_from_runway(runway)

    recommendations = {
        "growth": "Expand crawl coverage and invest in index freshness.",
        "stable": "Maintain current footprint and monitor donation trends.",
        "conservation": "Reduce crawler concurrency and increase donation prompts.",
        "survival": "Run minimum service profile and prepare fallback revenue mode.",
    }

    return BrainDecision(
        treasury_usd=round(treasury_usd, 2),
        net_burn_usd=round(net_burn, 2),
        runway_months=round(runway, 2),
        mode=mode,
        recommendation=recommendations[mode],
    )


def evaluate_once(state_file: Path) -> BrainDecision:
    state = json.loads(state_file.read_text())
    return evaluate_state(state)
