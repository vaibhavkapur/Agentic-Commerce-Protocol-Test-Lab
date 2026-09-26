"""RFC 9421 HTTP Message Signatures: signature base, header serialization, signing.

Used by the TAP driver (signing requests as the agent) and by the TAP verifier
fixture. The independent oracle in ``lab.assertions.oracles.rfc9421_independent``
re-implements the signature base from the RFC text so that the driver and the
verifier are not the only two parties agreeing on canonicalization.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .keys import KeyHandle

DERIVED = {"@method", "@authority", "@path", "@target-uri", "@query", "@scheme"}


@dataclass
class SignatureParams:
    covered: List[str]
    created: Optional[int] = None
    expires: Optional[int] = None
    keyid: Optional[str] = None
    alg: Optional[str] = None
    nonce: Optional[str] = None
    tag: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def serialize(self) -> str:
        """Serialize as the value of ``@signature-params`` (RFC 9421 §2.3)."""
        inner = "(" + " ".join(f'"{c}"' for c in self.covered) + ")"
        parts = [inner]
        ordered: List[Tuple[str, Any]] = []
        for name in ("created", "expires", "nonce", "alg", "keyid", "tag"):
            val = getattr(self, name)
            if val is not None:
                ordered.append((name, val))
        ordered.extend(self.extra.items())
        for name, val in ordered:
            if isinstance(val, bool):
                parts.append(f";{name}=?{'1' if val else '0'}")
            elif isinstance(val, int):
                parts.append(f";{name}={val}")
            else:
                parts.append(f';{name}="{val}"')
        return "".join(parts)


@dataclass
class HttpRequestView:
    method: str
    authority: str
    path: str
    headers: Dict[str, str]
    body: bytes = b""
    scheme: str = "http"
    query: str = ""

    def header(self, name: str) -> Optional[str]:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return None


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"


def component_value(req: HttpRequestView, component: str) -> str:
    if component == "@method":
        return req.method.upper()
    if component == "@authority":
        return req.authority.lower()
    if component == "@path":
        return req.path or "/"
    if component == "@scheme":
        return req.scheme.lower()
    if component == "@query":
        return "?" + req.query
    if component == "@target-uri":
        q = f"?{req.query}" if req.query else ""
        return f"{req.scheme}://{req.authority}{req.path}{q}"
    if component.startswith("@"):
        raise ValueError(f"unsupported derived component {component}")
    value = req.header(component)
    if value is None:
        raise ValueError(f"covered header {component!r} is absent")
    return re.sub(r"\s+", " ", value.strip())


def signature_base(req: HttpRequestView, params: SignatureParams) -> str:
    lines = [f'"{c}": {component_value(req, c)}' for c in params.covered]
    lines.append(f'"@signature-params": {params.serialize()}')
    return "\n".join(lines)


def sign_request(req: HttpRequestView, key: KeyHandle, params: SignatureParams, label: str = "sig1") -> Dict[str, str]:
    """Return the ``Signature-Input`` and ``Signature`` headers for ``req``."""
    base = signature_base(req, params)
    sig = key.sign(base.encode("utf-8"))
    return {
        "Signature-Input": f"{label}={params.serialize()}",
        "Signature": f"{label}=:{base64.b64encode(sig).decode('ascii')}:",
    }


_INPUT_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)=\(([^)]*)\)(.*)$", re.S)
_PARAM_RE = re.compile(r'\s*;\s*([A-Za-z]+)=("(?:[^"\\]|\\.)*"|\?[01]|-?[0-9]+)')
_SIG_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)=:([A-Za-z0-9+/=]+):\s*$")


def parse_signature_input(value: str) -> Tuple[str, SignatureParams, str]:
    """Parse ``label=("a" "b");p=v...`` → (label, params, raw_params_text)."""
    m = _INPUT_RE.match(value)
    if not m:
        raise ValueError("Signature-Input is not an RFC 9421 inner list")
    label, comps, rest = m.group(1), m.group(2), m.group(3)
    covered = [c.strip().strip('"') for c in comps.split() if c.strip()]
    params = SignatureParams(covered=covered)
    pos = 0
    while pos < len(rest):
        pm = _PARAM_RE.match(rest, pos)
        if not pm:
            if rest[pos:].strip() == "":
                break
            raise ValueError(f"malformed signature parameter near {rest[pos:pos + 24]!r}")
        name, raw = pm.group(1), pm.group(2)
        if raw.startswith('"'):
            val: Any = raw[1:-1]
        elif raw.startswith("?"):
            val = raw == "?1"
        else:
            val = int(raw)
        if name in ("created", "expires", "keyid", "alg", "nonce", "tag"):
            setattr(params, name, val)
        else:
            params.extra[name] = val
        pos = pm.end()
    raw_params = value.split("=", 1)[1].strip()
    return label, params, raw_params


def parse_signature(value: str) -> Tuple[str, bytes]:
    m = _SIG_RE.match(value)
    if not m:
        raise ValueError("Signature header is not label=:base64:")
    return m.group(1), base64.b64decode(m.group(2), validate=True)
