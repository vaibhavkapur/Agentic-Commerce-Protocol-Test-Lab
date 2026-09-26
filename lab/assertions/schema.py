"""JSON Schema validation against the pinned protocol schema snapshots.

Schema names are ``<protocol>:<Definition>``:

* ``acp:CheckoutSession`` → ``#/$defs/CheckoutSession`` of the ACP 2026-04-17 bundle
* ``ucp:checkout`` → ``https://ucp.dev/schemas/shopping/checkout.json`` (full ref tree resolved from the pinned copy)
* ``ucp:error_response``, ``ucp:profile`` (business profile document)
* ``ap2:checkout_mandate``, ``ap2:payment_mandate``, ``ap2:checkout_receipt``, ``ap2:payment_receipt``

The lab never fetches schemas from the network at validation time.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .. import paths

ACP_BUNDLE = paths.PINNED / "acp" / "2026-04-17" / "schema.agentic_checkout.json"
UCP_ROOT = paths.PINNED / "ucp" / "2026-08-25" / "schemas"
AP2_ROOT = paths.PINNED / "ap2" / "v0.2.0"

UCP_BASE = "https://ucp.dev/schemas/"
AP2_BASE = "https://ap2-protocol.org/schemas/"


class SchemaError(ValueError):
    pass


@lru_cache(maxsize=None)
def _acp_bundle() -> Dict[str, Any]:
    return json.loads(ACP_BUNDLE.read_text(encoding="utf-8"))


def _load_tree(root: Path, base_uri: str) -> Registry:
    resources = []
    for p in root.rglob("*.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        rel = p.relative_to(root).as_posix()
        uri = doc.get("$id") or (base_uri + rel)
        resources.append((uri, Resource.from_contents(doc, default_specification=DRAFT202012)))
        # Also register under the path-derived URI so relative refs resolve even when $id differs.
        path_uri = base_uri + rel
        if path_uri != uri:
            resources.append((path_uri, Resource.from_contents(doc, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _ucp_registry() -> Registry:
    return _load_tree(UCP_ROOT, UCP_BASE)


@lru_cache(maxsize=None)
def _ap2_registry() -> Registry:
    return _load_tree(AP2_ROOT, AP2_BASE)


@lru_cache(maxsize=None)
def validator_for(name: str) -> Draft202012Validator:
    proto, _, defn = name.partition(":")
    if proto == "acp":
        bundle = _acp_bundle()
        if defn not in bundle["$defs"]:
            raise SchemaError(f"unknown ACP definition {defn!r}")
        schema = {"$schema": bundle["$schema"], "$id": bundle["$id"] + f"#def-{defn}", "$defs": bundle["$defs"],
                  "$ref": f"#/$defs/{defn}"}
        return Draft202012Validator(schema)
    if proto == "ucp":
        mapping = {
            "checkout": UCP_BASE + "shopping/checkout.json",
            "error_response": UCP_BASE + "common/types/error_response.json",
            "profile": UCP_BASE + "profile.json#/$defs/business_schema",
            "order": UCP_BASE + "shopping/order.json",
        }
        if defn not in mapping:
            raise SchemaError(f"unknown UCP definition {defn!r}")
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": mapping[defn]}
        return Draft202012Validator(schema, registry=_ucp_registry())
    if proto == "ap2":
        mapping = {
            "checkout_mandate": AP2_BASE + "checkout_mandate.json",
            "payment_mandate": AP2_BASE + "payment_mandate.json",
            "open_checkout_mandate": AP2_BASE + "open_checkout_mandate",
            "open_payment_mandate": AP2_BASE + "open_payment_mandate",
            "checkout_receipt": AP2_BASE + "checkout_receipt.json",
            "payment_receipt": AP2_BASE + "payment_receipt.json",
        }
        if defn not in mapping:
            raise SchemaError(f"unknown AP2 definition {defn!r}")
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": mapping[defn]}
        return Draft202012Validator(schema, registry=_ap2_registry())
    raise SchemaError(f"unknown schema namespace {proto!r}")


def validate(name: str, instance: Any) -> Tuple[bool, List[str]]:
    """Validate ``instance``; returns (ok, human-readable error list)."""
    v = validator_for(name)
    errors = []
    for e in sorted(v.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        loc = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        errors.append(f"{loc}: {e.message}")
    return (not errors), errors[:20]
