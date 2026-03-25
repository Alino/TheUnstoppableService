# The Unstoppable Service

This repository contains a draft prototype of an autonomous, decentralized search service.

If you read one document first, read the whitepaper:

- `specs/whitepaper.md`

## What This Repo Is

- A draft implementation of ideas described in the whitepaper
- A staged implementation (Phase 0 through Phase 6)
- A local prototype stack with crawler, indexer, API, treasury, and policy controls

## Canonical Project Docs

- Vision and architecture: `specs/whitepaper.md`
- Build progression:
  - `specs/phase-0-mvp.md`
  - `specs/phase-1-mvp.md`
  - `specs/phase-2-mvp.md`
  - `specs/phase-3-mvp.md`
  - `specs/phase-4-mvp.md`
  - `specs/phase-6-mvp.md`

## Quick Start

1. Install and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

export UNSTOPPABLE_ADMIN_API_TOKEN="replace-with-strong-token"
export UNSTOPPABLE_EXECUTOR_WEBHOOK_SECRET="replace-with-strong-secret"

python -m unstoppable.main run-phase2 --host 0.0.0.0 --port 8080
```

2. Open:

- `http://localhost:8080`
- `http://localhost:8080/donate`

## Verify

```bash
pytest -q
```

## Notes

- Default search backend is SQLite FTS.
- Elasticsearch can be enabled via env vars (`UNSTOPPABLE_SEARCH_BACKEND=elasticsearch`).
- Admin and webhook secrets are required at runtime.
- Billing-gated `GET /search` may return HTTP `402 Payment Required` with x402-style metadata (see `docs/OPERATIONS.md`).
