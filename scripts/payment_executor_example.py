#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"error": "missing stdin payload"}))
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"error": "invalid JSON payload"}))
        return 1

    intent = payload.get("intent", {})
    provider = intent.get("provider", "unknown")
    intent_id = intent.get("id", "no-intent")

    result = {
        "txid": f"ext-{provider}-{intent_id}-{int(time.time())}",
        "status": "submitted",
        "signer": "example-command-executor",
        "meta": {"network": "bitcoin", "mode": "example"},
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
