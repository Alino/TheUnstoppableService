# Phase 2 MVP Spec

Phase 2 introduces distributed workers and a persistent crawl queue.

## Goals

1. Split runtime into independent services: API, crawler worker, indexer worker, brain worker.
2. Move crawling from in-memory queue to persistent queue in SQLite.
3. Keep product testable locally with a one-command stack mode.

## New Components

- `queue.py`
  - persistent URL queue (pending/in_progress/done/failed)
  - claim, complete, fail, and queue stats helpers
- `crawler.py`
  - `crawl_queue_batch` for worker-based crawling
- `services.py`
  - long-running worker loops for crawler, indexer, and brain
- `main.py`
  - new commands:
    - `run-crawler-worker`
    - `run-indexer-worker`
    - `run-brain-worker`
    - `run-phase2`

## Local Test Modes

- Single process stack:
  - `python -m unstoppable.main run-phase2`
- Multi-container stack:
  - `docker compose -f docker-compose.phase2.yml up --build`

## Akash Artifacts

- `deploy/akash/phase2-multiservice.sdl`

## Current Limitation

- Phase 2 still uses SQLite for shared state. For production decentralized deployment, replace with a network-accessible state backend (e.g., Postgres-compatible service, distributed KV, or object-store-backed queue/index).
