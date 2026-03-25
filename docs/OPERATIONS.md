# Operations Notes

This file contains implementation-level API and operations references. The project narrative and rationale are in `specs/whitepaper.md`.

## Admin Header

Protected endpoints require:

- `X-Admin-Token: <UNSTOPPABLE_ADMIN_API_TOKEN>`

## Core Admin Endpoints

- `GET /policy`
- `POST /policy`
- `GET /payments/history`
- `GET /payments/intents`
- `POST /payments/intents/create`
- `POST /payments/intents/execute`
- `POST /payments/refresh`
- `GET /payments/retries`
- `POST /payments/retries/process`
- `GET /payments/retries/dead-letter`
- `POST /payments/retries/requeue`
- `POST /payments/retries/dismiss`
- `GET /payments/receipts`
- `GET /payments/receipts/export?format=jsonl|csv`

## Tests

```bash
pytest -q
```
