"""AP2 v0.2.0 mandate-chain verifier fixture (merchant role and credential-provider role).

Implements the verification order from ``docs/ap2/specification.md#verification``
and ``docs/ap2/agent_authorization.md#verification-and-processing-rules``:

1. verify and process the SD-JWT chain (signatures, ``sd_hash`` linkage, key binding
   through ``cnf``, ``aud``, validity windows);
2. claims present in open mandate content must be unchanged in the closed content;
3. every constraint from every open mandate is evaluated against the closed content;
   unknown constraint types fail evaluation (``unresolved_constraint``).

Merchant-specific: ``checkout_hash`` must equal the hash of the disclosed
``checkout_jwt``; the checkout JWT must be merchant-signed and must be the
checkout being completed. Verifier decisions are returned as signed receipts
whose ``reference`` is the hash of the received presentation.

The broken reference target disables the checkout-binding check
(``ap2_check_checkout_binding=False``) — it authenticates the chain but does not
verify purchase authority for *this* checkout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fixtures.issuers.ap2_fixtures import (
    CT_ALLOWED_MERCHANTS,
    CT_ALLOWED_PAYEES,
    CT_AMOUNT_RANGE,
    CT_LINE_ITEMS,
    VCT_CHECKOUT,
    VCT_CHECKOUT_OPEN,
    VCT_PAYMENT,
    VCT_PAYMENT_OPEN,
    VCT_USER_CREDENTIAL,
    checkout_hash,
)
from fixtures.issuers.jws import sign_compact, split_compact, verify_compact
from fixtures.issuers.keys import KeyHandle, b64url_decode
from fixtures.issuers.sdjwt import TYP_KB_SD_JWT, TYP_SD_JWT, DisclosureIndex, Link, parse_chain, presentation_prefix, sd_hash
from fixtures.registries.agent_registry import IssuerRegistry, TrustError

ALLOWED_ALGS = ("ES256", "EdDSA")
MAX_KB_AGE = 600
MAX_SKEW = 60

ERR_INVALID_CREDENTIAL = "invalid_credential"
ERR_UNRESOLVED_CONSTRAINT = "unresolved_constraint"
ERR_INVALID_MANDATE = "invalid_mandate"


class MandateError(Exception):
    def __init__(self, code: str, description: str, step: str):
        super().__init__(description)
        self.code = code
        self.description = description
        self.step = step


@dataclass
class ChainResult:
    closed_content: Dict[str, Any]
    open_contents: List[Dict[str, Any]]
    links: List[Link]
    final_signer_jwk: Dict[str, Any]
    steps: List[str] = field(default_factory=list)


class Ap2Verifier:
    def __init__(self, clock, issuer_registry: IssuerRegistry, *, merchant_key: KeyHandle, receipt_key: KeyHandle,
                 merchant: Dict[str, Any], options: Optional[Dict[str, Any]] = None):
        self.clock = clock
        self.issuers = issuer_registry
        self.merchant_key = merchant_key
        self.receipt_key = receipt_key
        self.merchant = merchant
        self.options = {"ap2_check_checkout_binding": True}
        self.options.update(options or {})
        self.decisions: List[Dict[str, Any]] = []
        self.seen_nonces: set = set()

    # ----------------------------------------------------------- chain core
    def verify_chain(self, presentation: str, *, expected_aud: str, closed_vct: str, open_vct: str) -> ChainResult:
        steps: List[str] = []
        try:
            links = parse_chain(presentation)
        except ValueError as exc:
            raise MandateError(ERR_INVALID_CREDENTIAL, f"malformed presentation: {exc}", "parse")
        steps.append(f"parsed {len(links)} links")
        if len(links) < 2:
            raise MandateError(ERR_INVALID_CREDENTIAL, "chain needs an issuer link and at least one mandate link", "structure")

        # Link 0: issuer-signed user credential
        hdr, payload, _, _ = split_compact(links[0].jwt)
        if hdr.get("typ") != TYP_SD_JWT:
            raise MandateError(ERR_INVALID_CREDENTIAL, f"link 0 typ {hdr.get('typ')!r} is not {TYP_SD_JWT}", "issuer_typ")
        if hdr.get("alg") not in ALLOWED_ALGS:
            raise MandateError(ERR_INVALID_CREDENTIAL, f"algorithm {hdr.get('alg')!r} is not allowed", "issuer_alg")
        try:
            issuer_jwk = self.issuers.resolve(payload.get("iss"), hdr.get("kid"))
        except TrustError as exc:
            raise MandateError(ERR_INVALID_CREDENTIAL, exc.detail, "issuer_trust")
        try:
            payload = verify_compact(links[0].jwt, issuer_jwk, expected_alg=hdr["alg"])
        except ValueError as exc:
            raise MandateError(ERR_INVALID_CREDENTIAL, f"issuer signature: {exc}", "issuer_signature")
        now = self.clock.now_int()
        if payload.get("exp") is not None and payload["exp"] < now:
            raise MandateError(ERR_INVALID_CREDENTIAL, "user credential expired", "issuer_exp")
        if payload.get("vct") != VCT_USER_CREDENTIAL:
            raise MandateError(ERR_INVALID_CREDENTIAL, f"unexpected credential vct {payload.get('vct')!r}", "issuer_vct")
        holder_jwk = (payload.get("cnf") or {}).get("jwk")
        if not holder_jwk:
            raise MandateError(ERR_INVALID_CREDENTIAL, "user credential lacks cnf.jwk", "issuer_cnf")
        steps.append("issuer link verified")

        open_contents: List[Dict[str, Any]] = []
        closed_content: Optional[Dict[str, Any]] = None
        signer_jwk = holder_jwk
        for i in range(1, len(links)):
            link = links[i]
            hdr, payload, _, _ = split_compact(link.jwt)
            if hdr.get("typ") != TYP_KB_SD_JWT:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} typ {hdr.get('typ')!r} is not {TYP_KB_SD_JWT}", "kb_typ")
            if hdr.get("alg") not in ALLOWED_ALGS:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"algorithm {hdr.get('alg')!r} is not allowed", "kb_alg")
            try:
                payload = verify_compact(link.jwt, signer_jwk, expected_alg=hdr["alg"])
            except ValueError as exc:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} key binding signature: {exc}", "kb_signature")
            expected_sd_hash = sd_hash(presentation_prefix(links, i))
            if payload.get("sd_hash") != expected_sd_hash:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} sd_hash does not cover the preceding presentation", "kb_sd_hash")
            if payload.get("aud") != expected_aud:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} aud {payload.get('aud')!r} != {expected_aud!r}", "kb_aud")
            iat = payload.get("iat")
            if not isinstance(iat, int) or iat > now + MAX_SKEW or iat < now - MAX_KB_AGE:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} iat {iat!r} outside acceptance window", "kb_iat")
            index = DisclosureIndex(link.disclosures)
            delegate = payload.get("delegate_payload")
            if not isinstance(delegate, list) or len(delegate) != 1:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} delegate_payload must carry exactly one mandate", "kb_delegate")
            resolved = index.resolve(delegate)
            if len(resolved) != 1 or not isinstance(resolved[0], dict):
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} mandate content is not disclosed", "kb_content_disclosure")
            content = resolved[0]
            raw_content = self._raw_content(delegate, index)
            vct = content.get("vct")
            if closed_content is not None:
                raise MandateError(ERR_INVALID_CREDENTIAL, "links after a closed mandate are not allowed", "kb_after_closed")
            if vct == open_vct:
                cnf = (content.get("cnf") or {}).get("jwk")
                if not cnf:
                    raise MandateError(ERR_INVALID_CREDENTIAL, "open mandate lacks cnf.jwk", "open_cnf")
                if content.get("exp") is not None and content["exp"] < now:
                    raise MandateError(ERR_INVALID_CREDENTIAL, "open mandate expired", "open_exp")
                open_contents.append(content)
                signer_jwk = cnf
                steps.append(f"link {i}: open mandate accepted; next signer bound to cnf")
            elif vct == closed_vct:
                content["_undisclosed"] = index.undisclosed_digests(raw_content)
                closed_content = content
                steps.append(f"link {i}: closed mandate content resolved")
            else:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"link {i} vct {vct!r} is not {open_vct!r} or {closed_vct!r}", "kb_vct")
        if closed_content is None:
            raise MandateError(ERR_INVALID_CREDENTIAL, "chain ends with an open mandate; no closed mandate presented", "no_closed")

        # Rule 2: open-mandate claims must be unchanged in the closed content.
        for oc in open_contents:
            for k, v in oc.items():
                if k in ("vct", "cnf", "constraints", "iat", "exp", "_sd", "_sd_alg"):
                    continue
                if k in closed_content and closed_content[k] != v:
                    raise MandateError(ERR_INVALID_MANDATE, f"closed mandate changed claim {k!r} fixed by an open mandate", "claim_drift")
        return ChainResult(closed_content=closed_content, open_contents=open_contents, links=links, final_signer_jwk=signer_jwk, steps=steps)

    @staticmethod
    def _raw_content(delegate: List[Any], index: DisclosureIndex) -> Any:
        element = delegate[0]
        hit = index.by_digest.get(element.get("...")) if isinstance(element, dict) else None
        return hit[1][1] if hit else {}

    # -------------------------------------------------------- merchant role
    def verify_checkout_mandate(self, presentation: str, *, expected_checkout_id: str) -> Tuple[Dict[str, Any], str]:
        """Return (receipt_payload, receipt_jwt) for the merchant's decision."""
        reference = sd_hash(presentation)
        try:
            chain = self.verify_chain(presentation, expected_aud=self.merchant["id"], closed_vct=VCT_CHECKOUT, open_vct=VCT_CHECKOUT_OPEN)
            closed = chain.closed_content
            checkout_jwt = closed.get("checkout_jwt")
            if not checkout_jwt:
                raise MandateError(ERR_INVALID_CREDENTIAL, "checkout_jwt was not disclosed; required for merchant verification", "missing_disclosure")
            if closed.get("checkout_hash") != checkout_hash(checkout_jwt):
                raise MandateError(ERR_INVALID_CREDENTIAL, "checkout_hash does not match the disclosed checkout_jwt", "checkout_hash")
            try:
                checkout = verify_compact(checkout_jwt, self.merchant_key.public_jwk, expected_alg="ES256")
            except ValueError as exc:
                raise MandateError(ERR_INVALID_CREDENTIAL, f"checkout_jwt is not merchant-signed: {exc}", "checkout_jwt_signature")
            if self.options.get("ap2_check_checkout_binding", True) and checkout.get("id") != expected_checkout_id:
                raise MandateError(ERR_INVALID_MANDATE,
                                   f"mandate authorizes checkout {checkout.get('id')!r}, not {expected_checkout_id!r}", "checkout_binding")
            for oc in chain.open_contents:
                self._evaluate_checkout_constraints(oc.get("constraints", []), checkout)
            receipt = self._receipt("Success", reference, order_id=f"pending:{expected_checkout_id}", steps=chain.steps)
        except MandateError as exc:
            receipt = self._receipt("Error", reference, error=exc.code, error_description=exc.description, failed_step=exc.step)
        jwt = sign_compact(self.merchant_key, {"typ": "JWT"}, receipt)
        self.decisions.append({"role": "merchant", "receipt": receipt, "at": self.clock.iso()})
        return receipt, jwt

    def _evaluate_checkout_constraints(self, constraints: List[Dict[str, Any]], checkout: Dict[str, Any]) -> None:
        for c in constraints:
            ctype = c.get("type")
            if ctype == CT_ALLOWED_MERCHANTS:
                allowed_ids = {m.get("id") for m in c.get("allowed", []) if isinstance(m, dict)}
                if self.merchant["id"] not in allowed_ids:
                    raise MandateError(ERR_INVALID_MANDATE, "merchant is not in the revealed allowed_merchants", "constraint_allowed_merchants")
            elif ctype == CT_LINE_ITEMS:
                if not self._line_items_satisfied(c.get("items", []), checkout.get("line_items", [])):
                    raise MandateError(ERR_INVALID_MANDATE, "checkout line items do not satisfy the line_items constraint", "constraint_line_items")
            else:
                raise MandateError(ERR_UNRESOLVED_CONSTRAINT, f"unknown constraint type {ctype!r}", "constraint_unknown")

    @staticmethod
    def _line_items_satisfied(requirements: List[Dict[str, Any]], line_items: List[Dict[str, Any]]) -> bool:
        """Bipartite max-flow between requirement entries and checkout item ids (spec's suggested evaluation)."""
        supply = {}
        for li in line_items:
            iid = li.get("item", {}).get("id")
            supply[iid] = supply.get(iid, 0) + int(li.get("quantity", 1))
        total_required = sum(int(r.get("quantity", 0)) for r in requirements)
        if total_required != sum(supply.values()):
            return False
        # Edmonds–Karp on a tiny graph.
        req_nodes = [f"r{i}" for i in range(len(requirements))]
        item_nodes = list(supply.keys())
        cap: Dict[str, Dict[str, int]] = {"s": {}, "t": {}}
        for i, r in enumerate(requirements):
            cap["s"][req_nodes[i]] = int(r.get("quantity", 0))
            cap.setdefault(req_nodes[i], {})
            for acc in r.get("acceptable_items", []):
                iid = acc.get("id") if isinstance(acc, dict) else None
                if iid in supply:
                    cap[req_nodes[i]][iid] = 10 ** 9
        for iid, q in supply.items():
            cap.setdefault(iid, {})["t"] = q
        flow = 0
        while True:
            parent = {"s": None}
            queue = ["s"]
            while queue and "t" not in parent:
                u = queue.pop(0)
                for v, cuv in cap.get(u, {}).items():
                    if cuv > 0 and v not in parent:
                        parent[v] = u
                        queue.append(v)
            if "t" not in parent:
                break
            path_flow = 10 ** 9
            v = "t"
            while parent[v] is not None:
                u = parent[v]
                path_flow = min(path_flow, cap[u][v])
                v = u
            v = "t"
            while parent[v] is not None:
                u = parent[v]
                cap[u][v] -= path_flow
                cap.setdefault(v, {})[u] = cap[v].get(u, 0) + path_flow
                v = u
            flow += path_flow
        return flow == total_required

    # ----------------------------------------------- credential provider role
    def verify_payment_mandate(self, presentation: str, *, expected_transaction_id: str, expected_amount: int,
                               expected_currency: str) -> Tuple[Dict[str, Any], str]:
        reference = sd_hash(presentation)
        try:
            chain = self.verify_chain(presentation, expected_aud="credential-provider.lab.test", closed_vct=VCT_PAYMENT, open_vct=VCT_PAYMENT_OPEN)
            closed = chain.closed_content
            for k in ("transaction_id", "payee", "payment_amount", "payment_instrument"):
                if k not in closed:
                    raise MandateError(ERR_INVALID_CREDENTIAL, f"closed payment mandate lacks {k}", "payment_schema")
            if closed["transaction_id"] != expected_transaction_id:
                raise MandateError(ERR_INVALID_MANDATE, "payment mandate transaction_id is not bound to this checkout", "transaction_binding")
            amt = closed["payment_amount"]
            if amt.get("amount") != expected_amount or amt.get("currency") != expected_currency:
                raise MandateError(ERR_INVALID_MANDATE, "payment_amount does not match the checkout total", "amount_binding")
            for oc in chain.open_contents:
                self._evaluate_payment_constraints(oc.get("constraints", []), closed)
            pid = f"pay_auth_{expected_transaction_id[:12]}"
            receipt = self._receipt("Success", reference, payment_id=pid, psp_confirmation_id=f"psp_{expected_transaction_id[12:24]}",
                                    network_confirmation_id=f"net_{expected_transaction_id[24:36]}", steps=chain.steps)
        except MandateError as exc:
            receipt = self._receipt("Error", reference, payment_id=f"pay_auth_{expected_transaction_id[:12]}", error=exc.code,
                                    error_description=exc.description, failed_step=exc.step)
        receipt["iss"] = "credential-provider.lab.test"
        jwt = sign_compact(self.receipt_key, {"typ": "JWT"}, receipt)
        self.decisions.append({"role": "credential_provider", "receipt": receipt, "at": self.clock.iso()})
        return receipt, jwt

    def _evaluate_payment_constraints(self, constraints: List[Dict[str, Any]], closed: Dict[str, Any]) -> None:
        for c in constraints:
            ctype = c.get("type")
            if ctype == CT_AMOUNT_RANGE:
                amt = closed["payment_amount"]
                if amt.get("currency") != c.get("currency"):
                    raise MandateError(ERR_INVALID_MANDATE, "payment currency differs from amount_range currency", "constraint_amount_currency")
                if amt.get("amount") > c.get("max", 0) or amt.get("amount") < c.get("min", 0):
                    raise MandateError(ERR_INVALID_MANDATE, f"amount {amt.get('amount')} outside [{c.get('min', 0)}, {c.get('max')}]", "constraint_amount_range")
            elif ctype == CT_ALLOWED_PAYEES:
                allowed_ids = {m.get("id") for m in c.get("allowed", []) if isinstance(m, dict)}
                if closed["payee"].get("id") not in allowed_ids:
                    raise MandateError(ERR_INVALID_MANDATE, "payee is not in the revealed allowed_payees", "constraint_allowed_payees")
            else:
                raise MandateError(ERR_UNRESOLVED_CONSTRAINT, f"unknown constraint type {ctype!r}", "constraint_unknown")

    # --------------------------------------------------------------- receipt
    def _receipt(self, status: str, reference: str, **extra) -> Dict[str, Any]:
        receipt: Dict[str, Any] = {"iss": self.merchant["id"], "iat": self.clock.now_int(), "status": status, "reference": reference}
        receipt.update({k: v for k, v in extra.items() if v is not None})
        return receipt
