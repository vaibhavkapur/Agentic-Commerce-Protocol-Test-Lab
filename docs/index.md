# Agentic Commerce Protocol Test Lab

A versioned interoperability and failure-testing toolkit that exercises a declared target and reports requirements, application invariants, and evidence separately.

[Get Started](getting-started.md) · [CLI Reference](cli-reference.md) · [Repository README](../README.md)

## Key Features

- Pinned ACP, UCP, AP2, and TAP profiles with manifest and schema validation.
- Deterministic faults, capability applicability, and independent observation/verification.
- JSON, JUnit, HTML, and baseline/candidate comparison with explicit inconclusive and harness-error results.

## Tech Stack and Scope

Python / FastAPI fixtures / httpx / JSON Schema / pytest; SQLite run index and self-contained browser reports. The control interface is a CLI, not a run-management REST API.

## Documentation

- [Getting Started](getting-started.md)
- [Architecture](architecture.md)
- [CLI Reference](cli-reference.md)
- [Configuration](configuration.md)
- [Testing](testing.md)
- [Deployment and CI](deployment.md)
- [Reports and Storage](reports.md)
- [Fixtures and Provenance](fixtures-and-provenance.md)
- [Case Inventory](test-case-inventory.md)
- [Demos](demos.md)
- [Limitations](limitations.md)

## Project Structure

- `lab/`, `drivers/`, `fault_proxy/`: runner, assertions, protocol drivers, and faults.
- `manifests/`, `schemas/`, `suites/`: pinned contracts and test definitions.
- `fixtures/`, `reference_targets/`, `dashboard/`, `tests/`: local targets, reports, and harness verification.

The implementation guides describe the current code. [Development plan](../plan.md) records design intent and future work; planned features are not automatically implemented.

## Related projects

These are independent companion repositories, not runtime dependencies or claims of an implemented integration:

- [Cross-Border Payments Engine](https://github.com/vaibhavkapur/Cross-Border-Payments-Engine): remittance quoting, settlement lifecycle, and ledger demonstration.
- [Stablecoin Payments API](https://github.com/vaibhavkapur/Stablecoin-Payments-API): customer, wallet, deposit, transfer, and checkout API.
- [Agentic Commerce + Stablecoin Checkout](https://github.com/vaibhavkapur/Agentic-Commerce-Stablecoin-Checkout): conversational commerce, policy checks, and payment routing.
- [Smart Wallet Policy Engine](https://github.com/vaibhavkapur/smart-wallet-policy-engine): transaction risk evaluation and wallet authorization.
- [Stablecoin Payment Orchestrator](https://github.com/vaibhavkapur/Stablecoin-Payment-Orchestrator): USDC routing, workers, and treasury accounting.
