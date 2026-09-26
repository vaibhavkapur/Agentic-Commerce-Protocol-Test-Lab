"""Minimal SD-JWT and Delegate SD-JWT chain primitives for the AP2 v0.2 fixtures.

Encoding rules follow SD-JWT (draft-ietf-oauth-selective-disclosure-jwt):

* a disclosure is ``b64url(JSON([salt, name, value]))`` for object properties and
  ``b64url(JSON([salt, value]))`` for array elements;
* a digest is ``b64url(sha256(ascii(disclosure)))``;
* a presentation is ``jwt~disc~disc~...~`` and chained links append further
  ``kb+sd-jwt`` JWTs, each with ``sd_hash`` over the preceding presentation.

This module is deliberately independent of any AP2 SDK: it is fixture material
whose behavior is pinned in ``manifests/profiles/ap2-mandates-v0.2.0.yaml``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .keys import b64url, b64url_decode

SD_ALG = "sha-256"
TYP_SD_JWT = "dc+sd-jwt"
TYP_KB_SD_JWT = "kb+sd-jwt"


def encode_disclosure(salt: str, value: Any, name: Optional[str] = None) -> str:
    arr = [salt, name, value] if name is not None else [salt, value]
    return b64url(json.dumps(arr, separators=(",", ":")).encode("utf-8"))


def decode_disclosure(disclosure: str) -> List[Any]:
    arr = json.loads(b64url_decode(disclosure))
    if not isinstance(arr, list) or len(arr) not in (2, 3):
        raise ValueError("malformed disclosure")
    return arr


def digest_disclosure(disclosure: str) -> str:
    return b64url(hashlib.sha256(disclosure.encode("ascii")).digest())


def sd_hash(presentation: str) -> str:
    """Hash of a presentation string, as used for ``sd_hash`` and receipt ``reference``."""
    return b64url(hashlib.sha256(presentation.encode("ascii")).digest())


@dataclass
class Link:
    jwt: str
    disclosures: List[str] = field(default_factory=list)

    def serialize(self) -> str:
        return "~".join([self.jwt] + self.disclosures) + "~"


def serialize_chain(links: List[Link]) -> str:
    return "".join(link.serialize() for link in links)


def _looks_like_jwt(part: str) -> bool:
    if part.count(".") != 2:
        return False
    try:
        hdr = json.loads(b64url_decode(part.split(".")[0]))
        return isinstance(hdr, dict) and "alg" in hdr
    except Exception:
        return False


def parse_chain(presentation: str) -> List[Link]:
    if not presentation.endswith("~"):
        raise ValueError("presentation must end with '~'")
    parts = presentation[:-1].split("~")
    links: List[Link] = []
    for part in parts:
        if not part:
            raise ValueError("empty element in presentation")
        if _looks_like_jwt(part):
            links.append(Link(jwt=part))
        else:
            if not links:
                raise ValueError("presentation must start with a JWT")
            links[-1].disclosures.append(part)
    if not links:
        raise ValueError("no JWT in presentation")
    return links


def presentation_prefix(links: List[Link], upto: int) -> str:
    """Serialization of links[0:upto] — the input to ``sd_hash`` of link ``upto``."""
    return serialize_chain(links[:upto])


class DisclosureIndex:
    """Digest -> decoded disclosure lookup with use tracking (unused disclosures are a smell)."""

    def __init__(self, disclosures: List[str]):
        self.by_digest: Dict[str, Tuple[str, List[Any]]] = {}
        for d in disclosures:
            self.by_digest[digest_disclosure(d)] = (d, decode_disclosure(d))
        self.used: set = set()

    def resolve(self, value: Any) -> Any:
        """Recursively replace ``_sd`` digests and ``{"...": digest}`` array elements."""
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for k, v in value.items():
                if k == "_sd":
                    for dg in v:
                        hit = self.by_digest.get(dg)
                        if hit is None:
                            continue  # undisclosed claim
                        _, arr = hit
                        if len(arr) != 3:
                            raise ValueError("object disclosure must have three elements")
                        self.used.add(dg)
                        out[arr[1]] = self.resolve(arr[2])
                elif k == "_sd_alg":
                    continue
                else:
                    out[k] = self.resolve(v)
            return out
        if isinstance(value, list):
            out_list = []
            for item in value:
                if isinstance(item, dict) and set(item.keys()) == {"..."}:
                    hit = self.by_digest.get(item["..."])
                    if hit is None:
                        continue  # undisclosed array element
                    _, arr = hit
                    if len(arr) != 2:
                        raise ValueError("array disclosure must have two elements")
                    self.used.add(item["..."])
                    out_list.append(self.resolve(arr[1]))
                else:
                    out_list.append(self.resolve(item))
            return out_list
        return value

    def undisclosed_digests(self, value: Any) -> List[str]:
        """Digests referenced in ``value`` that have no matching disclosure."""
        missing: List[str] = []

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k == "_sd":
                        for dg in x:
                            if dg not in self.by_digest:
                                missing.append(dg)
                    else:
                        walk(x)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and set(item.keys()) == {"..."}:
                        if item["..."] not in self.by_digest:
                            missing.append(item["..."])
                    else:
                        walk(item)

        walk(value)
        return missing


def make_sd_object(rng, fields: Dict[str, Any], disclosable: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Build an object with plain ``fields`` and selectively disclosable ``disclosable`` claims."""
    obj = dict(fields)
    disclosures = []
    digests = []
    for name, value in disclosable.items():
        d = encode_disclosure(b64url(rng.bytes(16)), value, name=name)
        disclosures.append(d)
        digests.append(digest_disclosure(d))
    if digests:
        obj["_sd"] = sorted(digests)
    return obj, disclosures


def make_sd_array_element(rng, value: Any) -> Tuple[Dict[str, str], str]:
    d = encode_disclosure(b64url(rng.bytes(16)), value)
    return {"...": digest_disclosure(d)}, d
