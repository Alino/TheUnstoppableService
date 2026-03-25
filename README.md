# The Unstoppable Service

Phase 6 implementation scaffold for an autonomous, decentralized organic search service.

## Included Specs

- `specs/phase-0-mvp.md`
- `specs/phase-1-mvp.md`
- `specs/phase-2-mvp.md`
- `specs/phase-3-mvp.md`
- `specs/phase-4-mvp.md`
- `specs/phase-6-mvp.md`

## What You Can Test Now

- Distributed crawler/indexer/brain workers
- Persistent crawl queue
- Search API + UI + donation page
- Treasury + autopay simulation
- API key billing + contextual ads fallback
- Policy-guarded payment intents
- Pluggable payment execution adapters (mock or external command)
- Payment transaction status refresh endpoint
- Optional Elasticsearch backend with SQLite fallback

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Run Product

```bash
export UNSTOPPABLE_ADMIN_API_TOKEN="change-me-admin-token"
export UNSTOPPABLE_EXECUTOR_WEBHOOK_SECRET="change-me-webhook-secret"
python -m unstoppable.main run-phase2 --host 0.0.0.0 --port 8080
```

Open:

```bash
http://localhost:8080
http://localhost:8080/donate
```

## Core Checks

```bash
curl "http://localhost:8080/health"
curl "http://localhost:8080/stats"
curl "http://localhost:8080/queue/stats"
curl "http://localhost:8080/search?q=domain&limit=5"
curl "http://localhost:8080/treasury"
curl "http://localhost:8080/payments/history" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/payments/intents" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/payments/receipts" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/payments/retries" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/payments/retries/dead-letter" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/payments/receipts/export?format=jsonl&limit=200" -H "X-Admin-Token: change-me-admin-token"
curl "http://localhost:8080/policy" -H "X-Admin-Token: change-me-admin-token"
```

## Monetization APIs

```bash
curl -X POST "http://localhost:8080/apikeys/create" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"name":"demo","plan":"builder"}'
curl "http://localhost:8080/apikeys/list" -H "X-Admin-Token: change-me-admin-token"
curl -X POST "http://localhost:8080/revenue/config" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"ads_enabled":true,"max_ads_per_query":1}'
curl "http://localhost:8080/ads/preview?q=decentralized%20hosting"
```

Use API key on search:

```bash
curl "http://localhost:8080/search?q=domain&limit=5" -H "X-API-Key: <api-key>"
```

## Payment Intent Flow

```bash
curl -X POST "http://localhost:8080/payments/intents/create" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"amount_usd":12,"provider":"akash","reason":"manual"}'
curl -X POST "http://localhost:8080/payments/intents/execute" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"intent_id":"<intent-id>"}'
curl -X POST "http://localhost:8080/payments/refresh" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"payment_id":"<payment-id>"}'
curl -X POST "http://localhost:8080/payments/retries/process" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"max_jobs":5}'
curl -X POST "http://localhost:8080/payments/retries/requeue" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"limit":5}'
curl -X POST "http://localhost:8080/payments/retries/dismiss" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"limit":5,"note":"manual triage"}'
```

Idempotent create/execute calls:

```bash
curl -X POST "http://localhost:8080/payments/intents/create" -H "X-Admin-Token: change-me-admin-token" -H "X-Idempotency-Key: create-123" -H "Content-Type: application/json" -d '{"amount_usd":12,"provider":"akash","reason":"manual"}'
curl -X POST "http://localhost:8080/payments/intents/execute" -H "X-Admin-Token: change-me-admin-token" -H "X-Idempotency-Key: exec-123" -H "Content-Type: application/json" -d '{"intent_id":"<intent-id>"}'
```

Adjust policy:

```bash
curl -X POST "http://localhost:8080/policy" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"max_single_payment_usd":20,"allow_autopay":true}'
curl -X POST "http://localhost:8080/policy" -H "Content-Type: application/json" -H "X-Admin-Token: change-me-admin-token" -d '{"retry_max_attempts":7,"retry_base_delay_seconds":45,"retry_max_delay_seconds":2400}'
```

## Tests

```bash
pytest -q
```

## CI

GitHub Actions workflow is in `.github/workflows/ci.yml` and runs:

- dependency install
- `python -m compileall src`
- `pytest -q`

## Elasticsearch (Optional)

```bash
export UNSTOPPABLE_SEARCH_BACKEND=elasticsearch
export ELASTICSEARCH_URL="http://localhost:9200"
export ELASTICSEARCH_INDEX="unstoppable-pages"
python -m unstoppable.main run-phase2 --host 0.0.0.0 --port 8080
```

When ES is unavailable, search falls back to SQLite automatically.

## External Payment Executor (Optional)

Default mode is `mock` signer.

```bash
export UNSTOPPABLE_PAYMENT_EXECUTOR_MODE=command
export UNSTOPPABLE_PAYMENT_EXECUTOR_CMD="python scripts/payment_executor_example.py"
export UNSTOPPABLE_EXECUTOR_WEBHOOK_SECRET="change-me-webhook-secret"
```

The command receives JSON on stdin with `{ intent, payment }` and must output JSON:

```json
{"txid":"<chain-txid>","status":"submitted","signer":"executor","meta":{"network":"bitcoin"}}
```

Executor webhook callback (signed):

```bash
payload='{"payment_id":"<payment-id>","txid":"<txid>","status":"confirmed","confirmations":3}'
export PAYLOAD="$payload"
nonce="nonce-$(date +%s)"
ts="$(date +%s)"
export TS="$ts"
export NONCE="$nonce"
sig=$(python - <<'PY'
import hmac,hashlib,os
body=(os.environ['TS']+'.'+os.environ['NONCE']+'.').encode()+os.environ['PAYLOAD'].encode()
secret='change-me-webhook-secret'.encode()
print(hmac.new(secret, body, hashlib.sha256).hexdigest())
PY
)
curl -X POST "http://localhost:8080/payments/webhook/executor" -H "Content-Type: application/json" -H "X-Executor-Signature: $sig" -H "X-Executor-Timestamp: $ts" -H "X-Executor-Nonce: $nonce" -d "$payload"
```
