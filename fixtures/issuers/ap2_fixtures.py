"""AP2 v0.2.0 mandate fixture builder (Checkout Mandate and Payment Mandate chains).

Structures follow ``docs/ap2/checkout_mandate.md`` and ``docs/ap2/payment_mandate.md``
at tag ``v0.2.0``: closed mandates are ``kb+sd-jwt`` links whose
``delegate_payload`` carries the mandate content as a selectively disclosable
array element; the closed Checkout Mandate discloses ``checkout_jwt`` and binds
it through ``checkout_hash``.

Negative fixtures are constructed *first* and then signed with the appropriate
synthetic key (plan §14): tampering after signing is only used when a broken
signature is the intended case.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from lab.runner.clock import LabClock, LabRng

from .jws import sign_compact, split_compact
from .keys import KeyHandle, b64url, b64url_decode, public_only
from .sdjwt import (
    SD_ALG,
    TYP_KB_SD_JWT,
    TYP_SD_JWT,
    Link,
    encode_disclosure,
    digest_disclosure,
    make_sd_array_element,
    make_sd_object,
    sd_hash,
    serialize_chain,
)
from .trust_material import CREDENTIAL_PROVIDER_AUDIENCE, ISSUER_ID, MERCHANT_AUDIENCE, ROGUE_ISSUER_ID, TrustMaterial

VCT_CHECKOUT = "mandate.checkout.1"
VCT_CHECKOUT_OPEN = "mandate.checkout.open.1"
VCT_PAYMENT = "mandate.payment.1"
VCT_PAYMENT_OPEN = "mandate.payment.open.1"
VCT_USER_CREDENTIAL = "urn:commerce-lab:user-credential:1"

CT_ALLOWED_MERCHANTS = "checkout.allowed_merchants"
CT_LINE_ITEMS = "checkout.line_items"
CT_AMOUNT_RANGE = "payment.amount_range"
CT_ALLOWED_PAYEES = "payment.allowed_payees"


def checkout_hash(checkout_jwt: str) -> str:
    return b64url(hashlib.sha256(checkout_jwt.encode("ascii")).digest())


@dataclass
class MandateBundle:
    presentation: str
    links: List[Link]
    checkout_jwt: Optional[str]
    checkout_hash: Optional[str]
    variant: str
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return {"variant": self.variant, "notes": self.notes, "checkout_hash": self.checkout_hash,
                "link_count": len(self.links), "presentation_length": len(self.presentation)}


class Ap2FixtureBuilder:
    def __init__(self, clock: LabClock, rng: LabRng, material: TrustMaterial):
        self.clock = clock
        self.rng = rng.child("ap2-fixtures")
        self.m = material

    # ------------------------------------------------------- merchant checkout
    def merchant_checkout_jwt(self, checkout: Dict[str, Any], *, signer: Optional[KeyHandle] = None) -> str:
        """Merchant-signed Checkout JWT. ES256 (non-deterministic signature) as the spec requires."""
        key = signer or self.m.merchant
        payload = dict(checkout)
        payload.setdefault("iat", self.clock.now_int())
        payload.setdefault("iss", MERCHANT_AUDIENCE)
        return sign_compact(key, {"typ": "JWT", "alg": key.alg, "kid": key.kid}, payload)

    # --------------------------------------------------------- chain pieces
    def user_credential_link(self, *, issuer: Optional[KeyHandle] = None, iss: str = ISSUER_ID,
                             holder: Optional[KeyHandle] = None, exp_offset: int = 3600, alg_override: Optional[str] = None) -> Link:
        issuer = issuer or self.m.issuer
        holder = holder or self.m.user_device
        now = self.clock.now_int()
        obj, disclosures = make_sd_object(
            self.rng,
            {"iss": iss, "sub": "user_lab_1", "iat": now, "exp": now + exp_offset, "vct": VCT_USER_CREDENTIAL,
             "cnf": {"jwk": public_only(holder.public_jwk)}, "_sd_alg": SD_ALG},
            {"given_name": "Lab", "family_name": "User"},
        )
        header = {"typ": TYP_SD_JWT, "alg": alg_override or issuer.alg, "kid": issuer.kid}
        return Link(jwt=sign_compact(issuer, header, obj), disclosures=disclosures)

    def _kb_link(self, signer: KeyHandle, *, content: Dict[str, Any], content_disclosures: List[str], prev: str,
                 aud: str, iat: Optional[int] = None) -> Link:
        element, disc = make_sd_array_element(self.rng, content)
        payload = {
            "delegate_payload": [element],
            "iat": iat if iat is not None else self.clock.now_int(),
            "aud": aud,
            "nonce": self.rng.hex(16),
            "sd_hash": sd_hash(prev),
            "_sd_alg": SD_ALG,
        }
        jwt = sign_compact(signer, {"typ": TYP_KB_SD_JWT, "alg": signer.alg, "kid": signer.kid}, payload)
        return Link(jwt=jwt, disclosures=[disc] + content_disclosures)

    def closed_checkout_content(self, checkout_jwt: str, *, hash_of: Optional[str] = None, disclose_jwt: bool = True):
        disclosure = encode_disclosure(b64url(self.rng.bytes(16)), checkout_jwt, name="checkout_jwt")
        content = {"vct": VCT_CHECKOUT, "checkout_hash": checkout_hash(hash_of or checkout_jwt), "_sd": [digest_disclosure(disclosure)]}
        return content, ([disclosure] if disclose_jwt else [])

    def open_checkout_content(self, agent: KeyHandle, constraints: List[Dict[str, Any]], *, exp_offset: int = 900) -> Dict[str, Any]:
        now = self.clock.now_int()
        return {"vct": VCT_CHECKOUT_OPEN, "cnf": {"jwk": public_only(agent.public_jwk)}, "constraints": constraints,
                "iat": now, "exp": now + exp_offset}

    def closed_payment_content(self, *, transaction_id: str, amount: int, currency: str, payee: Dict[str, Any],
                               instrument: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "vct": VCT_PAYMENT,
            "transaction_id": transaction_id,
            "payee": payee,
            "payment_amount": {"amount": amount, "currency": currency},
            "payment_instrument": instrument or {"id": "instr_lab_card_1", "type": "card", "description": "Card ••••4242"},
        }

    def open_payment_content(self, agent: KeyHandle, constraints: List[Dict[str, Any]], *, exp_offset: int = 900) -> Dict[str, Any]:
        now = self.clock.now_int()
        return {"vct": VCT_PAYMENT_OPEN, "cnf": {"jwk": public_only(agent.public_jwk)}, "constraints": constraints,
                "iat": now, "exp": now + exp_offset}

    # ------------------------------------------------------- checkout chains
    def build_checkout_mandate(self, variant: str, checkout_jwt: str, *, merchant: Dict[str, Any],
                               other_checkout_jwt: Optional[str] = None, allowed_skus: Optional[List[str]] = None) -> MandateBundle:
        """Build the checkout mandate chain for ``variant``.

        Variants: valid_direct, valid_autonomous, tampered_content, unknown_issuer,
        unsupported_algorithm, wrong_agent_key, expired, checkout_hash_mismatch,
        bound_to_other_checkout, missing_disclosure, constraint_violation,
        unknown_constraint, wrong_merchant_constraint.
        """
        m = self.m
        notes = ""
        if variant == "valid_direct":
            l0 = self.user_credential_link()
            content, discs = self.closed_checkout_content(checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "user credential → user-signed closed checkout mandate (human present)"
        elif variant in ("valid_autonomous", "wrong_agent_key", "constraint_violation", "unknown_constraint", "wrong_merchant_constraint", "expired"):
            skus = allowed_skus if allowed_skus is not None else self._skus_from_checkout(checkout_jwt)
            constraints: List[Dict[str, Any]] = [
                {"type": CT_ALLOWED_MERCHANTS, "allowed": [merchant if variant != "wrong_merchant_constraint"
                                                            else {"id": "other_merchant", "name": "Other Merchant"}]},
                {"type": CT_LINE_ITEMS, "items": [{"id": "req-1", "acceptable_items": [{"id": s, "title": s} for s in skus],
                                                   "quantity": self._total_quantity(checkout_jwt)}]},
            ]
            if variant == "constraint_violation":
                constraints[1]["items"][0]["acceptable_items"] = [{"id": "sku_socks_best", "title": "The Best Socks"}]
            if variant == "unknown_constraint":
                constraints.append({"type": "checkout.lab_unknown_constraint", "value": 1})
            l0 = self.user_credential_link()
            open_content = self.open_checkout_content(m.agent, constraints, exp_offset=(-60 if variant == "expired" else 900))
            l1 = self._kb_link(m.user_device, content=open_content, content_disclosures=[], prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            content, discs = self.closed_checkout_content(checkout_jwt)
            signer = m.agent_secondary if variant == "wrong_agent_key" else m.agent
            l2 = self._kb_link(signer, content=content, content_disclosures=discs, prev=serialize_chain([l0, l1]), aud=MERCHANT_AUDIENCE)
            links = [l0, l1, l2]
            notes = f"user credential → user-signed open mandate (cnf=agent) → agent-signed closed mandate [{variant}]"
        elif variant == "tampered_content":
            l0 = self.user_credential_link()
            content, discs = self.closed_checkout_content(checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            hdr, payload, _sig, _ = split_compact(l1.jwt)
            payload["aud"] = "attacker-merchant"
            parts = l1.jwt.split(".")
            parts[1] = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
            l1.jwt = ".".join(parts)
            links = [l0, l1]
            notes = "closed mandate payload mutated after signing (aud changed); signature must fail"
        elif variant == "unknown_issuer":
            l0 = self.user_credential_link(issuer=m.rogue_issuer, iss=ROGUE_ISSUER_ID)
            content, discs = self.closed_checkout_content(checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "user credential issued by an issuer that is not in the trusted issuer registry"
        elif variant == "unsupported_algorithm":
            l0 = self.user_credential_link(alg_override="ES384")
            content, discs = self.closed_checkout_content(checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "issuer JWT header declares an algorithm outside the profile allowlist"
        elif variant == "checkout_hash_mismatch":
            assert other_checkout_jwt
            l0 = self.user_credential_link()
            content, discs = self.closed_checkout_content(checkout_jwt, hash_of=other_checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "checkout_hash does not match the disclosed checkout_jwt"
        elif variant == "bound_to_other_checkout":
            assert other_checkout_jwt
            l0 = self.user_credential_link()
            content, discs = self.closed_checkout_content(other_checkout_jwt)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "well-formed, authentic mandate bound to a different checkout than the one being completed"
            checkout_jwt = other_checkout_jwt
        elif variant == "missing_disclosure":
            l0 = self.user_credential_link()
            content, discs = self.closed_checkout_content(checkout_jwt, disclose_jwt=False)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=discs, prev=l0.serialize(), aud=MERCHANT_AUDIENCE)
            links = [l0, l1]
            notes = "checkout_jwt disclosure withheld; merchant cannot verify the binding"
        else:
            raise ValueError(f"unknown checkout mandate variant {variant}")
        return MandateBundle(presentation=serialize_chain(links), links=links, checkout_jwt=checkout_jwt,
                             checkout_hash=checkout_hash(checkout_jwt), variant=variant, notes=notes)

    # -------------------------------------------------------- payment chains
    def build_payment_mandate(self, variant: str, *, checkout_jwt: str, amount: int, currency: str, payee: Dict[str, Any],
                              other_checkout_jwt: Optional[str] = None) -> MandateBundle:
        """Variants: valid_direct, valid_autonomous, transaction_mismatch, amount_out_of_range, wrong_payee."""
        m = self.m
        tx = checkout_hash(checkout_jwt)
        if variant == "valid_direct":
            l0 = self.user_credential_link()
            content = self.closed_payment_content(transaction_id=tx, amount=amount, currency=currency, payee=payee)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=[], prev=l0.serialize(), aud=CREDENTIAL_PROVIDER_AUDIENCE)
            links = [l0, l1]
            notes = "user-signed closed payment mandate bound to the checkout hash"
        elif variant in ("valid_autonomous", "amount_out_of_range", "wrong_payee"):
            max_amount = amount - 1 if variant == "amount_out_of_range" else amount + 5000
            allowed = [payee] if variant != "wrong_payee" else [{"id": "other_merchant", "name": "Other Merchant"}]
            constraints = [
                {"type": CT_AMOUNT_RANGE, "currency": currency, "min": 0, "max": max_amount},
                {"type": CT_ALLOWED_PAYEES, "allowed": allowed},
            ]
            l0 = self.user_credential_link()
            open_content = self.open_payment_content(m.agent, constraints)
            l1 = self._kb_link(m.user_device, content=open_content, content_disclosures=[], prev=l0.serialize(), aud=CREDENTIAL_PROVIDER_AUDIENCE)
            content = self.closed_payment_content(transaction_id=tx, amount=amount, currency=currency, payee=payee)
            l2 = self._kb_link(m.agent, content=content, content_disclosures=[], prev=serialize_chain([l0, l1]), aud=CREDENTIAL_PROVIDER_AUDIENCE)
            links = [l0, l1, l2]
            notes = f"open payment mandate with constraints → agent-signed closed payment mandate [{variant}]"
        elif variant == "transaction_mismatch":
            assert other_checkout_jwt
            l0 = self.user_credential_link()
            content = self.closed_payment_content(transaction_id=checkout_hash(other_checkout_jwt), amount=amount, currency=currency, payee=payee)
            l1 = self._kb_link(m.user_device, content=content, content_disclosures=[], prev=l0.serialize(), aud=CREDENTIAL_PROVIDER_AUDIENCE)
            links = [l0, l1]
            notes = "payment mandate transaction_id references a different checkout than the checkout mandate"
        else:
            raise ValueError(f"unknown payment mandate variant {variant}")
        return MandateBundle(presentation=serialize_chain(links), links=links, checkout_jwt=checkout_jwt, checkout_hash=tx,
                             variant=variant, notes=notes)

    # ---------------------------------------------------------------- utils
    def _checkout_payload(self, checkout_jwt: str) -> Dict[str, Any]:
        return json.loads(b64url_decode(checkout_jwt.split(".")[1]))

    def _skus_from_checkout(self, checkout_jwt: str) -> List[str]:
        return [li["item"]["id"] for li in self._checkout_payload(checkout_jwt).get("line_items", [])]

    def _total_quantity(self, checkout_jwt: str) -> int:
        return sum(li.get("quantity", 1) for li in self._checkout_payload(checkout_jwt).get("line_items", []))
