from __future__ import annotations

import json
import csv
import io

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from unstoppable.apikeys import (
    PLAN_CONFIG,
    authorize_and_record,
    create_key,
    list_keys,
    topup_key,
    usage_for_key,
)
from unstoppable.auth import require_admin
from unstoppable.config import DB_PATH, EXECUTOR_WEBHOOK_SECRET
from unstoppable.idempotency import reserve_or_get as idem_reserve_or_get
from unstoppable.idempotency import store_response as idem_store
from unstoppable.queue import enqueue_urls, queue_stats
from unstoppable.search_backend import search as search_documents
from unstoppable.storage import connect
from unstoppable.webhook import verify_timed_signature


app = FastAPI(title="The Unstoppable Service API", version="0.3.0")


class DonationRequest(BaseModel):
    coin: str = Field(default="btc")
    amount_usd: float = Field(gt=0)
    source: str = Field(default="manual")
    txid: str | None = None


class PaymentRequest(BaseModel):
    amount_usd: float | None = Field(default=None, gt=0)
    provider: str = Field(default="akash")


class QueueSeedRequest(BaseModel):
    urls: list[str]
    priority: int = Field(default=100, ge=1, le=1000)


class ApiKeyCreateRequest(BaseModel):
    name: str
    plan: str = Field(default="free")


class ApiKeyTopupRequest(BaseModel):
    api_key: str
    amount_usd: float = Field(gt=0)


class RevenueConfigPatch(BaseModel):
    ads_enabled: bool | None = None
    ads_fallback_enabled: bool | None = None
    activate_if_runway_below_months: float | None = Field(default=None, gt=0)
    max_ads_per_query: int | None = Field(default=None, ge=0, le=10)


class PaymentIntentCreateRequest(BaseModel):
    amount_usd: float = Field(gt=0)
    provider: str = Field(default="akash")
    reason: str = Field(default="manual")


class PaymentIntentExecuteRequest(BaseModel):
    intent_id: str


class PaymentRefreshRequest(BaseModel):
    payment_id: str | None = None


class RetryProcessRequest(BaseModel):
    max_jobs: int = Field(default=5, ge=1, le=100)


class RetryRequeueRequest(BaseModel):
    job_ids: list[int] | None = None
    limit: int = Field(default=10, ge=1, le=200)


class RetryDismissRequest(BaseModel):
    job_ids: list[int] | None = None
    limit: int = Field(default=10, ge=1, le=200)
    note: str | None = None


class PolicyPatch(BaseModel):
    payments_enabled: bool | None = None
    max_single_payment_usd: float | None = Field(default=None, gt=0)
    max_daily_payment_usd: float | None = Field(default=None, gt=0)
    min_treasury_buffer_usd: float | None = Field(default=None, ge=0)
    allow_autopay: bool | None = None
    retry_max_attempts: int | None = Field(default=None, ge=1, le=50)
    retry_base_delay_seconds: int | None = Field(default=None, ge=1, le=3600)
    retry_max_delay_seconds: int | None = Field(default=None, ge=1, le=86400)


def _controller(request: Request):
    return getattr(request.app.state, "controller", None)


def _revenue(request: Request):
    return getattr(request.app.state, "revenue", None)


def _policy(request: Request):
    return getattr(request.app.state, "policy", None)


def _idempotency_key(request: Request) -> str | None:
    key = request.headers.get("x-idempotency-key")
    if not key:
        return None
    key = key.strip()
    return key or None


def _treasury_service(request: Request):
    controller = _controller(request)
    if controller is not None:
        return controller.treasury
    return getattr(request.app.state, "treasury_service", None)


