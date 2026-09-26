"""Synthetic key material with distinct roles (plan §12, §19).

Keys are derived deterministically from the run seed so that a seeded run
reproduces the same JWK thumbprints and signatures. Only Ed25519 and P-256 are
supported: both can be derived from seeded scalars without a CSPRNG.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from lab.runner.clock import LabRng


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


@dataclass
class KeyHandle:
    """A private key with its role label and public JWK."""

    kid: str
    role: str  # issuer | user | agent | merchant | verifier | registry
    alg: str  # "EdDSA" | "ES256"
    private_key: Any
    public_jwk: Dict[str, Any]

    @property
    def rfc9421_alg(self) -> str:
        return {"EdDSA": "ed25519", "ES256": "ecdsa-p256-sha256"}[self.alg]

    def sign(self, data: bytes) -> bytes:
        return sign_bytes(self.private_key, self.alg, data)

    def public_pem(self) -> str:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")


def generate_key(rng: LabRng, kid: str, role: str, alg: str = "EdDSA") -> KeyHandle:
    if alg == "EdDSA":
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(rng.bytes(32))
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        jwk = {"kty": "OKP", "crv": "Ed25519", "x": b64url(pub), "kid": kid, "alg": "EdDSA", "use": "sig"}
    elif alg == "ES256":
        scalar = int.from_bytes(rng.bytes(32), "big") % (_P256_ORDER - 1) + 1
        priv = ec.derive_private_key(scalar, ec.SECP256R1())
        nums = priv.public_key().public_numbers()
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": b64url(nums.x.to_bytes(32, "big")),
            "y": b64url(nums.y.to_bytes(32, "big")),
            "kid": kid,
            "alg": "ES256",
            "use": "sig",
        }
    else:
        raise ValueError(f"unsupported alg {alg}")
    return KeyHandle(kid=kid, role=role, alg=alg, private_key=priv, public_jwk=jwk)


def jwk_thumbprint(jwk: Dict[str, Any]) -> str:
    """RFC 7638 thumbprint (used as UCP-style kid where required)."""
    if jwk["kty"] == "OKP":
        members = {"crv": jwk["crv"], "kty": "OKP", "x": jwk["x"]}
    elif jwk["kty"] == "EC":
        members = {"crv": jwk["crv"], "kty": "EC", "x": jwk["x"], "y": jwk["y"]}
    else:
        raise ValueError("unsupported kty")
    blob = json.dumps(members, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b64url(hashlib.sha256(blob).digest())


def public_only(jwk: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in jwk.items() if k in ("kty", "crv", "x", "y", "kid", "alg", "use")}


def jwk_to_public_key(jwk: Dict[str, Any]):
    if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
        return ed25519.Ed25519PublicKey.from_public_bytes(b64url_decode(jwk["x"]))
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        x = int.from_bytes(b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(b64url_decode(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    raise ValueError(f"unsupported JWK {jwk.get('kty')}/{jwk.get('crv')}")


def sign_bytes(private_key, alg: str, data: bytes) -> bytes:
    if alg in ("EdDSA", "ed25519"):
        return private_key.sign(data)
    if alg in ("ES256", "ecdsa-p256-sha256"):
        der = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")
    raise ValueError(f"unsupported alg {alg}")


def verify_bytes(public_key, alg: str, data: bytes, signature: bytes) -> bool:
    try:
        if alg in ("EdDSA", "ed25519"):
            public_key.verify(signature, data)
            return True
        if alg in ("ES256", "ecdsa-p256-sha256"):
            if len(signature) != 64:
                return False
            r = int.from_bytes(signature[:32], "big")
            s = int.from_bytes(signature[32:], "big")
            public_key.verify(encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
            return True
    except InvalidSignature:
        return False
    except Exception:
        return False
    return False


def alg_for_jwk(jwk: Dict[str, Any]) -> Optional[str]:
    if jwk.get("alg"):
        return jwk["alg"]
    if jwk.get("crv") == "Ed25519":
        return "EdDSA"
    if jwk.get("crv") == "P-256":
        return "ES256"
    return None
