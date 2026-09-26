"""Network fault proxy (plan §14).

An ASGI application that sits between the lab's protocol drivers and the
implementation under test. Every request/response pair is journaled as
protocol events; armed faults fire at named, deterministic trigger points and
are journaled as fault events with confirmation evidence (for example the
upstream status code that was observed *before* a response was dropped).

Fault types → trigger points:

* ``drop_request``            → ``before_request_delivery`` (upstream never sees the request)
* ``bounded_delay``           → ``before_request_delivery``
* ``duplicate_delivery``      → ``before_request_delivery`` (request forwarded N times)
* ``drop_response``           → ``after_backend_commit_before_response``
* ``connection_interruption`` → ``during_response_streaming``

A dropped response is surfaced to the client as a transport-level failure in
both in-process (``httpx.ASGITransport``) and network (uvicorn) modes: the proxy
starts the response with a ``content-length`` and then aborts, so the client sees
an incomplete message rather than an HTTP status.

Boundary canonicalization: the proxy stamps ``X-Lab-Boundary-Authority`` with the
``Host`` it actually observed. Caller-supplied ``X-Forwarded-Host`` is forwarded
untouched so that a verifier which trusts it can be shown to be wrong.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import httpx

from lab.runner.events import EventJournal, digest_bytes

BOUNDARY_HEADER = "x-lab-boundary-authority"
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection"}

TRIGGER_FOR_TYPE = {
    "drop_request": "before_request_delivery",
    "bounded_delay": "before_request_delivery",
    "duplicate_delivery": "before_request_delivery",
    "drop_response": "after_backend_commit_before_response",
    "connection_interruption": "during_response_streaming",
}


class FaultInjected(Exception):
    """Raised inside the ASGI app to abort delivery; clients observe a transport failure."""

    def __init__(self, fault_id: str, fault_type: str):
        super().__init__(f"lab fault {fault_id} ({fault_type}) fired")
        self.fault_id = fault_id
        self.fault_type = fault_type


@dataclass
class ArmedFault:
    id: str
    type: str
    trigger_point: str
    method: Optional[str] = None
    path_regex: Optional[str] = None
    header_equals: Dict[str, str] = field(default_factory=dict)
    delay_ms: int = 0
    copies: int = 2
    max_fires: int = 1
    fired: int = 0
    case_id: str = ""
    step_id: Optional[str] = None

    def matches(self, method: str, path: str, headers: Dict[str, str]) -> bool:
        if self.fired >= self.max_fires:
            return False
        if self.method and self.method.upper() != method.upper():
            return False
        if self.path_regex and not re.search(self.path_regex, path):
            return False
        for k, v in self.header_equals.items():
            if headers.get(k.lower()) != v:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "trigger_point": self.trigger_point, "method": self.method,
            "path_regex": self.path_regex, "header_equals": self.header_equals, "delay_ms": self.delay_ms,
            "copies": self.copies, "max_fires": self.max_fires, "fired": self.fired, "case_id": self.case_id, "step_id": self.step_id,
        }


class FaultProxy:
    def __init__(self, journal: EventJournal, *, upstream_app=None, upstream_url: Optional[str] = None,
                 protocol_resolver=None, profile_version: str = ""):
        if (upstream_app is None) == (upstream_url is None):
            raise ValueError("provide exactly one of upstream_app or upstream_url")
        self.journal = journal
        self.upstream_app = upstream_app
        self.upstream_url = upstream_url
        self.protocol_resolver = protocol_resolver or (lambda path: "http")
        self.profile_version = profile_version
        self.armed: List[ArmedFault] = []
        self._counter = 0
        self.upstream_contacts: List[Dict[str, Any]] = []

    # --------------------------------------------------------------- control
    def arm(self, fault_type: str, *, trigger_point: Optional[str] = None, method: Optional[str] = None,
            path_regex: Optional[str] = None, header_equals: Optional[Dict[str, str]] = None, delay_ms: int = 0,
            copies: int = 2, max_fires: int = 1, case_id: str = "", step_id: Optional[str] = None) -> ArmedFault:
        if fault_type not in TRIGGER_FOR_TYPE:
            raise ValueError(f"fault type {fault_type!r} is not a proxy fault")
        expected_trigger = TRIGGER_FOR_TYPE[fault_type]
        if trigger_point and trigger_point != expected_trigger:
            raise ValueError(f"fault {fault_type} fires at {expected_trigger}, not {trigger_point}")
        self._counter += 1
        fault = ArmedFault(id=f"fault-{self._counter:03d}", type=fault_type, trigger_point=expected_trigger, method=method,
                           path_regex=path_regex, header_equals={k.lower(): v for k, v in (header_equals or {}).items()},
                           delay_ms=delay_ms, copies=copies, max_fires=max_fires, case_id=case_id, step_id=step_id)
        self.armed.append(fault)
        return fault

    def disarm_all(self) -> List[ArmedFault]:
        unfired = [f for f in self.armed if f.fired < f.max_fires]
        self.armed.clear()
        return unfired

    def unfired(self) -> List[ArmedFault]:
        return [f for f in self.armed if f.fired < f.max_fires]

    # ------------------------------------------------------------- upstream
    def _client(self) -> httpx.AsyncClient:
        if self.upstream_app is not None:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.upstream_app), base_url="http://upstream.lab", timeout=10.0)
        return httpx.AsyncClient(base_url=self.upstream_url, timeout=10.0)

    async def _forward(self, method: str, path: str, query: str, headers: Dict[str, str], body: bytes) -> httpx.Response:
        fwd = {k: v for k, v in headers.items() if k.lower() not in HOP_HEADERS and k.lower() != "content-length"}
        fwd[BOUNDARY_HEADER] = headers.get("host", "")
        fwd["x-lab-proxy"] = "1"
        if self.upstream_url:
            fwd.pop("host", None)
        url = path + (f"?{query}" if query else "")
        async with self._client() as client:
            resp = await client.request(method, url, headers=fwd, content=body)
            await resp.aread()
            self.upstream_contacts.append({"method": method, "path": path, "status": resp.status_code, "at": self.journal.clock.iso()})
            return resp

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path.startswith("/_lab/faults"):
            return await self._control(scope, receive, send)
        method = scope["method"]
        query = scope.get("query_string", b"").decode("latin-1")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        operation = f"{method} {unquote(path)}"
        affected = operation + (f" idem={headers['idempotency-key']}" if headers.get("idempotency-key") else "")
        fault = next((f for f in self.armed if f.matches(method, path, headers)), None)
        protocol = self.protocol_resolver(path)
        self.journal.record_message(direction="agent->target", protocol=protocol, profile_version=self.profile_version,
                                    operation=operation, headers=headers, body=_maybe_json(body), raw_body=body,
                                    meta={"armed_fault": fault.id if fault else None, "boundary_authority": headers.get("host", "")})

        if fault and fault.type == "drop_request":
            fault.fired += 1
            self.journal.record_fault(fault_id=fault.id, fault_type=fault.type, trigger_point=fault.trigger_point,
                                      affected_operation=affected, confirmation_evidence={"upstream_contacted": False},
                                      case_id=fault.case_id or None)
            await self._abort(send, fault)
            return
        if fault and fault.type == "bounded_delay":
            fault.fired += 1
            await asyncio.sleep(fault.delay_ms / 1000.0)
            self.journal.record_fault(fault_id=fault.id, fault_type=fault.type, trigger_point=fault.trigger_point,
                                      affected_operation=affected, confirmation_evidence={"delay_ms": fault.delay_ms},
                                      case_id=fault.case_id or None)
        copies = fault.copies if (fault and fault.type == "duplicate_delivery") else 1
        responses: List[httpx.Response] = []
        for i in range(copies):
            try:
                resp = await self._forward(method, path, query, headers, body)
            except httpx.HTTPError as exc:
                self.journal.record_message(direction="target->agent", protocol=protocol, profile_version=self.profile_version,
                                            operation=operation, headers={}, body={"transport_error": str(exc)},
                                            meta={"upstream_unreachable": True})
                await self._send_json(send, 502, {"lab_proxy_error": "upstream unreachable", "detail": str(exc)})
                return
            responses.append(resp)
            self.journal.record_message(direction="target->agent", protocol=protocol, profile_version=self.profile_version,
                                        operation=operation, headers=dict(resp.headers), body=_maybe_json(resp.content), raw_body=resp.content,
                                        meta={"status": resp.status_code, "copy": i + 1, "copies": copies,
                                              "armed_fault": fault.id if fault else None})
        if fault and fault.type == "duplicate_delivery":
            fault.fired += 1
            self.journal.record_fault(fault_id=fault.id, fault_type=fault.type, trigger_point=fault.trigger_point,
                                      affected_operation=affected,
                                      confirmation_evidence={"copies_delivered": copies, "upstream_statuses": [r.status_code for r in responses]},
                                      case_id=fault.case_id or None)
        resp = responses[-1]
        if fault and fault.type == "drop_response":
            fault.fired += 1
            self.journal.record_fault(fault_id=fault.id, fault_type=fault.type, trigger_point=fault.trigger_point,
                                      affected_operation=affected,
                                      confirmation_evidence={"upstream_status": resp.status_code, "upstream_body_digest": digest_bytes(resp.content),
                                                             "upstream_committed": resp.status_code < 400},
                                      case_id=fault.case_id or None)
            await self._abort(send, fault, status=resp.status_code, expected_length=max(len(resp.content), 1))
            return
        if fault and fault.type == "connection_interruption":
            fault.fired += 1
            half = resp.content[: max(1, len(resp.content) // 2)]
            self.journal.record_fault(fault_id=fault.id, fault_type=fault.type, trigger_point=fault.trigger_point,
                                      affected_operation=affected,
                                      confirmation_evidence={"upstream_status": resp.status_code, "bytes_delivered": len(half),
                                                             "bytes_total": len(resp.content)},
                                      case_id=fault.case_id or None)
            await send({"type": "http.response.start", "status": resp.status_code,
                        "headers": _response_headers(resp, content_length=len(resp.content))})
            await send({"type": "http.response.body", "body": half, "more_body": True})
            raise FaultInjected(fault.id, fault.type)
        await send({"type": "http.response.start", "status": resp.status_code, "headers": _response_headers(resp, content_length=len(resp.content))})
        await send({"type": "http.response.body", "body": resp.content, "more_body": False})

    async def _abort(self, send, fault: ArmedFault, status: int = 200, expected_length: int = 1) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-length", str(expected_length).encode()), (b"x-lab-fault", fault.id.encode())]})
        raise FaultInjected(fault.id, fault.type)

    async def _send_json(self, send, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    # -------------------------------------------------- control API (network)
    async def _control(self, scope, receive, send) -> None:
        path, method = scope["path"], scope["method"]
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if path == "/_lab/faults/arm" and method == "POST":
            spec = json.loads(body or b"{}")
            try:
                fault = self.arm(spec["type"], trigger_point=spec.get("trigger_point"), method=spec.get("method"),
                                 path_regex=spec.get("path_regex"), header_equals=spec.get("header_equals"),
                                 delay_ms=spec.get("delay_ms", 0), copies=spec.get("copies", 2), max_fires=spec.get("max_fires", 1),
                                 case_id=spec.get("case_id", ""), step_id=spec.get("step_id"))
            except (KeyError, ValueError) as exc:
                return await self._send_json(send, 400, {"error": str(exc)})
            return await self._send_json(send, 200, fault.to_dict())
        if path == "/_lab/faults/disarm" and method == "POST":
            unfired = self.disarm_all()
            return await self._send_json(send, 200, {"unfired": [f.to_dict() for f in unfired]})
        if path == "/_lab/faults" and method == "GET":
            return await self._send_json(send, 200, {"armed": [f.to_dict() for f in self.armed]})
        if path == "/_lab/faults/events" and method == "GET":
            return await self._send_json(send, 200, {"fault_events": [f.to_dict() for f in self.journal.fault_events],
                                                     "protocol_events": [e.to_dict() for e in self.journal.protocol_events],
                                                     "evidence": self.journal.evidence.all()})
        return await self._send_json(send, 404, {"error": "unknown control endpoint"})


def _maybe_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw[:512].decode("utf-8", "replace")}


def _response_headers(resp: httpx.Response, content_length: int) -> List[tuple]:
    out = []
    for k, v in resp.headers.multi_items():
        lk = k.lower()
        if lk in HOP_HEADERS or lk in ("content-length", "content-encoding"):
            continue
        out.append((lk.encode("latin-1"), v.encode("latin-1")))
    out.append((b"content-length", str(content_length).encode()))
    return out
