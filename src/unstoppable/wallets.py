from __future__ import annotations

from dataclasses import dataclass

import requests


class WalletAdapterError(Exception):
    pass


@dataclass
class WalletSnapshot:
    balances_usd: dict[str, float]
    source: str


class WalletAdapter:
    def fetch_balances_usd(self) -> WalletSnapshot:
        raise NotImplementedError


class NoopWalletAdapter(WalletAdapter):
    def fetch_balances_usd(self) -> WalletSnapshot:
        return WalletSnapshot(balances_usd={}, source="noop")


class PublicApiWalletAdapter(WalletAdapter):
    def __init__(self, btc_address: str | None = None, timeout: int = 8) -> None:
        self.btc_address = btc_address
        self.timeout = timeout

    def _fetch_btc_balance(self, address: str) -> float:
        url = f"https://mempool.space/api/address/{address}"
        response = requests.get(url, timeout=self.timeout)
        if response.status_code != 200:
            raise WalletAdapterError(
                f"btc balance request failed: {response.status_code}"
            )
        data = response.json()
        chain = data.get("chain_stats", {})
        funded = int(chain.get("funded_txo_sum", 0))
        spent = int(chain.get("spent_txo_sum", 0))
        sats = max(funded - spent, 0)
        return sats / 100_000_000

    def _fetch_btc_price(self) -> float:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        response = requests.get(url, timeout=self.timeout)
        if response.status_code != 200:
            raise WalletAdapterError(f"price request failed: {response.status_code}")
        data = response.json()
        return float(data.get("bitcoin", {}).get("usd", 0.0))

    def fetch_balances_usd(self) -> WalletSnapshot:
        balances: dict[str, float] = {}
        if self.btc_address:
            btc = self._fetch_btc_balance(self.btc_address)
            price = self._fetch_btc_price()
            balances["btc"] = round(btc * price, 2)
        return WalletSnapshot(balances_usd=balances, source="public_api")
