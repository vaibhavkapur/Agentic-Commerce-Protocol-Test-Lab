"""Synthetic issuer, user, agent, merchant, and verifier keys with distinct roles (plan §12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from lab.runner.clock import LabRng

from .keys import KeyHandle, generate_key

ISSUER_ID = "https://issuer.lab.test"
ROGUE_ISSUER_ID = "https://rogue-issuer.lab.test"
MERCHANT_AUDIENCE = "merchant_lab_1"
CREDENTIAL_PROVIDER_AUDIENCE = "credential-provider.lab.test"
AGENT_ID = "agent_lab_shopper_1"
AGENT_DIRECTORY_URL = "https://agents.lab.test/directory"


@dataclass
class TrustMaterial:
    issuer: KeyHandle
    rogue_issuer: KeyHandle
    user_device: KeyHandle
    agent: KeyHandle
    agent_secondary: KeyHandle
    agent_rotated: KeyHandle
    rogue_agent: KeyHandle
    merchant: KeyHandle
    credential_provider: KeyHandle

    @classmethod
    def generate(cls, rng: LabRng) -> "TrustMaterial":
        r = rng.child("trust-material")
        return cls(
            issuer=generate_key(r, "iss-key-1", "issuer", "ES256"),
            rogue_issuer=generate_key(r, "rogue-iss-key-1", "issuer", "ES256"),
            user_device=generate_key(r, "user-device-key-1", "user", "ES256"),
            agent=generate_key(r, "agent-key-ed25519-1", "agent", "EdDSA"),
            agent_secondary=generate_key(r, "agent-key-p256-1", "agent", "ES256"),
            agent_rotated=generate_key(r, "agent-key-ed25519-2", "agent", "EdDSA"),
            rogue_agent=generate_key(r, "rogue-agent-key-1", "agent", "EdDSA"),
            merchant=generate_key(r, "merchant-key-1", "merchant", "ES256"),
            credential_provider=generate_key(r, "cp-key-1", "verifier", "ES256"),
        )

    def public_snapshot(self) -> Dict[str, Any]:
        return {name: getattr(self, name).public_jwk for name in self.__dataclass_fields__}
