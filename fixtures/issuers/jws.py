"""Compact JWS helpers (RFC 7515) used by the AP2 fixtures and receipts."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from .keys import KeyHandle, b64url, b64url_decode, jwk_to_public_key, verify_bytes


def _json(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign_compact(key: KeyHandle, header: Dict[str, Any], payload: Dict[str, Any]) -> str:
    hdr = dict(header)
    hdr.setdefault("alg", key.alg)
    hdr.setdefault("kid", key.kid)
    signing_input = b64url(_json(hdr)) + "." + b64url(_json(payload))
    sig = key.sign(signing_input.encode("ascii"))
    return signing_input + "." + b64url(sig)


def split_compact(token: str) -> Tuple[Dict[str, Any], Dict[str, Any], bytes, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("compact JWS must have three parts")
    header = json.loads(b64url_decode(parts[0]))
    payload = json.loads(b64url_decode(parts[1]))
    return header, payload, b64url_decode(parts[2]), parts[0] + "." + parts[1]


def verify_compact(token: str, public_jwk: Dict[str, Any], *, expected_alg: Optional[str] = None) -> Dict[str, Any]:
    """Verify signature and return the payload. Raises ValueError on failure."""
    header, payload, sig, signing_input = split_compact(token)
    alg = header.get("alg")
    if expected_alg and alg != expected_alg:
        raise ValueError(f"alg {alg!r} does not match expected {expected_alg!r}")
    if alg not in ("EdDSA", "ES256"):
        raise ValueError(f"unsupported alg {alg!r}")
    pub = jwk_to_public_key(public_jwk)
    if not verify_bytes(pub, alg, signing_input.encode("ascii"), sig):
        raise ValueError("signature does not verify")
    return payload


def peek_header(token: str) -> Dict[str, Any]:
    return split_compact(token)[0]


def peek_payload(token: str) -> Dict[str, Any]:
    return split_compact(token)[1]
