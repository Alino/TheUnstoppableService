# Phase 6 MVP Spec

Phase 6 adds pluggable payment execution and transaction status tracking.

## Goals

1. Decouple payment execution from treasury logic.
2. Support external signer/executor integration without changing core APIs.
3. Track transaction status after payment execution.
4. Persist payment execution receipts in durable store.
5. Add replay-safe webhook updates and retry queue for failed execution.
6. Add idempotency protection for payment intent create/execute APIs.
7. Add dead-letter triage lifecycle and audit export.

## Implemented

- `payment_exec.py`
  - `PaymentExecutor` interface
  - `MockPaymentExecutor`
  - `CommandPaymentExecutor` (executes external command with JSON input/output)
  - `BitcoinMempoolStatus` status provider
  - `HybridPaymentExecutor` (execution + tx status lookup)
- `runtime.py`
  - `build_payment_executor()` factory
  - controlled by env vars:
    - `UNSTOPPABLE_PAYMENT_EXECUTOR_MODE`
    - `UNSTOPPABLE_PAYMENT_EXECUTOR_CMD`
- `treasury.py`
  - payment execution delegated through executor interface
  - payment records now include:
    - `txid`
    - `tx_status`
    - `confirmations`
    - `last_status_check`
    - `executor_meta`
  - status refresh function: `refresh_payment_status`
  - durable payment receipt recording into SQLite
- `search_api.py`
  - new endpoint: `POST /payments/refresh`
  - webhook endpoint: `POST /payments/webhook/executor`
  - receipt endpoint: `GET /payments/receipts`
  - retry endpoints: `GET /payments/retries`, `POST /payments/retries/process`
  - dead-letter endpoints: `GET /payments/retries/dead-letter`, `POST /payments/retries/requeue`
  - idempotent handling via `X-Idempotency-Key` for payment intent create/execute
  - receipt export endpoint (`jsonl`/`csv`)
  - dead-letter dismiss endpoint

## API Additions

- `POST /payments/refresh`
  - optionally refresh one payment by `payment_id`
  - refreshes tx status and confirmations
- `POST /payments/webhook/executor`
  - signed webhook callback for executor-reported status updates
  - verifies `X-Executor-Signature` over `<timestamp>.<body>`
  - requires `X-Executor-Timestamp` and `X-Executor-Nonce`
  - nonce uniqueness check prevents replay
- `GET /payments/receipts`
  - returns durable execution/status receipt trail
- `GET /payments/retries`
  - inspect failed execution retries
- `POST /payments/retries/process`
  - process due retry jobs
- `GET /payments/retries/dead-letter`
  - inspect dead-letter retry jobs
- `POST /payments/retries/requeue`
  - move dead-letter jobs back to pending
- `POST /payments/retries/dismiss`
  - mark dead-letter jobs as dismissed with optional note
- `GET /payments/receipts/export?format=jsonl|csv`
  - export receipt trail for external analysis/audit

## Execution Modes

- `mock` (default): deterministic simulated txids (`sim-...`)
- `command`: external command signs/broadcasts and returns txid payload

## External Executor Contract

Input (stdin):

```json
{"intent": {...}, "payment": {...}}
```

Output (stdout):

```json
{"txid":"...","status":"submitted","signer":"executor","meta":{}}
```

## Notes

- This phase keeps settlement simulation in place by default.
- Command mode enables bridge to real signing infrastructure.
- Further hardening should include signed callback verification and durable tx receipt store.
