"""ACP 2026-04-17 driver: the lab acts as the Agent against a Seller target."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from drivers.base import DriverContext, MetricUnavailable, ProtocolDriver, StepOutcome
from lab.runner.environment import Environment, HarnessError

API_KEY = "lab_acp_api_key_synthetic"
API_VERSION = "2026-04-17"
HANDLER_ID = "handler_lab_sim_01"
DEFAULT_BUYER = {"first_name": "Lab", "last_name": "Buyer", "email": "lab.buyer@example.test"}


class AcpDriver(ProtocolDriver):
    protocol = "acp"

    # -------------------------------------------------------------- discovery
    async def inspect_target(self, env: Environment) -> Dict[str, Any]:
        try:
            r = await env.protocol_client.get("/.well-known/acp.json")
        except httpx.HTTPError as exc:
            return {"capabilities": [], "error": f"discovery unreachable: {exc}", "raw": None}
        if r.status_code != 200:
            return {"capabilities": [], "error": f"discovery returned {r.status_code}", "raw": None}
        doc = r.json()
        caps: List[str] = []
        c = doc.get("capabilities", {})
        for svc in c.get("services", []):
            caps.append(svc)
        for ext in c.get("extensions", []) or []:
            if isinstance(ext, dict) and ext.get("name"):
                caps.append(f"extension:{ext['name']}")
        return {"capabilities": caps, "protocol_version": doc.get("protocol", {}).get("version"),
                "supported_versions": doc.get("protocol", {}).get("supported_versions"), "api_base_url": doc.get("api_base_url"), "raw": doc}

    # ---------------------------------------------------------------- helpers
    complete_schema: Optional[str] = "acp:CheckoutSessionCompleteRequest"

    def _base(self, ctx: DriverContext) -> str:
        return "/acp"

    def _complete_path(self, ctx: DriverContext, cid: str) -> str:
        return f"{self._base(ctx)}/checkout_sessions/{cid}/complete"

    def _get_path(self, ctx: DriverContext, cid: str) -> str:
        return f"{self._base(ctx)}/checkout_sessions/{cid}"

    def _headers(self, ctx: DriverContext, step: Dict[str, Any], *, idempotency_key: Optional[str]) -> Dict[str, str]:
        params = step.get("with", {})
        h = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "API-Version": API_VERSION,
            "Request-Id": ctx.rng.uuid(),
            "User-Agent": "commerce-lab/0.1 (acp driver)",
            "Accept-Language": "en-US",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        for name in params.get("omit_headers", []):
            h = {k: v for k, v in h.items() if k.lower() != name.lower()}
        h.update(params.get("headers", {}))
        return h

    def _idem_key(self, ctx: DriverContext, step: Dict[str, Any]) -> Optional[str]:
        spec = step.get("with", {}).get("idempotency_key", "auto")
        if spec is None:
            return None
        if spec == "auto":
            return ctx.rng.uuid()
        if isinstance(spec, str) and spec.startswith("reuse:"):
            prev = ctx.steps.get(spec.split(":", 1)[1])
            if not prev or not prev.request:
                raise HarnessError(f"cannot reuse idempotency key from step {spec}")
            return prev.observations.get("idempotency_key")
        return str(spec)

    def _checkout_id(self, ctx: DriverContext, step: Dict[str, Any]) -> str:
        cid = step.get("with", {}).get("checkout_id") or ctx.state.get("checkout_id")
        if not cid:
            raise HarnessError(f"step {step['id']} needs a checkout session; none was created")
        return cid

    @staticmethod
    def _expand_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """ACP 2026-04-17 Item has no quantity: one entry per unit (profile lab decision)."""
        out = []
        for it in items:
            for _ in range(int(it.get("quantity", 1))):
                out.append({"id": it["id"]})
        return out

    def _after_session_response(self, ctx: DriverContext, outcome: StepOutcome) -> None:
        body = outcome.body
        if isinstance(body, dict) and body.get("id") and outcome.status in (200, 201):
            ctx.state["checkout_id"] = body["id"]
            ctx.state["operation"] = {"checkout_id": body["id"], "protocol": self.protocol}
            ctx.state["last_session"] = body

    # ---------------------------------------------------------------- actions
    async def action_fetch_discovery(self, step, ctx: DriverContext) -> StepOutcome:
        return await self.send(ctx, step, method="GET", path="/.well-known/acp.json", headers={})

    async def action_create_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        body: Dict[str, Any] = p.get("body_override") or {
            "line_items": self._expand_items(p.get("items", [{"id": "sku_tee_blue_m", "quantity": 1}])),
            "currency": p.get("currency", "usd"),
            "capabilities": p.get("capabilities", {}),
        }
        if not p.get("body_override"):
            buyer = p.get("buyer", DEFAULT_BUYER)
            if buyer:
                body["buyer"] = buyer
            if p.get("discount_codes") is not None:
                body["discounts"] = {"codes": p["discount_codes"]}
        key = self._idem_key(ctx, step)
        outcome = await self.send(ctx, step, method="POST", path=f"{self._base(ctx)}/checkout_sessions",
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=body,
                                  request_schema="acp:CheckoutSessionCreateRequest")
        outcome.observations["idempotency_key"] = key
        self._after_session_response(ctx, outcome)
        return outcome

    async def action_update_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        cid = self._checkout_id(ctx, step)
        body: Dict[str, Any] = p.get("body_override") or {}
        if not p.get("body_override"):
            if "items" in p:
                body["line_items"] = self._expand_items(p["items"])
            if "buyer" in p:
                body["buyer"] = p["buyer"]
            if p.get("discount_codes") is not None:
                body["discounts"] = {"codes": p["discount_codes"]}
        key = self._idem_key(ctx, step)
        outcome = await self.send(ctx, step, method="POST", path=f"{self._base(ctx)}/checkout_sessions/{cid}",
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=body,
                                  request_schema="acp:CheckoutSessionUpdateRequest")
        outcome.observations["idempotency_key"] = key
        self._after_session_response(ctx, outcome)
        return outcome

    def _complete_body(self, p: Dict[str, Any]) -> Dict[str, Any]:
        if p.get("body_override"):
            return p["body_override"]
        body: Dict[str, Any] = {
            "payment_data": {
                "handler_id": p.get("handler_id", HANDLER_ID),
                "instrument": {"type": "card", "credential": {"type": "spt", "token": p.get("payment_token", "tok_ok")}},
            }
        }
        if p.get("buyer"):
            body["buyer"] = p["buyer"]
        return body

    async def action_complete_checkout(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        cid = self._checkout_id(ctx, step)
        body = self._complete_body(p)
        key = self._idem_key(ctx, step)
        headers = self._headers(ctx, step, idempotency_key=key)
        # Durable client journal entry written *before* the request (write-ahead), as a careful client would.
        op_id = key or f"op-{step['id']}"
        ctx.durable[op_id] = {"kind": "complete_checkout", "checkout_id": cid, "idempotency_key": key, "body": body,
                              "headers": headers, "status": "pending", "resolved_order_id": None}
        ctx.state["operation"] = {"checkout_id": cid, "protocol": self.protocol, "op_id": op_id}
        outcome = await self.send(ctx, step, method="POST", path=self._complete_path(ctx, cid),
                                  headers=headers, json_body=body, request_schema=self.complete_schema)
        outcome.observations["idempotency_key"] = key
        outcome.observations["op_id"] = op_id
        self._resolve_from_response(ctx, op_id, outcome)
        return outcome

    @staticmethod
    def _completed_order_id(outcome: StepOutcome) -> Optional[str]:
        body = outcome.body
        if outcome.status == 200 and isinstance(body, dict) and body.get("status") == "completed" and isinstance(body.get("order"), dict):
            return body["order"].get("id")
        return None

    def _resolve_from_response(self, ctx: DriverContext, op_id: str, outcome: StepOutcome) -> None:
        body = outcome.body
        op = ctx.durable.get(op_id)
        if op is None:
            return
        if outcome.transport_error:
            op["status"] = "pending"
            op["last_error"] = outcome.transport_error
            return
        resolved = self._completed_order_id(outcome)
        if resolved:
            op["status"] = "resolved"
            op["resolved_order_id"] = resolved
            ctx.state["client_resolved_order_id"] = resolved
        elif outcome.status is not None and outcome.status >= 400:
            op["status"] = "rejected"
            op["last_error"] = body

    async def action_retry_last_operation(self, step, ctx: DriverContext) -> StepOutcome:
        """Re-send the last complete request byte-for-byte (same Idempotency-Key, same body)."""
        op_id = (ctx.state.get("operation") or {}).get("op_id")
        op = ctx.durable.get(op_id) if op_id else None
        if not op:
            raise HarnessError("retry_last_operation: no prior complete_checkout in this case")
        headers = dict(op["headers"])
        headers["Request-Id"] = ctx.rng.uuid()
        outcome = await self.send(ctx, step, method="POST", path=self._complete_path(ctx, op["checkout_id"]),
                                  headers=headers, json_body=op["body"], request_schema=self.complete_schema)
        outcome.observations["idempotency_key"] = op["idempotency_key"]
        self._resolve_from_response(ctx, op_id, outcome)
        return outcome

    async def action_retry_with_different_body(self, step, ctx: DriverContext) -> StepOutcome:
        op_id = (ctx.state.get("operation") or {}).get("op_id")
        op = ctx.durable.get(op_id) if op_id else None
        if not op:
            raise HarnessError("retry_with_different_body: no prior complete_checkout in this case")
        p = dict(step.get("with", {}))
        p.setdefault("payment_token", "tok_ok_alt")
        body = self._complete_body(p)
        headers = dict(op["headers"])
        headers["Request-Id"] = ctx.rng.uuid()
        outcome = await self.send(ctx, step, method="POST", path=self._complete_path(ctx, op["checkout_id"]),
                                  headers=headers, json_body=body, request_schema=self.complete_schema)
        outcome.observations["idempotency_key"] = op["idempotency_key"]
        return outcome

    async def action_restart_client_worker(self, step, ctx: DriverContext) -> StepOutcome:
        """Simulate a client process crash: volatile state is lost, the durable journal survives."""
        pending = [k for k, v in ctx.durable.items() if v["status"] == "pending"]
        keep = {"operation": ctx.state.get("operation"), "checkout_id": ctx.state.get("checkout_id")}
        ctx.state.clear()
        ctx.state.update({k: v for k, v in keep.items() if v is not None})
        ctx.journal.record_fault(fault_id=f"client-restart-{step['id']}", fault_type="process_crash", trigger_point="during_worker_restart",
                                 affected_operation=", ".join(pending) or "none",
                                 confirmation_evidence={"volatile_state_cleared": True, "pending_operations": pending})
        out = StepOutcome(step_id=step["id"], action=step["action"], observations={"pending_operations": pending})
        out.fault_id = f"client-restart-{step['id']}"
        return out

    async def action_recover_existing_operation(self, step, ctx: DriverContext) -> StepOutcome:
        """Recover pending operations after a restart.

        policy ``replay_same_key`` (ACP default): re-send the identical request with the same Idempotency-Key.
        policy ``get_then_replay``: GET the session first; replay only if it does not show completion.
        """
        policy = step.get("with", {}).get("policy", "replay_same_key")
        pending = [(k, v) for k, v in ctx.durable.items() if v["status"] == "pending"]
        if not pending:
            raise HarnessError("recover_existing_operation: nothing pending in the durable journal")
        op_id, op = pending[-1]
        ctx.state["operation"] = {"checkout_id": op["checkout_id"], "protocol": self.protocol, "op_id": op_id}
        if policy == "get_then_replay":
            get_headers = self._headers(ctx, step, idempotency_key=None)
            probe = await self.send(ctx, {**step, "id": step["id"] + "_probe"}, method="GET",
                                    path=self._get_path(ctx, op["checkout_id"]), headers=get_headers)
            ctx.record(probe)
            resolved = self._completed_order_id(probe)
            if resolved:
                op["status"] = "resolved"
                op["resolved_order_id"] = resolved
                ctx.state["client_resolved_order_id"] = resolved
                out = StepOutcome(step_id=step["id"], action=step["action"], response=probe.response,
                                  observations={"policy": policy, "resolved_by": "get"})
                return out
        headers = dict(op["headers"])
        headers["Request-Id"] = ctx.rng.uuid()
        outcome = await self.send(ctx, step, method="POST", path=self._complete_path(ctx, op["checkout_id"]),
                                  headers=headers, json_body=op["body"], request_schema=self.complete_schema)
        outcome.observations.update({"policy": policy, "idempotency_key": op["idempotency_key"], "resolved_by": "replay"})
        self._resolve_from_response(ctx, op_id, outcome)
        return outcome

    async def action_get_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        cid = self._checkout_id(ctx, step)
        outcome = await self.send(ctx, step, method="GET", path=self._get_path(ctx, cid),
                                  headers=self._headers(ctx, step, idempotency_key=None))
        return outcome

    async def action_cancel_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        cid = self._checkout_id(ctx, step)
        key = self._idem_key(ctx, step)
        body = step.get("with", {}).get("body_override", {"intent_trace": {"reason_code": "other", "trace_summary": "lab cancel"}})
        outcome = await self.send(ctx, step, method="POST", path=f"{self._base(ctx)}/checkout_sessions/{cid}/cancel",
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=body,
                                  request_schema="acp:CancelSessionRequest")
        outcome.observations["idempotency_key"] = key
        return outcome

    async def action_send_raw_request(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        path = p["path"].replace("{checkout_id}", ctx.state.get("checkout_id", "missing"))
        headers = self._headers(ctx, step, idempotency_key=self._idem_key(ctx, step)) if p.get("default_headers", True) else {}
        headers.update(p.get("headers", {}))
        raw = p.get("raw_body")
        return await self.send(ctx, step, method=p.get("method", "POST"), path=f"{self._base(ctx)}{path}" if path.startswith("/checkout") else path,
                               headers=headers, json_body=p.get("body") if raw is None else None,
                               raw_body=raw.encode("utf-8") if raw is not None else None)

    async def action_change_inventory(self, step, ctx: DriverContext) -> StepOutcome:
        """Fixture-side fault: stale price or stock change between quote and completion."""
        p = step.get("with", {})
        fault = step.get("fault") or {}
        sku = p.get("sku") or fault.get("sku") or "sku_tee_blue_m"
        body = {}
        if "new_unit_amount" in fault or "new_unit_amount" in p:
            body["unit_amount"] = fault.get("new_unit_amount", p.get("new_unit_amount"))
        if "new_stock" in fault or "new_stock" in p:
            body["stock"] = fault.get("new_stock", p.get("new_stock"))
        result = await ctx.env.admin(f"/_lab/admin/inventory/{sku}", body)
        ftype = fault.get("type") or ("stale_price_change" if "unit_amount" in body else "stock_change")
        fid = f"fixture-{step['id']}"
        ctx.journal.record_fault(fault_id=fid, fault_type=ftype, trigger_point=fault.get("trigger_point", "before_step"),
                                 affected_operation=f"inventory {sku}", confirmation_evidence={"admin_response": result})
        out = StepOutcome(step_id=step["id"], action=step["action"], observations={"inventory": result}, fault_id=fid)
        return out

    async def action_advance_clock(self, step, ctx: DriverContext) -> StepOutcome:
        seconds = int(step.get("with", {}).get("seconds", 0))
        ctx.clock.advance(seconds)
        return StepOutcome(step_id=step["id"], action=step["action"], observations={"advanced_seconds": seconds, "now": ctx.clock.now_int()})

    # ---------------------------------------------------------------- metrics
    async def metric_last_status(self, ctx: DriverContext) -> Optional[int]:
        last = ctx.last_step()
        if last is None or last.response is None:
            raise MetricUnavailable("no HTTP response in the last step")
        return last.status
