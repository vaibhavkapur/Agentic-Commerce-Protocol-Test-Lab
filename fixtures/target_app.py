"""Composed local target: merchant core + ACP/UCP adapters + AP2/TAP verifiers + registries.

Read-only observation endpoints (``/_lab/observe/...``) and admin endpoints
(``/_lab/admin/...``) are the lab's independent oracles and fixture controls.
They are never routed through the fault proxy and never count as protocol traffic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lab.runner.clock import LabClock, LabRng

from fixtures.issuers.ap2_fixtures import Ap2FixtureBuilder
from fixtures.issuers.trust_material import AGENT_DIRECTORY_URL, AGENT_ID, ISSUER_ID, TrustMaterial
from fixtures.merchants.acp_adapter import AcpAdapter
from fixtures.merchants.core import DomainError, MerchantCore
from fixtures.merchants.ucp_adapter import UcpAdapter
from fixtures.payments.simulator import PaymentSimulator
from fixtures.registries.agent_registry import AgentRegistry, IssuerRegistry
from fixtures.verifiers.ap2_endpoints import Ap2Endpoints
from fixtures.verifiers.ap2_verifier import Ap2Verifier
from fixtures.verifiers.tap_verifier import TapVerifier

DEFAULT_OPTIONS: Dict[str, Any] = {
    # merchant core
    "idempotency": "strict",
    "complete_order": "check_then_charge",
    "psp_idempotency": True,
    "error_body": "conformant",
    # capability toggles
    "acp_discount_extension": True,
    "ucp_discount_capability": True,
    "ucp_advertise_checkout": True,
    # verifiers
    "tap_trust_forwarded_host": False,
    "tap_replay_protection": True,
    "ap2_check_checkout_binding": True,
}


@dataclass
class TargetBundle:
    """Everything the runner needs to drive and observe a local target in-process."""

    app: Starlette
    core: MerchantCore
    payments: PaymentSimulator
    registry: AgentRegistry
    issuers: IssuerRegistry
    material: TrustMaterial
    ap2_builder: Ap2FixtureBuilder
    ap2_verifier: Ap2Verifier
    tap_verifier: TapVerifier
    options: Dict[str, Any]
    clock: LabClock

    def snapshot(self) -> Dict[str, Any]:
        return {
            "merchant": self.core.snapshot(),
            "payments": self.payments.snapshot(),
            "registry": self.registry.snapshot(),
            "tap": self.tap_verifier.snapshot(),
            "ap2_decisions": list(self.ap2_verifier.decisions),
        }


def build_target(clock: LabClock, rng: LabRng, options: Optional[Dict[str, Any]] = None) -> TargetBundle:
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    fixture_rng = rng.child("fixture")
    payments = PaymentSimulator(clock, fixture_rng.child("payments"))
    core = MerchantCore(clock, fixture_rng.child("merchant"), payments,
                        options={k: opts[k] for k in ("idempotency", "complete_order", "psp_idempotency", "error_body")})
    material = TrustMaterial.generate(rng)
    merchant = {"id": core.merchant_id, "name": core.merchant_name, "website": core.merchant_website}

    issuers = IssuerRegistry()
    issuers.trust(ISSUER_ID, material.issuer)
    registry = AgentRegistry(clock)

    def seed_registry() -> None:
        registry.agents.clear()
        registry.keys.clear()
        registry.audit.clear()
        registry.register_agent(AGENT_ID, "Lab Shopping Agent", AGENT_DIRECTORY_URL)
        registry.register_key(AGENT_ID, material.agent, description="primary Ed25519 key")
        registry.register_key(AGENT_ID, material.agent_secondary, description="secondary P-256 key")

    seed_registry()

    ap2_builder = Ap2FixtureBuilder(clock, rng, material)
    ap2_verifier = Ap2Verifier(clock, issuers, merchant_key=material.merchant, receipt_key=material.credential_provider,
                               merchant=merchant, options={"ap2_check_checkout_binding": opts["ap2_check_checkout_binding"]})
    ap2_endpoints = Ap2Endpoints(core, ap2_verifier, ap2_builder, merchant)
    tap_verifier = TapVerifier(clock, registry, options={"tap_trust_forwarded_host": opts["tap_trust_forwarded_host"],
                                                         "tap_replay_protection": opts["tap_replay_protection"]})
    acp = AcpAdapter(core, discount_extension=opts["acp_discount_extension"])
    ucp = UcpAdapter(core, discount_capability=opts["ucp_discount_capability"], advertise_checkout=opts["ucp_advertise_checkout"])

    # ----------------------------------------------------------- observation
    async def observe_orders(request: Request):
        cid = request.query_params.get("checkout_id")
        orders = core.orders_for_checkout(cid) if cid else list(core.orders.values())
        return JSONResponse({"orders": [o.to_dict() for o in orders]})

    async def observe_payments(request: Request):
        cid = request.query_params.get("checkout_id")
        recs = payments.for_checkout(cid) if cid else list(payments.payments.values())
        return JSONResponse({"payments": [p.to_dict() for p in recs]})

    async def observe_checkout(request: Request):
        try:
            chk = core.get_checkout(request.path_params["checkout_id"])
        except DomainError as exc:
            return JSONResponse({"error": exc.code}, status_code=404)
        return JSONResponse(chk.to_dict())

    async def observe_events(request: Request):
        return JSONResponse({"merchant_journal": core.journal, "payment_journal": payments.journal})

    async def observe_idempotency(request: Request):
        return JSONResponse({"records": [r.to_dict() for r in core.idempotency.values()]})

    async def observe_verifiers(request: Request):
        return JSONResponse({"tap": tap_verifier.snapshot(), "ap2_decisions": ap2_verifier.decisions})

    async def observe_registry(request: Request):
        return JSONResponse(registry.snapshot())

    async def observe_capabilities(request: Request):
        return JSONResponse({"options": opts, "revision": "local"})

    # ----------------------------------------------------------------- admin
    async def admin_reset(request: Request):
        core.reset()
        seed_registry()
        tap_verifier.replay_cache.clear()
        tap_verifier.decisions.clear()
        ap2_verifier.decisions.clear()
        ap2_endpoints.issued_checkout_jwts.clear()
        return JSONResponse({"ok": True, "at": clock.iso()})

    async def admin_inventory(request: Request):
        body = json.loads(await request.body() or b"{}")
        try:
            inv = core.set_inventory(request.path_params["sku"], unit_amount=body.get("unit_amount"), stock=body.get("stock"))
        except DomainError as exc:
            return JSONResponse({"error": exc.code}, status_code=404)
        return JSONResponse({"sku": request.path_params["sku"], "inventory": inv})

    async def admin_registry_revoke(request: Request):
        registry.revoke_key(request.path_params["key_id"])
        return JSONResponse({"ok": True})

    async def admin_registry_rotate(request: Request):
        registry.rotate_key(AGENT_ID, material.agent_rotated)
        return JSONResponse({"ok": True, "new_key_id": material.agent_rotated.kid})

    async def admin_clock_advance(request: Request):
        body = json.loads(await request.body() or b"{}")
        clock.advance(float(body.get("seconds", 0)))
        return JSONResponse({"now": clock.now_int()})

    async def health(request: Request):
        return JSONResponse({"ok": True, "now": clock.iso()})

    routes = []
    routes += acp.routes()
    routes += ucp.routes()
    routes += ap2_endpoints.routes()
    routes += tap_verifier.routes()
    routes += registry.routes()
    routes += [
        Route("/_lab/health", health, methods=["GET"]),
        Route("/_lab/observe/orders", observe_orders, methods=["GET"]),
        Route("/_lab/observe/payments", observe_payments, methods=["GET"]),
        Route("/_lab/observe/checkouts/{checkout_id}", observe_checkout, methods=["GET"]),
        Route("/_lab/observe/events", observe_events, methods=["GET"]),
        Route("/_lab/observe/idempotency", observe_idempotency, methods=["GET"]),
        Route("/_lab/observe/verifiers", observe_verifiers, methods=["GET"]),
        Route("/_lab/observe/registry", observe_registry, methods=["GET"]),
        Route("/_lab/observe/capabilities", observe_capabilities, methods=["GET"]),
        Route("/_lab/admin/reset", admin_reset, methods=["POST"]),
        Route("/_lab/admin/inventory/{sku}", admin_inventory, methods=["POST"]),
        Route("/_lab/admin/registry/revoke/{key_id}", admin_registry_revoke, methods=["POST"]),
        Route("/_lab/admin/registry/rotate", admin_registry_rotate, methods=["POST"]),
        Route("/_lab/admin/clock/advance", admin_clock_advance, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    return TargetBundle(app=app, core=core, payments=payments, registry=registry, issuers=issuers, material=material,
                        ap2_builder=ap2_builder, ap2_verifier=ap2_verifier, tap_verifier=tap_verifier, options=opts, clock=clock)
