from __future__ import annotations

import json
import re
from pathlib import Path


DEFAULT_REVENUE_CONFIG = {
    "ads": {
        "enabled": False,
        "fallback_enabled": True,
        "activate_if_runway_below_months": 1.5,
        "max_ads_per_query": 2,
    },
    "catalog": [
        {
            "id": "ad-privacy-wallet",
            "title": "Private Crypto Wallet",
            "url": "https://example.com/privacy-wallet",
            "description": "Self-custody wallet built for private payments.",
            "keywords": ["wallet", "bitcoin", "monero", "zcash", "crypto"],
            "bid_usd": 0.02,
        },
        {
            "id": "ad-decentralized-vps",
            "title": "Decentralized Cloud Compute",
            "url": "https://example.com/decentralized-cloud",
            "description": "Deploy global workloads on community hardware.",
            "keywords": ["server", "cloud", "hosting", "compute", "decentralized"],
            "bid_usd": 0.03,
        },
        {
            "id": "ad-open-source-search",
            "title": "Open Source Search Toolkit",
            "url": "https://example.com/open-search",
            "description": "Build independent search products with transparent ranking.",
            "keywords": ["search", "ranking", "index", "crawler", "opensource"],
            "bid_usd": 0.015,
        },
    ],
}


class RevenueService:
    def __init__(self, config_file: Path) -> None:
        self.config_file = config_file
        self.config = self._load_or_create()

    def _load_or_create(self) -> dict:
        if not self.config_file.exists():
            self.config_file.write_text(json.dumps(DEFAULT_REVENUE_CONFIG, indent=2))
            return json.loads(json.dumps(DEFAULT_REVENUE_CONFIG))
        raw = json.loads(self.config_file.read_text())
        merged = json.loads(json.dumps(DEFAULT_REVENUE_CONFIG))
        merged["ads"] = {**merged.get("ads", {}), **raw.get("ads", {})}
        if isinstance(raw.get("catalog"), list) and raw["catalog"]:
            merged["catalog"] = raw["catalog"]
        if merged != raw:
            self.config_file.write_text(json.dumps(merged, indent=2))
        return merged

    def _save(self) -> None:
        self.config_file.write_text(json.dumps(self.config, indent=2))

    def get_config(self) -> dict:
        self.config = self._load_or_create()
        return self.config

    def update_config(self, patch: dict) -> dict:
        current = self.get_config()
        if "ads" in patch and isinstance(patch["ads"], dict):
            current["ads"] = {**current["ads"], **patch["ads"]}
        if "catalog" in patch and isinstance(patch["catalog"], list):
            current["catalog"] = patch["catalog"]
        self.config = current
        self._save()
        return self.config

    def ads_active(self, runway_months: float | None) -> bool:
        cfg = self.get_config().get("ads", {})
        if bool(cfg.get("enabled", False)):
            return True
        if not bool(cfg.get("fallback_enabled", True)):
            return False
        if runway_months is None:
            return False
        threshold = float(cfg.get("activate_if_runway_below_months", 1.5))
        return runway_months < threshold

    def select_ads(self, query: str, limit: int | None = None) -> list[dict]:
        cfg = self.get_config()
        max_ads = int(cfg.get("ads", {}).get("max_ads_per_query", 2))
        if limit is None:
            limit = max_ads

        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for ad in cfg.get("catalog", []):
            keywords = {k.lower() for k in ad.get("keywords", [])}
            overlap = len(terms.intersection(keywords))
            if overlap <= 0:
                continue
            bid = float(ad.get("bid_usd", 0.0))
            score = overlap * 1000 + bid
            scored.append((score, ad))

        scored.sort(reverse=True, key=lambda item: item[0])
        selected = []
        for _, ad in scored[: max(0, limit)]:
            selected.append(
                {
                    "id": ad.get("id"),
                    "title": ad.get("title"),
                    "url": ad.get("url"),
                    "description": ad.get("description"),
                    "sponsored": True,
                }
            )
        return selected
