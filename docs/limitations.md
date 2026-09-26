# Limitations

This lab is a bounded, local test suite. It is not a certification authority and it does not claim that an implementation “supports UCP/ACP/AP2/TAP” in general.

## What the initial suite does not cover

- Public endpoint scanning or unsolicited testing of third-party services.
- Live card processing, production credentials, or real money movement.
- Every protocol extension, transport binding, or optional capability.
- Broad automated vulnerability discovery.
- x402 payment requirements / paid retry / settlement evidence (planned later pack).
- MPP charge flow, receipts, and session methods (planned later pack).
- MCP tool discovery and paid-tool boundaries.
- A2A Agent Cards, quotation tasks, and quote artifacts.
- Verifiable Intent delegation chains (draft v0.1; any later pack must pin a revision).
- UCP transports other than the REST checkout profile used here (MCP, A2A, embedded).
- ACP transports other than REST (`mcp` is recorded as a lab decision, out of scope).
- Timing SLAs, unless a pinned clause or a declared application contract supplies one.

## Evidence limits

Order counts and settlement state are taken from lab observation endpoints on owned fixtures. Against a black-box network target with `observation.kind: none`, those metrics are `INCONCLUSIVE`. A client log that says “succeeded” is never treated as proof that exactly one order exists.

Cryptographic cases use published structures plus a second, independently implemented verifier. Comparing the fixture verifier with itself is not treated as evidence.

## Result limits

Pass rate is `PASS / (PASS + FAIL)` over executed, applicable cases. `NOT_APPLICABLE`, `NOT_RUN`, `INCONCLUSIVE`, and `HARNESS_ERROR` are displayed alongside and are not folded into a higher percentage.

An armed fault that never fired cannot produce `PASS`. Target timeouts are distinct from harness outages (`HARNESS_ERROR`).

## Trust and policy

TAP key revocation and key-cache behaviour are labelled `application_policy` unless the pinned profile requires them. Local registry contents and synthetic keys are lab fixtures, not a production trust list.

The lab never modifies a validly signed payload to create a negative case. Tampered fixtures are constructed first and then signed with the appropriate synthetic key.

## Draft versus released

ACP coverage uses the published `2026-04-17` snapshot, not unreleased work on the default branch. TAP coverage follows the official sample at the pinned revision and is labelled `reference_sample`. Do not read either as “the current unspecified tip of the repository.”
