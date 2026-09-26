"""Independent checks on AP2 mandate presentations and receipts (plan §15).

Kept free of ``fixtures.verifiers``: it re-derives digests and signature checks
from the SD-JWT and JWS rules with ``hashlib`` and ``cryptography`` only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def presentation_hash(presentation: str) -> str:
    return _b64url(hashlib.sha256(presentation.encode("ascii")).digest())


def jws_verify(token: str, jwk: Dict[str, Any]) -> Dict[str, Any]:
    h, p, s = token.split(".")
    header = json.loads(_b64url_decode(h))
    data = f"{h}.{p}".encode("ascii")
    sig = _b64url_decode(s)
    alg = header.get("alg")
    try:
        if alg == "ES256" and jwk.get("kty") == "EC":
            x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
            y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
            pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            r, s_ = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
            pub.verify(encode_dss_signature(r, s_), data, ec.ECDSA(hashes.SHA256()))
        elif alg == "EdDSA" and jwk.get("kty") == "OKP":
            ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"])).verify(sig, data)
        else:
            raise ValueError(f"alg/key mismatch {alg}/{jwk.get('kty')}")
    except InvalidSignature:
        raise ValueError("signature invalid")
    return json.loads(_b64url_decode(p))


def disclosed_claims(link_jwt: str, disclosures: List[str]) -> Dict[str, Any]:
    """Resolve the delegate_payload mandate content of a kb+sd-jwt link independently."""
    payload = json.loads(_b64url_decode(link_jwt.split(".")[1]))
    by_digest = {_b64url(hashlib.sha256(d.encode("ascii")).digest()): json.loads(_b64url_decode(d)) for d in disclosures}
    out: Dict[str, Any] = {}
    for el in payload.get("delegate_payload", []):
        if isinstance(el, dict) and "..." in el and el["..."] in by_digest:
            content = by_digest[el["..."]][1]
            if isinstance(content, dict):
                for dg in content.get("_sd", []):
                    if dg in by_digest and len(by_digest[dg]) == 3:
                        content[by_digest[dg][1]] = by_digest[dg][2]
                out = content
    return out


def split_links(presentation: str) -> List[Dict[str, Any]]:
    parts = presentation.rstrip("~").split("~")
    links: List[Dict[str, Any]] = []
    for part in parts:
        if part.count(".") == 2:
            links.append({"jwt": part, "disclosures": []})
        elif links:
            links[-1]["disclosures"].append(part)
    return links


def checkout_binding_report(presentation: str, merchant_jwk: Dict[str, Any], expected_checkout_id: Optional[str]) -> Dict[str, Any]:
    """Independently answer: is the closed checkout mandate bound to ``expected_checkout_id``?"""
    links = split_links(presentation)
    if not links:
        return {"ok": False, "reason": "no links"}
    closed = disclosed_claims(links[-1]["jwt"], links[-1]["disclosures"])
    jwt = closed.get("checkout_jwt")
    if not jwt:
        return {"ok": False, "reason": "checkout_jwt not disclosed", "closed": closed}
    hash_ok = closed.get("checkout_hash") == _b64url(hashlib.sha256(jwt.encode("ascii")).digest())
    try:
        checkout = jws_verify(jwt, merchant_jwk)
        merchant_sig_ok = True
    except (ValueError, KeyError) as exc:
        checkout, merchant_sig_ok = {}, False
    bound_ok = expected_checkout_id is None or checkout.get("id") == expected_checkout_id
    return {"ok": hash_ok and merchant_sig_ok and bound_ok, "hash_ok": hash_ok, "merchant_signature_ok": merchant_sig_ok,
            "bound_to": checkout.get("id"), "expected": expected_checkout_id, "bound_ok": bound_ok}
