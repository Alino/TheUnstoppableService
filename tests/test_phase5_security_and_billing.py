from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import unstoppable.auth as auth
import unstoppable.search_api as search_api
from unstoppable.autonomy import AutonomyController
from unstoppable.monetization import RevenueService
from unstoppable.policy import PolicyService
from unstoppable.storage import connect, init_schema
from unstoppable.treasury import TreasuryService
from unstoppable.webhook import compute_timed_signature


ADMIN_TOKEN = "phase5-test-token"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _bootstrap_client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "test.db"
    seed_path = tmp_path / "seeds.txt"
    seed_path.write_text("https://example.com\n")

    treasury_path = tmp_path / "treasury_state.json"
    _write_json(
        treasury_path,
        {
            "balances_usd": {"btc": 120.0, "xmr": 30.0, "zec": 10.0, "usdc": 90.0},
            "monthly_burn_usd": 110.0,
            "monthly_donation_income_usd": 45.0,
            "infra": {
                "monthly_target_cost_usd": 110.0,
                "accrued_hosting_due_usd": 0.0,
                "autopay_threshold_usd": 10.0,
            },
            "donations": [],
            "payments": [],
            "swaps": [],
            "payment_intents": [],
            "wallet_sync": {
                "enabled": False,
                "source": "none",
                "last_sync": None,
                "last_error": None,
            },
        },
    )

    revenue_path = tmp_path / "revenue_config.json"
    _write_json(
        revenue_path,
        {
            "ads": {
                "enabled": False,
                "fallback_enabled": True,
                "activate_if_runway_below_months": 1.5,
                "max_ads_per_query": 2,
            },
            "catalog": [],
        },
    )

    policy_path = tmp_path / "policy_config.json"
    _write_json(
        policy_path,
        {
            "payments": {
                "enabled": True,
                "max_single_payment_usd": 50.0,
                "max_daily_payment_usd": 150.0,
                "min_treasury_buffer_usd": 25.0,
                "allow_autopay": True,
            }
        },
    )

    conn = connect(db_path)
    init_schema(conn)
    conn.close()

    search_api.DB_PATH = db_path
    auth.ADMIN_API_TOKEN = ADMIN_TOKEN

    policy = PolicyService(policy_path)
    controller = AutonomyController(
        db_path=db_path,
        seed_file=seed_path,
        treasury_state_file=treasury_path,
        interval_seconds=10,
        max_pages_per_cycle=1,
        delay_seconds=0.0,
        policy_service=policy,
        receipts_db_path=db_path,
    )
    search_api.app.state.controller = controller
    search_api.app.state.policy = policy
    search_api.app.state.treasury_service = TreasuryService(
        treasury_path,
        policy_service=policy,
        receipts_db_path=db_path,
    )
    search_api.app.state.revenue = RevenueService(revenue_path)
    return TestClient(search_api.app)


def test_admin_token_required_for_sensitive_endpoints(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    no_token = client.get("/policy")
    assert no_token.status_code == 401

    ok = client.get("/policy", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert ok.status_code == 200
    assert "payments" in ok.json()


def test_api_key_billing_and_usage_tracking(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    created = client.post(
        "/apikeys/create",
        headers=headers,
        json={"name": "test-billing", "plan": "builder"},
    )
    assert created.status_code == 200
    api_key = created.json()["created"]["api_key"]

    search = client.get(
        "/search", params={"q": "domain", "limit": 2}, headers={"X-API-Key": api_key}
    )
    assert search.status_code == 200
    billing = search.json()["billing"]
    assert billing["plan"] == "builder"
    assert billing["charged_usd"] > 0

    usage = client.get("/apikeys/usage", params={"api_key": api_key}, headers=headers)
    assert usage.status_code == 200
    assert usage.json()["daily"][0]["queries"] >= 1


def test_search_payment_required_returns_x402_hints(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)

    blocked = client.get(
        "/search", params={"q": "domain", "limit": 2}, headers={"X-API-Key": "bad-key"}
    )
    assert blocked.status_code == 402
    assert blocked.headers["www-authenticate"] == "x402"
    assert blocked.headers["x-402-topup-endpoint"] == "/apikeys/topup"
    assert (
        blocked.headers["x-402-payment-intent-endpoint"] == "/payments/intents/create"
    )

    payload = blocked.json()["detail"]
    assert payload["error"] == "invalid api key"
    assert payload["x402"]["accepts"][0]["scheme"] == "api-key-credit"


def test_policy_blocks_large_payment_intent(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    updated = client.post(
        "/policy", headers=headers, json={"max_single_payment_usd": 1.0}
    )
    assert updated.status_code == 200

    intent = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 5.0, "provider": "akash", "reason": "manual"},
    )
    assert intent.status_code == 200
    payload = intent.json()["intent"]
    assert payload["status"] == "rejected"

    executed = client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": payload["id"]},
    )
    assert executed.status_code == 200
    assert "error" in executed.json()["result"]


def test_payment_intent_execution_and_refresh(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    intent = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 3.0, "provider": "akash", "reason": "manual"},
    )
    assert intent.status_code == 200
    intent_id = intent.json()["intent"]["id"]

    executed = client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": intent_id},
    )
    assert executed.status_code == 200
    payment = executed.json()["result"]
    assert payment["txid"].startswith("sim-")
    assert payment["tx_status"] == "submitted"

    refreshed = client.post(
        "/payments/refresh",
        headers=headers,
        json={"payment_id": payment["id"]},
    )
    assert refreshed.status_code == 200
    payload = refreshed.json()["refresh"]
    assert payload["updated"] == 1


