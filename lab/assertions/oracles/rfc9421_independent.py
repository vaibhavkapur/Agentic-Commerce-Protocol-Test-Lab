"""Independent RFC 9421 verifier used as a second opinion on TAP cases (plan §15).

This deliberately does *not* import ``fixtures.issuers.http_signatures``. It parses
``Signature-Input`` and rebuilds the signature base directly from the RFC text
(§2.1 derived components, §2.5 signature base, §3.2 verification) using only the
``cryptography`` primitives. The oracle answers: given what the agent actually
sent and the registry's public key, *should* the signature verify?

Comparing the verifier fixture with itself would be weak evidence; comparing
its verdict with this oracle is the check the lab reports.
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

_LIST_RE = re.compile(r'^\s*([A-Za-z0-9_-]+)=\((.*?)\)(.*)$', re.S)
_PARAM_RE = re.compile(r';\s*([a-z]+)=("(?:[^"\\]|\\.)*"|\?[01]|-?\d+)')


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_input(signature_input: str) -> Tuple[str, List[str], Dict[str, Any], str]:
    m = _LIST_RE.match(signature_input)
    if not m:
        raise ValueError("not an RFC 9421 Signature-Input")
    label, inner, rest = m.groups()
    covered = [tok.strip().strip('"') for tok in inner.split() if tok.strip()]
    params: Dict[str, Any] = {}
    for pm in _PARAM_RE.finditer(rest):
        k, raw = pm.group(1), pm.group(2)
        params[k] = raw[1:-1] if raw.startswith('"') else (raw == "?1" if raw.startswith("?") else int(raw))
    # The @signature-params value is everything after "label=".
    return label, covered, params, signature_input.split("=", 1)[1].strip()


def build_base(method: str, authority: str, path: str, headers: Dict[str, str], covered: List[str], params_value: str) -> str:
    lower = {k.lower(): v for k, v in headers.items()}
    lines = []
    for c in covered:
        if c == "@method":
            val = method.upper()
        elif c == "@authority":
            val = authority.lower()
        elif c == "@path":
            val = path
        elif c.startswith("@"):
            raise ValueError(f"oracle does not support derived component {c}")
        else:
            if c not in lower:
                raise ValueError(f"covered header {c} missing")
            val = " ".join(lower[c].strip().split())
        lines.append(f'"{c}": {val}')
    lines.append(f'"@signature-params": {params_value}')
    return "\n".join(lines)


def verify(alg: str, jwk: Dict[str, Any], base: str, signature: bytes) -> bool:
    data = base.encode("utf-8")
    try:
        if alg == "ed25519" and jwk.get("kty") == "OKP":
            ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"])).verify(signature, data)
            return True
        if alg == "ecdsa-p256-sha256" and jwk.get("kty") == "EC":
            x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
            y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
            pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            pub.verify(encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
            return True
    except (InvalidSignature, ValueError):
        return False
    return False


def content_digest_sha256(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"


def expected_verdict(*, method: str, sent_authority: str, sent_path: str, sent_headers: Dict[str, str], sent_body: Optional[bytes],
                     signature_input: str, signature_b64: str, public_jwk: Dict[str, Any], now: int,
                     allowed_algs=("ed25519", "ecdsa-p256-sha256"), max_lifetime: int = 480) -> Dict[str, Any]:
    """Independently decide whether the request *as sent* carries a valid signature over its actual context."""
    label, covered, params, params_value = parse_input(signature_input)
    reasons: List[str] = []
    if params.get("alg") not in allowed_algs:
        reasons.append(f"alg {params.get('alg')!r} not allowed")
    created, expires = params.get("created"), params.get("expires")
    if not isinstance(created, int) or not isinstance(expires, int):
        reasons.append("created/expires missing")
    else:
        if expires < now:
            reasons.append("expired")
        if created > now + 60:
            reasons.append("created in the future")
        if expires - created > max_lifetime or expires <= created:
            reasons.append("lifetime invalid")
    if "@authority" not in covered or "@path" not in covered:
        reasons.append("coverage insufficient")
    if sent_body and "content-digest" not in covered:
        reasons.append("body not covered by content-digest")
    if sent_body and "content-digest" in covered:
        hdr = {k.lower(): v for k, v in sent_headers.items()}.get("content-digest")
        if hdr != content_digest_sha256(sent_body):
            reasons.append("content-digest does not match sent body")
    sig_ok = False
    try:
        base = build_base(method, sent_authority, sent_path, sent_headers, covered, params_value)
        sig_ok = verify(params.get("alg"), public_jwk, base, base64.b64decode(signature_b64)) if params.get("alg") in allowed_algs else False
    except ValueError as exc:
        reasons.append(str(exc))
        base = None
    if not sig_ok:
        reasons.append("signature does not verify over the request as sent")
    return {"verdict": "verified" if not reasons else "rejected", "reasons": reasons, "base": base, "covered": covered, "label": label}
