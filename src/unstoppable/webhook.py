from __future__ import annotations

import hashlib
import hmac
import time


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return digest


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature.strip())


def compute_timed_signature(
    secret: str, timestamp: str, nonce: str, body: bytes
) -> str:
    signed = timestamp.encode("utf-8") + b"." + nonce.encode("utf-8") + b"." + body
    return compute_signature(secret, signed)


def verify_timed_signature(
    secret: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    nonce: str | None,
    tolerance_seconds: int = 300,
) -> tuple[bool, str]:
    if not signature:
        return False, "missing signature"
    if not timestamp:
        return False, "missing timestamp"
    if not nonce:
        return False, "missing nonce"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "invalid timestamp"

    now = int(time.time())
    if abs(now - ts) > tolerance_seconds:
        return False, "timestamp outside tolerance"

    expected = compute_timed_signature(secret, str(ts), nonce, body)
    if not hmac.compare_digest(expected, signature.strip()):
        return False, "invalid signature"
    return True, "ok"
