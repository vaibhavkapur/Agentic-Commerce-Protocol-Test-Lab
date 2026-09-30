# Agentic Commerce Protocol Test Lab

A local interoperability and failure-testing toolkit for agentic commerce implementations. Loads an explicit protocol/version profile, drives a local target, injects named faults, and reports which requirement or application invariant failed.

> **[Read the full documentation](docs/index.md)**

Built with Python, versioned manifests, and self-contained HTML/JUnit reports. The implemented control interface is the CLI.

## Getting Started

```bash
# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Validate manifests, then run the core suite
commerce-lab validate
commerce-lab run --suite checkout-core --target local-merchant-corrected --seed 42
```

Open `runs/<run-id>/report.html` in a browser. This is an independent test suite, not an official certification service.

See [Getting Started](docs/getting-started.md) for prerequisites, cloning, configuration, and verification.

## Quick Example

```bash
# Demo A — lost completion response. Broken target charges twice; corrected recovers once.
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-broken --run-id run-broken --seed 42
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-corrected --run-id run-corrected --seed 42
commerce-lab compare --baseline run-broken --candidate run-corrected

# Demo B — TAP authenticates the agent; AP2 rejects a mandate bound to another checkout.
commerce-lab run --suite demo-identity-vs-authority --target local-merchant-corrected --seed 42

# Demo C — optional capability missing → NOT_APPLICABLE; required capability missing → FAIL.
commerce-lab run --suite demo-applicability --target local-merchant-minimal --seed 42
commerce-lab run --suite demo-applicability --target local-merchant-misdeclared --seed 42
```

The broken and misdeclared targets intentionally produce nonzero exits. Run those commands separately so expected failures do not prevent the remaining demos. See [CLI Reference](docs/cli-reference.md) for exit codes and [Reports](docs/reports.md) for result meanings.
