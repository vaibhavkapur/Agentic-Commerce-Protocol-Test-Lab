"""Named oracles callable from scenario assertions (``kind: oracle``).

Each oracle receives the driver context and the referenced step outcome and
returns ``(value, detail)``. The assertion compares ``value`` with ``equals``
(default ``true``). Oracles raise :class:`OracleInconclusive` when the evidence
needed to compute a value is missing.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

from . import rfc9421_independent as rfc9421
from . import sdjwt_independent as sdjwt


class OracleInconclusive(Exception):
    pass


def tap_signature_independently_valid(ctx, step) -> Tuple[Any, str]:
    s = step.observations.get("signing")
    if not s or not step.request:
        raise OracleInconclusive("step has no signing record")
    report = rfc9421.expected_verdict(
        method=step.request["method"], sent_authority=s["sent_authority"], sent_path=s["sent_path"],
        sent_headers=_unredacted_headers(step), sent_body=_sent_body(step),
        signature_input=s["signature_input"], signature_b64=s["signature_b64"], public_jwk=s["public_jwk"], now=ctx.clock.now_int())
    return report["verdict"] == "verified", "; ".join(report["reasons"]) or "signature verifies over the request as sent"


def tap_verdict_matches_independent(ctx, step) -> Tuple[Any, str]:
    valid, detail = tap_signature_independently_valid(ctx, step)
    verifier = step.observations.get("verifier", {}).get("result")
    expected = "verified" if valid else "rejected"
    return verifier == expected, f"independent oracle: {expected} ({detail}); target verifier: {verifier}"


def _unredacted_headers(step) -> Dict[str, str]:
    # TAP requests carry no secrets; the redacted request headers are the sent headers.
    return dict(step.request.get("headers", {}))


def _sent_body(step) -> bytes:
    body = step.request.get("body")
    if isinstance(body, dict) and "_raw" in body:
        return body["_raw"].encode("utf-8")
    if body is None:
        return b""
    import json

    return json.dumps(body).encode("utf-8")


def ap2_receipt_signature_valid(ctx, step) -> Tuple[Any, str]:
    if "receipt_signature_valid" not in step.observations:
        raise OracleInconclusive("no receipt JWT observed")
    return step.observations["receipt_signature_valid"], "receipt JWT verified with the merchant/credential-provider public key"


def ap2_receipt_reference_matches(ctx, step) -> Tuple[Any, str]:
    if "receipt_reference_matches" not in step.observations:
        raise OracleInconclusive("no receipt observed")
    return step.observations["receipt_reference_matches"], "receipt.reference == hash(presentation) computed independently"


def ap2_receipt_schema_valid(ctx, step) -> Tuple[Any, str]:
    if "receipt_schema_valid" not in step.observations:
        raise OracleInconclusive("no receipt observed")
    return step.observations["receipt_schema_valid"], "; ".join(step.notes) or "receipt matches pinned schema"


def ap2_checkout_binding_independent(ctx, step) -> Tuple[Any, str]:
    bundle = ctx.state.get("checkout_mandate")
    jwk = (ctx.capability_snapshot or {}).get("merchant_public_jwk")
    if bundle is None or not jwk:
        raise OracleInconclusive("no mandate or merchant key available")
    report = sdjwt.checkout_binding_report(bundle.presentation, jwk, ctx.state.get("checkout_id"))
    return report["ok"], str({k: v for k, v in report.items() if k != "closed"})


def _totals_consistent(totals, *, discount_sign: int) -> Tuple[Any, str]:
    by = {}
    for t in totals or []:
        by.setdefault(t.get("type"), 0)
        by[t["type"]] += int(t.get("amount", 0))
    if "total" not in by or "subtotal" not in by:
        raise OracleInconclusive("totals lack subtotal/total")
    expected = by["subtotal"] + discount_sign * by.get("discount", 0) + by.get("fulfillment", 0) + by.get("tax", 0) + by.get("fee", 0)
    return by["total"] == expected, f"total={by['total']} expected subtotal{'-' if discount_sign < 0 else '+'}discount+fulfillment+tax+fee={expected}"


def acp_totals_consistent(ctx, step) -> Tuple[Any, str]:
    body = step.body if isinstance(step.body, dict) else {}
    return _totals_consistent(body.get("totals"), discount_sign=-1)


def ucp_totals_consistent(ctx, step) -> Tuple[Any, str]:
    body = step.body if isinstance(step.body, dict) else {}
    return _totals_consistent(body.get("totals"), discount_sign=+1)


def ucp_totals_exactly_one_subtotal_and_total(ctx, step) -> Tuple[Any, str]:
    body = step.body if isinstance(step.body, dict) else {}
    types = [t.get("type") for t in body.get("totals", [])]
    return (types.count("subtotal") == 1 and types.count("total") == 1), f"types={types}"


ORACLES: Dict[str, Callable] = {
    "tap_signature_independently_valid": tap_signature_independently_valid,
    "tap_verdict_matches_independent": tap_verdict_matches_independent,
    "ap2_receipt_signature_valid": ap2_receipt_signature_valid,
    "ap2_receipt_reference_matches": ap2_receipt_reference_matches,
    "ap2_receipt_schema_valid": ap2_receipt_schema_valid,
    "ap2_checkout_binding_independent": ap2_checkout_binding_independent,
    "acp_totals_consistent": acp_totals_consistent,
    "ucp_totals_consistent": ucp_totals_consistent,
    "ucp_totals_exactly_one_subtotal_and_total": ucp_totals_exactly_one_subtotal_and_total,
}
