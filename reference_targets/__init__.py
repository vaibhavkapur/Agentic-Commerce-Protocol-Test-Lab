"""Reference implementations under test: deliberately broken and corrected variants.

Each factory returns a :class:`fixtures.target_app.TargetBundle`. The variants
differ only in the option presets below, so a reader can see exactly which
behaviours separate a failing report from a passing one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from lab.runner.clock import LabClock, LabRng
from fixtures.target_app import TargetBundle, build_target

BROKEN_OPTIONS: Dict[str, Any] = {
    # Ignores Idempotency-Key entirely: no replay, no conflict detection, no 400 when missing.
    "idempotency": "ignored",
    # Charges the processor before checking whether the session was already completed.
    "complete_order": "charge_then_check",
    # Does not pass a processor-side idempotency key, so a re-submitted charge settles twice.
    "psp_idempotency": False,
    # Returns {"error": ...} instead of the ACP/UCP error object shapes.
    "error_body": "legacy",
    # Trusts caller-supplied X-Forwarded-Host when deriving @authority.
    "tap_trust_forwarded_host": True,
    # No nonce replay cache.
    "tap_replay_protection": False,
    # Authenticates the mandate chain but never checks it is bound to *this* checkout.
    "ap2_check_checkout_binding": False,
}

CORRECTED_OPTIONS: Dict[str, Any] = {}

MINIMAL_OPTIONS: Dict[str, Any] = {
    # Corrected behaviour, but the optional discount capabilities are not offered.
    "acp_discount_extension": False,
    "ucp_discount_capability": False,
}

MISDECLARED_OPTIONS: Dict[str, Any] = {
    # Claims the UCP checkout profile in its target manifest but does not advertise the capability.
    "ucp_advertise_checkout": False,
}


def broken(clock: LabClock, rng: LabRng, options: Optional[Dict[str, Any]] = None) -> TargetBundle:
    opts = dict(BROKEN_OPTIONS)
    opts.update(options or {})
    return build_target(clock, rng, opts)


def corrected(clock: LabClock, rng: LabRng, options: Optional[Dict[str, Any]] = None) -> TargetBundle:
    opts = dict(CORRECTED_OPTIONS)
    opts.update(options or {})
    return build_target(clock, rng, opts)


def corrected_minimal(clock: LabClock, rng: LabRng, options: Optional[Dict[str, Any]] = None) -> TargetBundle:
    opts = dict(MINIMAL_OPTIONS)
    opts.update(options or {})
    return build_target(clock, rng, opts)


def corrected_misdeclared(clock: LabClock, rng: LabRng, options: Optional[Dict[str, Any]] = None) -> TargetBundle:
    opts = dict(MISDECLARED_OPTIONS)
    opts.update(options or {})
    return build_target(clock, rng, opts)