def _payment_required_headers(billing: dict) -> dict[str, str]:
    charged = float(billing.get("charged_usd", 0.0) or 0.0)
    credit = billing.get("credit_usd")
    daily_limit = billing.get("daily_limit")
    headers = {
        "WWW-Authenticate": "x402",
        "X-402-Reason": str(billing.get("error", "payment required")),
        "X-402-Topup-Endpoint": "/apikeys/topup",
        "X-402-Payment-Intent-Endpoint": "/payments/intents/create",
        "X-402-Asset": "USD",
        "X-402-Price": f"{charged:.4f}",
    }
    if credit is not None:
        headers["X-402-Current-Credit"] = str(credit)
    if daily_limit is not None:
        headers["X-402-Daily-Limit"] = str(daily_limit)
    return headers


def _raise_payment_required(billing: dict) -> None:
    detail = {
        "error": billing.get("error", "query not authorized"),
        "x402": {
            "accepts": [
                {
                    "scheme": "api-key-credit",
                    "asset": "USD",
                    "topup_endpoint": "/apikeys/topup",
                    "payment_intent_endpoint": "/payments/intents/create",
                }
            ],
            "price_usd": float(billing.get("charged_usd", 0.0) or 0.0),
            "credit_usd": billing.get("credit_usd"),
            "daily_limit": billing.get("daily_limit"),
        },
    }
    raise HTTPException(
        status_code=402,
        detail=detail,
        headers=_payment_required_headers(billing),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>The Unstoppable Service</title>
  <style>
    :root { color-scheme: light; }
    body { margin: 0; font-family: Georgia, serif; background: linear-gradient(160deg,#f5f3ea,#e8edf3); color:#121212; }
    .wrap { max-width: 920px; margin: 40px auto; padding: 0 16px; }
    h1 { font-size: 2.2rem; margin: 0 0 8px; }
    p { margin: 0 0 14px; }
    .panel { background: #ffffffcc; border: 1px solid #d9d9d9; border-radius: 12px; padding: 14px; margin: 12px 0; }
    form { display: flex; gap: 8px; flex-wrap: wrap; }
    input { flex: 1; min-width: 220px; border: 1px solid #b8b8b8; border-radius: 8px; padding: 10px; font-size: 1rem; }
    button { border: 1px solid #111; border-radius: 8px; background: #111; color: #fff; padding: 10px 14px; cursor: pointer; }
    .meta { font-family: Menlo, monospace; font-size: 0.85rem; color: #444; }
    .res { padding: 10px 0; border-bottom: 1px solid #e6e6e6; }
    .res a { color: #0d3b66; font-size: 1.05rem; text-decoration: none; }
    .res a:hover { text-decoration: underline; }
    .snippet { color: #2c2c2c; margin-top: 6px; }
    .ad { background: #fdf7e8; border-left: 4px solid #b67b00; padding: 8px; margin: 8px 0; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>The Unstoppable Service</h1>
    <p>Organic search engine with autonomous operations.</p>
    <p><a href=\"/donate\">Donation page</a></p>

    <div class=\"panel\">
      <form id=\"search-form\">
        <input id=\"q\" placeholder=\"Search the independent index...\" />
        <input id=\"apiKey\" placeholder=\"Optional API key\" />
        <button type=\"submit\">Search</button>
      </form>
      <p class=\"meta\" id=\"search-meta\"></p>
      <div id=\"results\"></div>
    </div>

    <div class=\"panel\">
      <div class=\"row\">
        <button id=\"cycle\">Run One Autonomy Cycle</button>
        <button id=\"start\">Start Loop</button>
        <button id=\"stop\">Stop Loop</button>
        <button id=\"donate\">Simulate $10 BTC Donation</button>
        <button id=\"pay\">Pay Hosting Due Now</button>
      </div>
      <p class=\"meta\" id=\"status\"></p>
      <p class=\"meta\" id=\"treasury\"></p>
    </div>
  </div>
  <script>
    const resultsEl = document.getElementById('results');
    const metaEl = document.getElementById('search-meta');
    const statusEl = document.getElementById('status');
    const treasuryEl = document.getElementById('treasury');

    async function refreshStatus() {
      const r = await fetch('/autonomy/status');
      const data = await r.json();
      const cycle = data.last_cycle;
      const mode = cycle && cycle.brain ? cycle.brain.mode : 'n/a';
      statusEl.textContent = `running=${data.running} cycles=${data.cycles_completed} mode=${mode} error=${data.last_error || 'none'}`;
      const t = data.treasury || {};
      treasuryEl.textContent = `treasury=$${Number(t.treasury_usd || 0).toFixed(2)} runway=${Number(t.runway_months || 0).toFixed(2)}m due=$${Number((t.infra||{}).accrued_hosting_due_usd || 0).toFixed(2)}`;
    }

    async function runSearch(query) {
      const apiKey = document.getElementById('apiKey').value.trim();
      const headers = apiKey ? { 'X-API-Key': apiKey } : {};
      const r = await fetch(`/search?q=${encodeURIComponent(query)}&limit=10`, { headers });
      const data = await r.json();
      if (!r.ok) {
        metaEl.textContent = `Search blocked: ${data.detail || 'error'}`;
        return;
      }

      metaEl.textContent = `${data.count} results for "${data.query}" plan=${(data.billing||{}).plan || 'anonymous'} ads=${data.ads_enabled}`;
      const adHtml = (data.ads || []).map(x => `
        <div class="ad">
          <div class="meta">Sponsored</div>
          <a href="${x.url}" target="_blank" rel="noreferrer">${x.title}</a>
          <div class="snippet">${x.description}</div>
        </div>
      `).join('');
      const resultHtml = (data.results || []).map(x => `
        <div class="res">
          <a href="${x.url}" target="_blank" rel="noreferrer">${x.title}</a>
          <div class="meta">score=${Number(x.score).toFixed(6)} crawled=${x.last_crawled}</div>
          <div class="snippet">${x.snippet}</div>
        </div>
      `).join('');
      resultsEl.innerHTML = adHtml + resultHtml;
    }

    document.getElementById('search-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = document.getElementById('q').value.trim();
      if (!q) return;
      await runSearch(q);
    });

    document.getElementById('cycle').addEventListener('click', async () => {
      await fetch('/autonomy/run-once', { method: 'POST' });
      await refreshStatus();
    });

    document.getElementById('start').addEventListener('click', async () => {
      await fetch('/autonomy/start', { method: 'POST' });
      await refreshStatus();
    });

    document.getElementById('stop').addEventListener('click', async () => {
      await fetch('/autonomy/stop', { method: 'POST' });
      await refreshStatus();
    });

    document.getElementById('donate').addEventListener('click', async () => {
      await fetch('/treasury/donate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ coin: 'btc', amount_usd: 10, source: 'ui-demo' })
      });
      await refreshStatus();
    });

    document.getElementById('pay').addEventListener('click', async () => {
      await fetch('/payments/pay-now', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: 'akash' })
      });
      await refreshStatus();
    });

    refreshStatus();
    setInterval(refreshStatus, 5000);
  </script>
</body>
</html>
"""


@app.get("/donate", response_class=HTMLResponse)
def donate_page() -> str:
    return """<!doctype html>
<html><head><meta charset=\"utf-8\"/><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
<title>Donate - The Unstoppable Service</title>
<style>
body{font-family:Georgia,serif;background:#f6f2e9;color:#111;margin:0}
.wrap{max-width:760px;margin:40px auto;padding:0 16px}
.card{background:#fff;border:1px solid #ddd;border-radius:12px;padding:14px;margin:12px 0}
input,select,button{padding:10px;border-radius:8px;border:1px solid #aaa}
button{background:#111;color:#fff;cursor:pointer}
.mono{font-family:Menlo,monospace;font-size:.9rem}
</style></head><body><div class=\"wrap\">
<h1>Support the Service</h1>
<p>Donation-first revenue model. Ads activate only as fallback when runway drops.</p>
<div class=\"card\">
<p><strong>Example wallet destinations</strong></p>
<p class=\"mono\">BTC: bc1qexampleaddressforphase3demo0000000000000000</p>
<p class=\"mono\">XMR: 89ExampleMoneroAddressForDemoOnlyxxxxxxxxxxxxxxxx</p>
<p class=\"mono\">ZEC: zs1examplezcashaddressfordemo000000000000000000</p>
</div>
<div class=\"card\">
<p><strong>Simulate donation</strong></p>
<form id=\"f\"><select id=\"coin\"><option>btc</option><option>xmr</option><option>zec</option></select>
<input id=\"amount\" type=\"number\" value=\"10\" min=\"1\" step=\"0.01\"/><button type=\"submit\">Donate</button></form>
<p id=\"out\" class=\"mono\"></p>
</div>
<p><a href=\"/\">Back to search</a></p>
</div>
<script>
document.getElementById('f').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const coin=document.getElementById('coin').value;
  const amount=Number(document.getElementById('amount').value||0);
  const r=await fetch('/treasury/donate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({coin,amount_usd:amount,source:'donation-page'})});
  const d=await r.json();
  document.getElementById('out').textContent=JSON.stringify(d, null, 2);
});
</script></body></html>"""


@app.get("/stats")
def stats() -> dict:
    conn = connect(DB_PATH)
    try:
        pages = int(conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"])
        links = int(
            conn.execute("SELECT COUNT(*) AS c FROM page_links").fetchone()["c"]
        )
        queue = queue_stats(conn)
        return {"pages": pages, "links": links, "queue": queue, "plans": PLAN_CONFIG}
    finally:
        conn.close()


@app.get("/search")
def search(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    conn = connect(DB_PATH)
    try:
        api_key = request.headers.get("x-api-key")
        billing = authorize_and_record(conn, api_key)
        if not billing.get("allowed", False):
            _raise_payment_required(billing)

        if float(billing.get("charged_usd", 0.0)) > 0:
            treasury = _treasury_service(request)
            if treasury is not None:
                treasury.record_income(
                    amount_usd=float(billing["charged_usd"]),
                    source="api-key-query",
                    note=billing.get("key_hint"),
                )

        results, backend_used = search_documents(conn, q, limit)

        treasury = _treasury_service(request)
        runway = None
        if treasury is not None:
            runway = float(treasury.snapshot().get("runway_months", 0.0))

        revenue = _revenue(request)
        ads_enabled = False
        ads = []
        if revenue is not None:
            ads_enabled = revenue.ads_active(runway)
            if ads_enabled:
                ads = revenue.select_ads(q)

        return {
            "query": q,
            "count": len(results),
            "results": results,
            "search_backend": backend_used,
            "billing": billing,
            "ads_enabled": ads_enabled,
            "ads": ads,
        }
    finally:
        conn.close()


@app.get("/autonomy/status")
def autonomy_status(request: Request) -> dict:
    controller = _controller(request)
    if controller is None:
        return {
            "running": False,
            "last_cycle": None,
            "last_error": "controller not configured",
            "cycles_completed": 0,
        }
    return controller.status()


@app.post("/autonomy/run-once")
def autonomy_run_once(request: Request) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    return controller.run_cycle()


@app.post("/autonomy/start")
def autonomy_start(request: Request) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    return controller.start()


@app.post("/autonomy/stop")
def autonomy_stop(request: Request) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    return controller.stop()


@app.get("/treasury")
def treasury_snapshot(request: Request) -> dict:
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    return treasury.snapshot()


@app.post("/treasury/donate")
def treasury_donate(request: Request, payload: DonationRequest) -> dict:
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    event = treasury.add_donation(
        coin=payload.coin,
        amount_usd=payload.amount_usd,
        source=payload.source,
        txid=payload.txid,
    )
    return {"donation": event, "treasury": treasury.snapshot()}


@app.get("/payments/history")
def payments_history(
    request: Request, limit: int = Query(default=20, ge=1, le=200)
) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    payments = treasury.payments(limit=limit)
    return {"count": len(payments), "payments": payments}


@app.get("/payments/receipts")
def payments_receipts(
    request: Request, limit: int = Query(default=20, ge=1, le=500)
) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT recorded_at, kind, intent_id, payment_id, txid, status, payload_json
            FROM payment_receipts
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        receipts = [
            {
                "recorded_at": r["recorded_at"],
                "kind": r["kind"],
                "intent_id": r["intent_id"],
                "payment_id": r["payment_id"],
                "txid": r["txid"],
                "status": r["status"],
                "payload": json.loads(r["payload_json"]),
            }
            for r in rows
        ]
        return {"count": len(receipts), "receipts": receipts}
    finally:
        conn.close()


@app.get("/payments/receipts/export", response_class=PlainTextResponse)
def payments_receipts_export(
    request: Request,
    format: str = Query(default="jsonl", pattern="^(jsonl|csv)$"),
    limit: int = Query(default=200, ge=1, le=5000),
) -> str:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT recorded_at, kind, intent_id, payment_id, txid, status, payload_json
            FROM payment_receipts
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    if format == "jsonl":
        lines = []
        for r in rows:
            lines.append(
                json.dumps(
                    {
                        "recorded_at": r["recorded_at"],
                        "kind": r["kind"],
                        "intent_id": r["intent_id"],
                        "payment_id": r["payment_id"],
                        "txid": r["txid"],
                        "status": r["status"],
                        "payload": json.loads(r["payload_json"]),
                    },
                    ensure_ascii=True,
                )
            )
        return "\n".join(lines)

    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "recorded_at",
            "kind",
            "intent_id",
            "payment_id",
            "txid",
            "status",
            "payload_json",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "recorded_at": r["recorded_at"],
                "kind": r["kind"],
                "intent_id": r["intent_id"],
                "payment_id": r["payment_id"],
                "txid": r["txid"],
                "status": r["status"],
                "payload_json": r["payload_json"],
            }
        )
    return out.getvalue()


@app.post("/payments/pay-now")
def payments_pay_now(request: Request, payload: PaymentRequest) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    payment = controller.pay_now(
        amount_usd=payload.amount_usd, provider=payload.provider
    )
    return {"payment": payment, "treasury": controller.treasury.snapshot()}


@app.get("/payments/intents")
def payments_intents(
    request: Request, limit: int = Query(default=20, ge=1, le=200)
) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    intents = controller.payment_intents(limit=limit)
    return {"count": len(intents), "intents": intents}


@app.post("/payments/intents/create")
def payments_intents_create(
    request: Request, payload: PaymentIntentCreateRequest
) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    idem_key = _idempotency_key(request)
    if idem_key:
        conn = connect(DB_PATH)
        try:
            state, cached = idem_reserve_or_get(
                conn, "payments.intents.create", idem_key
            )
            if state == "done" and cached is not None:
                return cached[1]
            if state == "in_progress":
                raise HTTPException(
                    status_code=409, detail="idempotency key is in progress"
                )
        finally:
            conn.close()

    intent = controller.create_payment_intent(
        amount_usd=payload.amount_usd,
        provider=payload.provider,
        reason=payload.reason,
    )
    response = {"intent": intent, "treasury": controller.treasury.snapshot()}
    if idem_key:
        conn = connect(DB_PATH)
        try:
            idem_store(conn, "payments.intents.create", idem_key, 200, response)
        finally:
            conn.close()
    return response


@app.post("/payments/intents/execute")
def payments_intents_execute(
    request: Request, payload: PaymentIntentExecuteRequest
) -> dict:
    require_admin(request)
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail="controller not configured")
    idem_key = _idempotency_key(request)
    if idem_key:
        conn = connect(DB_PATH)
        try:
            state, cached = idem_reserve_or_get(
                conn, "payments.intents.execute", idem_key
            )
            if state == "done" and cached is not None:
                return cached[1]
            if state == "in_progress":
                raise HTTPException(
                    status_code=409, detail="idempotency key is in progress"
                )
        finally:
            conn.close()

    result = controller.execute_payment_intent(payload.intent_id)
    response = {"result": result, "treasury": controller.treasury.snapshot()}
    if idem_key:
        conn = connect(DB_PATH)
        try:
            idem_store(conn, "payments.intents.execute", idem_key, 200, response)
        finally:
            conn.close()
    return response


@app.post("/payments/refresh")
def payments_refresh(request: Request, payload: PaymentRefreshRequest) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    refreshed = treasury.refresh_payment_status(payment_id=payload.payment_id)
    return {"refresh": refreshed, "treasury": treasury.snapshot()}


@app.get("/payments/retries")
def payments_retries(
    request: Request, limit: int = Query(default=20, ge=1, le=500)
) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    jobs = treasury.retry_jobs(limit=limit)
    return {"count": len(jobs), "jobs": jobs}


@app.post("/payments/retries/process")
def payments_retries_process(request: Request, payload: RetryProcessRequest) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    result = treasury.process_retry_jobs(max_jobs=payload.max_jobs)
    return {"result": result}


@app.get("/payments/retries/dead-letter")
def payments_retries_dead_letter(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    jobs = [
        j for j in treasury.retry_jobs(limit=limit) if j.get("status") == "dead_letter"
    ]
    return {"count": len(jobs), "jobs": jobs}


@app.post("/payments/retries/requeue")
def payments_retries_requeue(request: Request, payload: RetryRequeueRequest) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    result = treasury.requeue_dead_letter_jobs(
        job_ids=payload.job_ids, limit=payload.limit
    )
    return {"result": result}


@app.post("/payments/retries/dismiss")
def payments_retries_dismiss(request: Request, payload: RetryDismissRequest) -> dict:
    require_admin(request)
    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    result = treasury.dismiss_dead_letter_jobs(
        job_ids=payload.job_ids,
        limit=payload.limit,
        note=payload.note,
        actor="admin-api",
    )
    return {"result": result}


@app.post("/payments/webhook/executor")
async def payments_webhook_executor(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("x-executor-signature")
    timestamp = request.headers.get("x-executor-timestamp")
    nonce = request.headers.get("x-executor-nonce")
    ok, reason = verify_timed_signature(
        EXECUTOR_WEBHOOK_SECRET,
        body,
        signature,
        timestamp,
        nonce,
    )
    if not ok:
        raise HTTPException(
            status_code=401, detail=f"invalid webhook signature: {reason}"
        )

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON payload")

    treasury = _treasury_service(request)
    if treasury is None:
        raise HTTPException(status_code=503, detail="treasury not configured")
    if not treasury.consume_webhook_nonce(nonce=nonce, source="executor-webhook"):
        raise HTTPException(status_code=409, detail="webhook nonce already used")
    result = treasury.apply_webhook_update(payload, source="executor-webhook")
    return {"result": result}


@app.get("/queue/stats")
def get_queue_stats() -> dict:
    conn = connect(DB_PATH)
    try:
        return queue_stats(conn)
    finally:
        conn.close()


@app.post("/queue/seed")
def seed_queue(request: Request, payload: QueueSeedRequest) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        inserted = enqueue_urls(conn, payload.urls, priority=payload.priority)
        return {"inserted": inserted, "queue": queue_stats(conn)}
    finally:
        conn.close()


@app.post("/apikeys/create")
def apikey_create(request: Request, payload: ApiKeyCreateRequest) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        key = create_key(conn, name=payload.name, plan=payload.plan)
        return {"created": key}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@app.post("/apikeys/topup")
def apikey_topup(request: Request, payload: ApiKeyTopupRequest) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        result = topup_key(conn, raw_key=payload.api_key, amount_usd=payload.amount_usd)
        if "error" not in result:
            treasury = getattr(app.state, "treasury_service", None)
            if treasury is not None:
                treasury.record_income(
                    payload.amount_usd,
                    source="api-key-topup",
                    note=result.get("key_hint"),
                )
        return result
    finally:
        conn.close()


@app.get("/apikeys/list")
def apikey_list(request: Request) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        return {"keys": list_keys(conn), "plans": PLAN_CONFIG}
    finally:
        conn.close()


@app.get("/apikeys/usage")
def apikey_usage(request: Request, api_key: str = Query(min_length=8)) -> dict:
    require_admin(request)
    conn = connect(DB_PATH)
    try:
        return usage_for_key(conn, raw_key=api_key)
    finally:
        conn.close()


@app.get("/revenue/config")
def revenue_config(request: Request) -> dict:
    require_admin(request)
    revenue = _revenue(request)
    if revenue is None:
        raise HTTPException(status_code=503, detail="revenue service not configured")
    return revenue.get_config()


@app.post("/revenue/config")
def revenue_update_config(request: Request, payload: RevenueConfigPatch) -> dict:
    require_admin(request)
    revenue = _revenue(request)
    if revenue is None:
        raise HTTPException(status_code=503, detail="revenue service not configured")

    patch: dict = {"ads": {}}
    if payload.ads_enabled is not None:
        patch["ads"]["enabled"] = payload.ads_enabled
    if payload.ads_fallback_enabled is not None:
        patch["ads"]["fallback_enabled"] = payload.ads_fallback_enabled
    if payload.activate_if_runway_below_months is not None:
        patch["ads"]["activate_if_runway_below_months"] = (
            payload.activate_if_runway_below_months
        )
    if payload.max_ads_per_query is not None:
        patch["ads"]["max_ads_per_query"] = payload.max_ads_per_query

    if not patch["ads"]:
        return revenue.get_config()
    return revenue.update_config(patch)


@app.get("/policy")
def policy_get(request: Request) -> dict:
    require_admin(request)
    policy = _policy(request)
    if policy is None:
        raise HTTPException(status_code=503, detail="policy service not configured")
    return policy.get()


@app.post("/policy")
def policy_update(request: Request, payload: PolicyPatch) -> dict:
    require_admin(request)
    policy = _policy(request)
    if policy is None:
        raise HTTPException(status_code=503, detail="policy service not configured")

    patch = {"payments": {}}
    if payload.payments_enabled is not None:
        patch["payments"]["enabled"] = payload.payments_enabled
    if payload.max_single_payment_usd is not None:
        patch["payments"]["max_single_payment_usd"] = payload.max_single_payment_usd
    if payload.max_daily_payment_usd is not None:
        patch["payments"]["max_daily_payment_usd"] = payload.max_daily_payment_usd
    if payload.min_treasury_buffer_usd is not None:
        patch["payments"]["min_treasury_buffer_usd"] = payload.min_treasury_buffer_usd
    if payload.allow_autopay is not None:
        patch["payments"]["allow_autopay"] = payload.allow_autopay
    if payload.retry_max_attempts is not None:
        patch["payments"]["retry_max_attempts"] = payload.retry_max_attempts
    if payload.retry_base_delay_seconds is not None:
        patch["payments"]["retry_base_delay_seconds"] = payload.retry_base_delay_seconds
    if payload.retry_max_delay_seconds is not None:
        patch["payments"]["retry_max_delay_seconds"] = payload.retry_max_delay_seconds

    if not patch["payments"]:
        return policy.get()
    return policy.update(patch)


@app.get("/ads/preview")
def ads_preview(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=2, ge=0, le=10),
) -> dict:
    revenue = _revenue(request)
    if revenue is None:
        raise HTTPException(status_code=503, detail="revenue service not configured")
    return {"query": q, "ads": revenue.select_ads(q, limit=limit)}
