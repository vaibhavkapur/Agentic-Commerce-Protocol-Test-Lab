"""Run environment: target, fault proxy, protocol client, and observation client.

Two modes:

* ``in_process`` — the local reference target and the fault proxy are ASGI apps
  driven through ``httpx.ASGITransport``. Deterministic and used by the test suite.
* ``network`` — the target, proxy, and observation endpoints are URLs (Docker Compose
  or manually started processes). Faults are armed through the proxy control API.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from fault_proxy import ArmedFault, FaultProxy
from .clock import LabClock, LabRng
from .events import EventJournal
from .manifests import Target

PROTOCOL_BY_PREFIX = [("/acp", "acp"), ("/.well-known/acp.json", "acp"), ("/ucp", "ucp"), ("/.well-known/ucp", "ucp"),
                      ("/ap2", "ap2"), ("/tap", "tap"), ("/registry", "tap")]


def resolve_protocol(path: str) -> str:
    for prefix, proto in PROTOCOL_BY_PREFIX:
        if path.startswith(prefix):
            return proto
    return "http"


class HarnessError(Exception):
    """The runner or a fixture failed; distinct from a target failure."""


class FaultController:
    """Uniform arm/disarm interface over an in-process or remote proxy."""

    def __init__(self, proxy: Optional[FaultProxy] = None, control_url: Optional[str] = None, journal: Optional[EventJournal] = None):
        self.proxy = proxy
        self.control_url = control_url
        self.journal = journal

    async def arm(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        if self.proxy is not None:
            return self.proxy.arm(**spec).to_dict()
        async with httpx.AsyncClient(base_url=self.control_url, timeout=10.0) as c:
            r = await c.post("/_lab/faults/arm", json=spec)
            if r.status_code != 200:
                raise HarnessError(f"proxy refused fault: {r.text}")
            return r.json()

    async def disarm_all(self) -> List[Dict[str, Any]]:
        if self.proxy is not None:
            return [f.to_dict() for f in self.proxy.disarm_all()]
        async with httpx.AsyncClient(base_url=self.control_url, timeout=10.0) as c:
            r = await c.post("/_lab/faults/disarm")
            return r.json().get("unfired", [])

    async def sync_remote_events(self) -> None:
        """Network mode: pull proxy-side events into the local journal."""
        if self.proxy is not None or self.journal is None:
            return
        from .events import FaultEvent, ProtocolEvent

        async with httpx.AsyncClient(base_url=self.control_url, timeout=10.0) as c:
            r = await c.get("/_lab/faults/events")
        data = r.json()
        known = {e.event_id for e in self.journal.protocol_events}
        for e in data.get("protocol_events", []):
            if e["event_id"] not in known:
                self.journal.protocol_events.append(ProtocolEvent(**e))
        known_f = {(f.fault_id, f.sequence) for f in self.journal.fault_events}
        for f in data.get("fault_events", []):
            if (f["fault_id"], f["sequence"]) not in known_f:
                self.journal.fault_events.append(FaultEvent(**f))
        for blob in data.get("evidence", []):
            self.journal.evidence._blobs.setdefault(blob["reference"], blob)


@dataclass
class Environment:
    target: Target
    clock: LabClock
    rng: LabRng
    journal: EventJournal
    mode: str = "in_process"
    bundle: Any = None
    proxy: Optional[FaultProxy] = None
    protocol_client: Optional[httpx.AsyncClient] = None
    observe_client: Optional[httpx.AsyncClient] = None
    faults: Optional[FaultController] = None
    description: Dict[str, Any] = field(default_factory=dict)

    @property
    def can_observe(self) -> bool:
        return self.observe_client is not None

    async def start(self) -> None:
        if self.target.kind == "local_reference":
            module_name, _, func_name = self.target.factory.rpartition(":")
            if not module_name:
                module_name, _, func_name = self.target.factory.rpartition(".")
            try:
                factory = getattr(importlib.import_module(module_name), func_name)
            except (ImportError, AttributeError) as exc:
                raise HarnessError(f"cannot import target factory {self.target.factory!r}: {exc}")
            self.bundle = factory(self.clock, self.rng, self.target.options)
            self.mode = "in_process"
            self.proxy = FaultProxy(self.journal, upstream_app=self.bundle.app, protocol_resolver=resolve_protocol)
            self.protocol_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.proxy), base_url="http://target.lab", timeout=10.0)
            if self.target.observation.get("kind") == "lab_fixture":
                self.observe_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.bundle.app), base_url="http://target.lab", timeout=10.0)
            self.faults = FaultController(proxy=self.proxy)
            self.description = {"mode": "in_process", "target_factory": self.target.factory, "options": self.bundle.options}
        else:
            self.mode = "network"
            proxy_base = (self.target.proxy or {}).get("base_url")
            control = (self.target.proxy or {}).get("control_url") or proxy_base
            if not proxy_base:
                raise HarnessError("network targets need proxy.base_url (run `commerce-lab proxy` in front of the target)")
            self.protocol_client = httpx.AsyncClient(base_url=proxy_base, timeout=15.0)
            if self.target.observation.get("kind") == "lab_fixture":
                self.observe_client = httpx.AsyncClient(base_url=self.target.observation["base_url"], timeout=15.0)
            self.faults = FaultController(control_url=control, journal=self.journal)
            self.description = {"mode": "network", "proxy": proxy_base, "target": self.target.base_url}
        await self._healthcheck()

    async def _healthcheck(self) -> None:
        if self.observe_client is None:
            return
        try:
            r = await self.observe_client.get("/_lab/health")
        except httpx.HTTPError as exc:
            raise HarnessError(f"fixture observation endpoint unreachable: {exc}")
        if r.status_code != 200:
            raise HarnessError(f"fixture health check returned {r.status_code}")

    async def stop(self) -> None:
        for c in (self.protocol_client, self.observe_client):
            if c is not None:
                await c.aclose()

    async def reset_fixture(self) -> None:
        if self.observe_client is None:
            return
        try:
            r = await self.observe_client.post("/_lab/admin/reset")
        except httpx.HTTPError as exc:
            raise HarnessError(f"fixture reset failed: {exc}")
        if r.status_code != 200:
            raise HarnessError(f"fixture reset returned {r.status_code}")

    async def observe(self, path: str, **params) -> Any:
        if self.observe_client is None:
            return None
        try:
            r = await self.observe_client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise HarnessError(f"observation call {path} failed: {exc}")
        if r.status_code >= 500:
            raise HarnessError(f"observation call {path} returned {r.status_code}")
        return r.json() if r.content else None

    async def admin(self, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        if self.observe_client is None:
            raise HarnessError("admin endpoint requires a lab fixture observation channel")
        try:
            r = await self.observe_client.post(path, json=body or {})
        except httpx.HTTPError as exc:
            raise HarnessError(f"admin call {path} failed: {exc}")
        if r.status_code >= 400:
            raise HarnessError(f"admin call {path} returned {r.status_code}: {r.text}")
        return r.json() if r.content else None
