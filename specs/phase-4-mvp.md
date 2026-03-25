# Phase 4 MVP Spec

Phase 4 introduces guarded autonomous payments and backend scaling controls.

## Objectives

1. Add payment policy guardrails for autonomous treasury execution.
2. Introduce payment intents (create -> approve/reject -> execute) flow.
3. Add optional Elasticsearch backend for scalable search/query operations.

## Implemented

- `policy.py`
  - payment limits and treasury buffer enforcement
  - configurable policy values persisted to `policy_config.json`
- `treasury.py`
  - payment intents ledger
  - policy checks on payment intent creation
  - mock signed execution path with txid simulation
  - atomic state file writes and retry reads for multi-worker safety
- `search_backend.py`
  - backend abstraction: sqlite or elasticsearch
  - fallback to sqlite if ES fails
  - ES sync support via bulk indexing
- `search_api.py`
  - policy endpoints
  - payment intents endpoints
  - search response includes backend used

## New Endpoints

- `GET /policy`
- `POST /policy`
- `GET /payments/intents`
- `POST /payments/intents/create`
- `POST /payments/intents/execute`

## Search Backend Selection

- `UNSTOPPABLE_SEARCH_BACKEND=sqlite` (default)
- `UNSTOPPABLE_SEARCH_BACKEND=elasticsearch`
- `ELASTICSEARCH_URL` and `ELASTICSEARCH_INDEX` used when ES mode is enabled

## Notes

- Payment signing is still simulation grade (`mock-signer`) but now passes through explicit intent/policy flow.
- Policy blocks dangerous autopay behavior if limits are exceeded.
