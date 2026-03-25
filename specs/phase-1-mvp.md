# Phase 1 MVP Spec

Phase 1 adds treasury and payment autonomy to the Phase 0 product.

## Objectives

1. Maintain a persistent treasury state across cycles.
2. Track accrued hosting costs and execute periodic payments.
3. Expose donation/payment actions via API.
4. Support optional read-only wallet sync from public BTC address APIs.

## Implemented Components

- `treasury.py`
  - Persistent balances and infra due tracking
  - Donation event ledger
  - Payment ledger
  - Auto-swap simulation from BTC/XMR/ZEC into USDC
  - Autopay trigger based on due threshold
- `wallets.py`
  - `NoopWalletAdapter`
  - `PublicApiWalletAdapter` (BTC only)
- `autonomy.py`
  - Cycle now performs wallet sync, crawling/indexing, cost accrual, and autopay
- `search_api.py`
  - Treasury endpoints
  - Payment endpoints
  - UI controls for simulated donation and immediate payment

## Endpoints Added

- `GET /treasury`
- `POST /treasury/donate`
- `GET /payments/history`
- `POST /payments/pay-now`

## Known Limits

- On-chain payment execution is simulated, not signed on-chain transactions yet.
- Wallet sync currently supports BTC public address only.
- XMR/ZEC live adapters are future work.
