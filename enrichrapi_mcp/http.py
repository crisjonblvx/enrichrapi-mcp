"""HTTP helpers for the Enrichr MCP server (no MCP dependency)."""
from __future__ import annotations

from typing import Any

ACCOUNT_GET = frozenset({
    "/v1/catalog",
    "/v1/account/usage",
    "/v1/account/options",
})

ACCOUNT_POST = frozenset({
    "/v1/account/signup",
    "/v1/account/recover",
    "/v1/account/verify",
    "/v1/account/portal",
    "/v1/account/rotate",
    "/v1/account/checkout",
})


def unwrap_success(payload: Any) -> Any:
    """Lift ``data`` to the top level for priced EnrichResponse payloads.

    ``cost_usd`` is copied onto the result (docstrings) and also kept in ``_meta``.
    """
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if not isinstance(data, dict) or "cost_usd" not in payload:
        return payload
    meta = {
        k: payload[k]
        for k in ("ok", "cost_usd", "call_count_this_month")
        if k in payload
    }
    out = {**data, "_meta": meta}
    if "cost_usd" in meta:
        out.setdefault("cost_usd", meta["cost_usd"])
    return out


def error_payload(
    *,
    status: int | None,
    detail: Any,
    code: str | None = None,
    retryable: bool | None = None,
    retry_after_seconds: int | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    if code is None:
        code = {
            400: "bad_request",
            401: "unauthorized",
            402: "payment_required",
            404: "not_found",
            413: "payload_too_large",
            422: "validation_error",
            429: "rate_limited",
            500: "internal_error",
            502: "upstream_error",
            503: "service_unavailable",
        }.get(status or 0, "error")
    if retryable is None:
        retryable = status in (429, 500, 502, 503)
    if retry_after_seconds is None and headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            try:
                retry_after_seconds = int(float(raw))
            except ValueError:
                retry_after_seconds = None
    err: dict[str, Any] = {
        "code": code,
        "retryable": bool(retryable),
        "status": status,
        "detail": detail,
        "retry_after_seconds": retry_after_seconds,
    }
    return {"ok": False, "error": err}


def parse_error_body(status: int, body: Any, headers: dict[str, str] | None = None) -> dict:
    detail: Any = body
    code = None
    retryable = None
    retry_after = None
    hints: dict[str, Any] = {}
    if isinstance(body, dict):
        detail = body.get("detail", body)
        code = body.get("code")
        retryable = body.get("retryable")
        retry_after = body.get("retry_after_seconds")
        for key in ("purchases_enabled", "topup_usd", "billing_url", "checkout_path"):
            if key in body:
                hints[key] = body[key]
    result = error_payload(
        status=status,
        detail=detail,
        code=code,
        retryable=retryable,
        retry_after_seconds=retry_after,
        headers=headers,
    )
    result["error"].update(hints)
    return result


def normalize_path(path: str) -> str:
    p = path.strip()
    if p.startswith("http://") or p.startswith("https://"):
        # strip origin
        without = p.split("://", 1)[1]
        p = "/" + without.split("/", 1)[1] if "/" in without else "/"
    if not p.startswith("/"):
        p = "/" + p
    if p.startswith("/v1/") or p == "/v1/catalog":
        return p.rstrip("/") or p
    if p.startswith("v1/"):
        return "/" + p.rstrip("/")
    return p


def is_get_path(path: str) -> bool:
    return path in ACCOUNT_GET
