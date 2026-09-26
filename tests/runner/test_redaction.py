"""Secrets are redacted from exported traces (plan §17, §23)."""

from __future__ import annotations

from lab.runner.redaction import REDACTED, contains_secret, redact_headers, redact_json
from tests.conftest import run_case


def test_authorization_header_is_redacted() -> None:
    out = redact_headers({"Authorization": "Bearer super-secret-token", "Content-Type": "application/json"})
    assert out["Authorization"] == REDACTED
    assert out["Content-Type"] == "application/json"


def test_sensitive_json_keys_and_private_jwk_members() -> None:
    body = {
        "token": "tok_live_abc",
        "id": "chk_1",
        "nested": {"api_key": "k-secret", "ok": True},
        "key": {"kty": "OKP", "crv": "Ed25519", "x": "public", "d": "private-scalar"},
    }
    redacted = redact_json(body)
    assert redacted["token"] == REDACTED
    assert redacted["id"] == "chk_1"
    assert redacted["nested"]["api_key"] == REDACTED
    assert redacted["nested"]["ok"] is True
    assert redacted["key"]["x"] == "public"
    assert redacted["key"]["d"] == REDACTED


def test_exported_run_bundle_does_not_contain_synthetic_secrets() -> None:
    bundle = run_case("local-merchant-corrected", "acp-create-session-201", run_id="redact-export")
    dumped = bundle.to_dict()
    secrets = [
        "lab_acp_api_key_synthetic",
        "BEGIN PRIVATE KEY",
        "BEGIN EC PRIVATE KEY",
    ]
    assert not contains_secret(dumped, secrets)
    for blob in dumped["evidence"]:
        headers = blob.get("headers") or {}
        for name, value in headers.items():
            if name.lower() == "authorization":
                assert value == REDACTED