def test_webhook_signature_and_receipt_persistence(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    created = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 2.0, "provider": "akash", "reason": "manual"},
    )
    intent_id = created.json()["intent"]["id"]
    executed = client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": intent_id},
    )
    payment_id = executed.json()["result"]["id"]
    txid = executed.json()["result"]["txid"]

    payload = {
        "payment_id": payment_id,
        "txid": txid,
        "status": "confirmed",
        "confirmations": 3,
    }
    body = json.dumps(payload).encode("utf-8")

    import time

    ts = str(int(time.time()))
    nonce = "nonce-1"

    bad = client.post(
        "/payments/webhook/executor",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Executor-Signature": "bad",
            "X-Executor-Timestamp": ts,
            "X-Executor-Nonce": nonce,
        },
    )
    assert bad.status_code == 401

    sig = compute_timed_signature("change-me-webhook-secret", ts, nonce, body)
    good = client.post(
        "/payments/webhook/executor",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Executor-Signature": sig,
            "X-Executor-Timestamp": ts,
            "X-Executor-Nonce": nonce,
        },
    )
    assert good.status_code == 200
    assert good.json()["result"]["updated"] is True

    replay = client.post(
        "/payments/webhook/executor",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Executor-Signature": sig,
            "X-Executor-Timestamp": ts,
            "X-Executor-Nonce": nonce,
        },
    )
    assert replay.status_code == 409

    receipts = client.get("/payments/receipts", headers=headers)
    assert receipts.status_code == 200
    assert receipts.json()["count"] >= 2


def test_retry_jobs_are_created_on_execution_failure(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    # drain treasury to force insufficient balance retry path
    t = search_api.app.state.treasury_service
    t.state["balances_usd"]["usdc"] = 0.0
    t.state["balances_usd"]["btc"] = 0.0
    t.state["balances_usd"]["xmr"] = 0.0
    t.state["balances_usd"]["zec"] = 0.0
    t.state["balances_usd"]["other"] = 100.0
    t._save()

    intent = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 5.0, "provider": "akash", "reason": "manual"},
    )
    intent_id = intent.json()["intent"]["id"]

    executed = client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": intent_id},
    )
    assert executed.status_code == 200
    assert "error" in executed.json()["result"]

    jobs = client.get("/payments/retries", headers=headers)
    assert jobs.status_code == 200
    assert jobs.json()["count"] >= 1


def test_idempotency_key_prevents_duplicate_intent_and_execute(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {
        "X-Admin-Token": ADMIN_TOKEN,
        "X-Idempotency-Key": "idem-intent-1",
    }

    first = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 1.5, "provider": "akash", "reason": "manual"},
    )
    second = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 1.5, "provider": "akash", "reason": "manual"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["intent"]["id"] == second.json()["intent"]["id"]

    intent_id = first.json()["intent"]["id"]
    exec_headers = {
        "X-Admin-Token": ADMIN_TOKEN,
        "X-Idempotency-Key": "idem-exec-1",
    }
    e1 = client.post(
        "/payments/intents/execute",
        headers=exec_headers,
        json={"intent_id": intent_id},
    )
    e2 = client.post(
        "/payments/intents/execute",
        headers=exec_headers,
        json={"intent_id": intent_id},
    )
    assert e1.status_code == 200
    assert e2.status_code == 200
    assert e1.json()["result"]["id"] == e2.json()["result"]["id"]


