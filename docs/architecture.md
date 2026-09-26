# Architecture

[Documentation home](index.md)

The lab is a versioned test harness, not a general distributed test platform. Drivers format protocol messages. Assertions decide whether the observed behaviour met a requirement. Faults, fixtures, and reports stay independent so a harness bug is not recorded as a target failure.

```text
CLI / CI → Suite loader → Scenario runner → Protocol driver
                │               │                ↓
                │               │           Fault proxy
                │               │                ↓
                │               │      Implementation under test
                │               │                ↓
                │               └──────── Fixture merchant / verifier / payment
                ↓
       Version and requirement registry
                ↓
          Assertion engine ← Observable state + protocol events
                ↓
          JSON / JUnit / browser report
```

## Components

**CLI (`lab/cli`)** — `commerce-lab` lists profiles, inspects targets, runs suites, exports reports, and compares two runs. A later HTTP API (`POST /v1/runs`, …) is not implemented; cancellation of an in-flight run is therefore a process interrupt, which stops new cases and still writes the partial bundle.

**Suite loader (`lab/runner/manifests`)** — YAML profiles, requirements, targets, suites, and scenarios are validated against strict JSON Schemas in `schemas/`. Unknown capabilities, unknown requirement ids, duplicate step ids, and schema violations raise `ManifestError` before any case executes.

**Runner (`lab/runner/engine`)** — For each case: capability discovery, applicability, fixture prepare, ordered steps with optional faults, assertions, evidence collection, cleanup. Unexpected exceptions become `HARNESS_ERROR`, never a target `FAIL`.

**Protocol drivers (`drivers/`)** — One driver per protocol with a small common interface: `inspect_target`, `check_applicability`, `prepare_fixture`, `execute_step`, `collect_evidence`. Native status values are preserved. Outgoing messages are validated against the pinned request schema unless a step is marked `intentionally_invalid`.

**Fault proxy (`fault_proxy/`)** — Sits between the driver and the target. Named trigger points (`before_request_delivery`, `after_backend_commit_before_response`, `during_response_streaming`) fire deterministic faults (`drop_response`, `drop_request`, `bounded_delay`, `duplicate_delivery`, `connection_interruption`). Every fire is journaled with confirmation evidence. An armed fault that never fired makes the case `INCONCLUSIVE`.

**Fixtures (`fixtures/`)** — A protocol-neutral merchant core (inventory, versioned checkouts, orders, idempotency) shared by separate ACP and UCP adapters. A payment simulator with explicit states (`authorized`, `submitted`, `settled`, `failed`, `unknown`) and provider references. Synthetic issuer / user / agent / merchant keys. AP2 and TAP verifiers. An agent registry. Read-only `/_lab/observe/...` endpoints are the independent oracles; `/_lab/admin/...` resets and mutates fixture state. Observation traffic is never routed through the fault proxy.

**Reference targets (`reference_targets/`)** — The same fixture code with option presets:

- `broken` — ignores `Idempotency-Key`, charges before checking session state, trusts `X-Forwarded-Host`, no TAP replay cache, AP2 authentication without checkout binding.
- `corrected` — the inverse of those defects.
- `corrected_minimal` — corrected behaviour without optional discount capabilities.
- `corrected_misdeclared` — claims the UCP checkout profile but does not advertise `dev.ucp.shopping.checkout`.

**Assertions (`lab/assertions`)** — JSON Schema checks against pinned snapshots, field/header/status comparisons, named metrics (order count, settled charge count), fault-fired confirmation, and independent cryptographic oracles (RFC 9421 and SD-JWT) that do not import the verifier under test.

**Reports (`lab/reports`, `lab/runner/store`)** — Each run writes `runs/<id>/bundle.json`, per-blob evidence files, `lab.sqlite` (`test_runs`, `case_results`, `protocol_events`, `fault_events`), JUnit XML, and a single-file HTML viewer. Payloads in evidence are redacted; an unredacted digest is kept for correlation.

## Execution modes

**In-process (default for local reference targets).** The target and proxy are ASGI apps driven through `httpx.ASGITransport`. The test clock and RNG are shared. This is the deterministic path used by the suite and by CI.

**Network.** `commerce-lab serve-target` and `commerce-lab proxy` run as processes (see `docker-compose.yml`). The runner talks HTTP. Without an observation channel, order/charge metrics are `INCONCLUSIVE` rather than assumed.

## Reproducibility

Each run records `suite_version`, `target_revision`, `spec_manifest_digest`, `fixture_seed`, and a frozen clock (default `1790294400`, 2026-09-25T00:00:00Z). Fixture ids, nonces, and signed validity windows are derived from that seed and clock so a replay is not accidentally an expiry test.

## Applicability

`check_applicability` compares the scenario's required capabilities with what discovery advertised:

- required capability missing → `FAIL`
- optional capability missing → `NOT_APPLICABLE`
- capability not defined by the profile → `HARNESS_ERROR` (and the loader already rejects it)

Application-robustness cases additionally require the target to declare the invariant. That distinction is why a merchant can fail a product guarantee without violating a particular protocol clause.
