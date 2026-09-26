"""Test agent registry and trusted-issuer registry (plan §4, §13).

Modelled on the Visa TAP sample's agent registry (``/keys/{key_id}`` returning
``key_id``, ``algorithm``, public key, and agent metadata). Key status, validity
windows, rotation, and revocation are *lab trust policy*, not protocol rules,
and assertions built on them are labelled ``application_policy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fixtures.issuers.keys import KeyHandle, public_only


class TrustError(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass
class RegisteredKey:
    key_id: str
    agent_id: str
    algorithm: str  # RFC 9421 algorithm name: ed25519 | ecdsa-p256-sha256
    public_jwk: Dict[str, Any]
    status: str = "active"  # active | revoked | rotated_out
    valid_from: int = 0
    valid_until: Optional[int] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegisteredAgent:
    agent_id: str
    name: str
    directory_url: str
    keys: List[str] = field(default_factory=list)


class AgentRegistry:
    def __init__(self, clock, *, rotation_grace_seconds: int = 300):
        self.clock = clock
        self.rotation_grace_seconds = rotation_grace_seconds
        self.agents: Dict[str, RegisteredAgent] = {}
        self.keys: Dict[str, RegisteredKey] = {}
        self.audit: List[Dict[str, Any]] = []

    def register_agent(self, agent_id: str, name: str, directory_url: str) -> RegisteredAgent:
        agent = RegisteredAgent(agent_id=agent_id, name=name, directory_url=directory_url)
        self.agents[agent_id] = agent
        return agent

    def register_key(self, agent_id: str, key: KeyHandle, *, description: str = "") -> RegisteredKey:
        rk = RegisteredKey(key_id=key.kid, agent_id=agent_id, algorithm=key.rfc9421_alg, public_jwk=public_only(key.public_jwk),
                           valid_from=self.clock.now_int(), description=description)
        self.keys[key.kid] = rk
        self.agents[agent_id].keys.append(key.kid)
        self.audit.append({"at": self.clock.iso(), "action": "register_key", "key_id": key.kid, "agent_id": agent_id})
        return rk

    def revoke_key(self, key_id: str) -> None:
        self.keys[key_id].status = "revoked"
        self.keys[key_id].valid_until = self.clock.now_int()
        self.audit.append({"at": self.clock.iso(), "action": "revoke_key", "key_id": key_id})

    def rotate_key(self, agent_id: str, new_key: KeyHandle) -> RegisteredKey:
        """Rotate: the old key stays valid for ``rotation_grace_seconds`` (lab policy), then rotates out."""
        for kid in self.agents[agent_id].keys:
            rk = self.keys[kid]
            if rk.status == "active":
                rk.status = "rotated_out"
                rk.valid_until = self.clock.now_int() + self.rotation_grace_seconds
        rk_new = self.register_key(agent_id, new_key, description="rotated-in key")
        self.audit.append({"at": self.clock.iso(), "action": "rotate_key", "agent_id": agent_id, "new_key_id": new_key.kid})
        return rk_new

    def resolve(self, key_id: str, *, at: Optional[int] = None) -> RegisteredKey:
        now = at if at is not None else self.clock.now_int()
        rk = self.keys.get(key_id)
        if rk is None:
            raise TrustError("unknown_key", f"key {key_id!r} is not registered")
        if rk.status == "revoked":
            raise TrustError("key_revoked", f"key {key_id!r} was revoked at {rk.valid_until}")
        if rk.status == "rotated_out" and rk.valid_until is not None and now > rk.valid_until:
            raise TrustError("key_rotated_out", f"key {key_id!r} rotated out at {rk.valid_until} (grace expired)")
        if now < rk.valid_from:
            raise TrustError("key_not_yet_valid", f"key {key_id!r} is valid from {rk.valid_from}")
        return rk

    def snapshot(self) -> Dict[str, Any]:
        return {
            "agents": {k: asdict(v) for k, v in self.agents.items()},
            "keys": {k: v.to_dict() for k, v in self.keys.items()},
            "audit": list(self.audit),
            "policy": {"rotation_grace_seconds": self.rotation_grace_seconds},
        }

    # ------------------------------------------------------------- HTTP view
    async def http_get_key(self, request: Request):
        kid = request.path_params["key_id"]
        rk = self.keys.get(kid)
        if rk is None:
            return JSONResponse({"detail": "key not found"}, status_code=404)
        body = rk.to_dict()
        body["agent"] = asdict(self.agents[rk.agent_id])
        return JSONResponse(body)

    async def http_list_agents(self, request: Request):
        return JSONResponse({"agents": [asdict(a) for a in self.agents.values()]})

    async def http_jwks(self, request: Request):
        return JSONResponse({"keys": [rk.public_jwk for rk in self.keys.values() if rk.status != "revoked"]})

    def routes(self, prefix: str = "/registry") -> List[Route]:
        return [
            Route(f"{prefix}/keys/{{key_id}}", self.http_get_key, methods=["GET"]),
            Route(f"{prefix}/agents", self.http_list_agents, methods=["GET"]),
            Route(f"{prefix}/.well-known/jwks.json", self.http_jwks, methods=["GET"]),
        ]


class IssuerRegistry:
    """Trusted issuers of user credentials (AP2 Trusted Surface / Credential Provider issuers)."""

    def __init__(self):
        self.issuers: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def trust(self, iss: str, key: KeyHandle) -> None:
        self.issuers.setdefault(iss, {})[key.kid] = public_only(key.public_jwk)

    def resolve(self, iss: str, kid: Optional[str]) -> Dict[str, Any]:
        keys = self.issuers.get(iss)
        if not keys:
            raise TrustError("unknown_issuer", f"issuer {iss!r} is not trusted")
        if kid is None:
            if len(keys) == 1:
                return next(iter(keys.values()))
            raise TrustError("unknown_key", "kid required for issuer with multiple keys")
        jwk = keys.get(kid)
        if jwk is None:
            raise TrustError("unknown_key", f"issuer {iss!r} has no key {kid!r}")
        return jwk

    def snapshot(self) -> Dict[str, Any]:
        return {"issuers": self.issuers}
