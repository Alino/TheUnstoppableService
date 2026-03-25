from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from unstoppable.config import ADMIN_API_TOKEN


def _extract_token(request: Request) -> str | None:
    explicit = request.headers.get("x-admin-token")
    if explicit:
        return explicit.strip()

    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_admin(request: Request) -> None:
    token = _extract_token(request)
    if not token or not hmac.compare_digest(token, ADMIN_API_TOKEN):
        raise HTTPException(status_code=401, detail="admin token required")
