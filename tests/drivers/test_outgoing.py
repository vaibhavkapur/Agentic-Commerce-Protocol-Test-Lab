"""Drivers validate outgoing messages so a harness bug is not a target failure (plan §10)."""

from __future__ import annotations

import pytest

from drivers.acp.driver import AcpDriver
from drivers.base import OutgoingMessageInvalid
from lab.runner.manifests import load_profile


def test_malformed_outgoing_checkout_is_harness_error() -> None:
    driver = AcpDriver(load_profile("acp-checkout-2026-04-17"))
    with pytest.raises(OutgoingMessageInvalid, match="intentionally_invalid"):
        driver._validate_outgoing(
            "acp:CheckoutSessionCreateRequest",
            {"line_items": "not-an-array"},
            {"id": "create", "action": "create_checkout_session"},
        )


def test_intentionally_invalid_outgoing_skips_schema() -> None:
    driver = AcpDriver(load_profile("acp-checkout-2026-04-17"))
    assert (
        driver._validate_outgoing(
            "acp:CheckoutSessionCreateRequest",
            {"line_items": "not-an-array"},
            {"id": "create", "action": "create_checkout_session", "intentionally_invalid": True},
        )
        is None
    )