def test_dead_letter_and_requeue_flow(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    t = search_api.app.state.treasury_service
    t.state["balances_usd"]["usdc"] = 0.0
    t.state["balances_usd"]["btc"] = 0.0
    t.state["balances_usd"]["xmr"] = 0.0
    t.state["balances_usd"]["zec"] = 0.0
    t.state["balances_usd"]["other"] = 100.0
    t._save()

    intent = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 6.0, "provider": "akash", "reason": "manual"},
    )
    intent_id = intent.json()["intent"]["id"]
    client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": intent_id},
    )

    conn = connect(search_api.DB_PATH)
    conn.execute(
        "UPDATE payment_retry_jobs SET attempts=4, max_attempts=5, next_attempt_at='2000-01-01T00:00:00+00:00'"
    )
    conn.commit()
    conn.close()

    processed = client.post(
        "/payments/retries/process", headers=headers, json={"max_jobs": 10}
    )
    assert processed.status_code == 200

    dl = client.get("/payments/retries/dead-letter", headers=headers)
    assert dl.status_code == 200
    assert dl.json()["count"] >= 1
    dead_id = dl.json()["jobs"][0]["id"]

    rq = client.post(
        "/payments/retries/requeue",
        headers=headers,
        json={"job_ids": [dead_id], "limit": 1},
    )
    assert rq.status_code == 200
    assert rq.json()["result"]["requeued"] == 1


def test_dead_letter_dismiss_flow(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    t = search_api.app.state.treasury_service
    t.state["balances_usd"]["usdc"] = 0.0
    t.state["balances_usd"]["btc"] = 0.0
    t.state["balances_usd"]["xmr"] = 0.0
    t.state["balances_usd"]["zec"] = 0.0
    t.state["balances_usd"]["other"] = 100.0
    t._save()

    intent = client.post(
        "/payments/intents/create",
        headers=headers,
        json={"amount_usd": 6.0, "provider": "akash", "reason": "manual"},
    )
    intent_id = intent.json()["intent"]["id"]
    client.post(
        "/payments/intents/execute",
        headers=headers,
        json={"intent_id": intent_id},
    )

    conn = connect(search_api.DB_PATH)
    conn.execute(
        "UPDATE payment_retry_jobs SET attempts=4, max_attempts=5, next_attempt_at='2000-01-01T00:00:00+00:00'"
    )
    conn.commit()
    conn.close()

    client.post("/payments/retries/process", headers=headers, json={"max_jobs": 10})
    dl = client.get("/payments/retries/dead-letter", headers=headers)
    assert dl.json()["count"] >= 1
    dead_id = dl.json()["jobs"][0]["id"]

    dismissed = client.post(
        "/payments/retries/dismiss",
        headers=headers,
        json={"job_ids": [dead_id], "limit": 1, "note": "manual triage dismiss"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["result"]["dismissed"] == 1


def test_receipts_export_and_retry_policy_patch(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    patched = client.post(
        "/policy",
        headers=headers,
        json={
            "retry_max_attempts": 7,
            "retry_base_delay_seconds": 45,
            "retry_max_delay_seconds": 2400,
        },
    )
    assert patched.status_code == 200
    payments_cfg = patched.json()["payments"]
    assert payments_cfg["retry_max_attempts"] == 7

    export_jsonl = client.get(
        "/payments/receipts/export",
        params={"format": "jsonl", "limit": 10},
        headers=headers,
    )
    assert export_jsonl.status_code == 200

    export_csv = client.get(
        "/payments/receipts/export",
        params={"format": "csv", "limit": 10},
        headers=headers,
    )
    assert export_csv.status_code == 200
    assert (
        "recorded_at,kind,intent_id,payment_id,txid,status,payload_json"
        in export_csv.text
    )


def test_webhook_rejects_stale_timestamp(tmp_path: Path) -> None:
    client = _bootstrap_client(tmp_path)
    payload = {
        "payment_id": "p",
        "txid": "t",
        "status": "confirmed",
        "confirmations": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    old_ts = "1"
    sig = compute_timed_signature(
        "change-me-webhook-secret", old_ts, "nonce-stale", body
    )

    res = client.post(
        "/payments/webhook/executor",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Executor-Signature": sig,
            "X-Executor-Timestamp": old_ts,
            "X-Executor-Nonce": "nonce-stale",
        },
    )
    assert res.status_code == 401
