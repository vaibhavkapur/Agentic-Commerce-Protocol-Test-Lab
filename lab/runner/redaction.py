"""Redaction of secrets from exported traces (plan §17, §23).

The lab only ever handles synthetic credentials, but exported bundles still
must not contain bearer tokens, API keys, payment credentials, or private keys.
Redaction is applied to headers and JSON bodies before evidence is persisted.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable

REDACTED = "[REDACTED]"

SENSITIVE_HEADERS = {
    "authorization",
    "x-api-key",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}

# JSON keys whose string values are always redacted regardless of nesting.
SENSITIVE_KEYS = {
    "token",
    "api_key",
    "apikey",
    "secret",
    "private_key",
    "d",  # JWK private scalar
    "password",
    "access_token",
    "refresh_token",
}

# Keys whose value is a private JWK member set; drop private members only.
PRIVATE_JWK_MEMBERS = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}

_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in SENSITIVE_HEADERS:
            out[k] = REDACTED
        else:
            out[k] = v
    return out


def redact_json(value: Any, parent_key: str = "") -> Any:
    """Return a redacted deep copy of ``value``."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        is_jwk = value.get("kty") is not None
        for k, v in value.items():
            lk = str(k).lower()
            if is_jwk and lk in PRIVATE_JWK_MEMBERS:
                out[k] = REDACTED
            elif lk in SENSITIVE_KEYS and isinstance(v, (str, int, float)) and not is_jwk:
                out[k] = REDACTED
            else:
                out[k] = redact_json(v, lk)
        return out
    if isinstance(value, list):
        return [redact_json(v, parent_key) for v in value]
    if isinstance(value, str):
        s = _BEARER_RE.sub("Bearer " + REDACTED, value)
        s = _PEM_RE.sub(REDACTED, s)
        return s
    return value


def redact_text(text: str) -> str:
    text = _BEARER_RE.sub("Bearer " + REDACTED, text)
    return _PEM_RE.sub(REDACTED, text)


def contains_secret(value: Any, secrets: Iterable[str]) -> bool:
    """True if any literal secret string appears in the serialized value."""
    import json

    blob = json.dumps(value, default=str)
    return any(s and s in blob for s in secrets)


def deep_copy(value: Any) -> Any:
    return copy.deepcopy(value)
