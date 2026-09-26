# Getting Started

[Documentation home](index.md)

## Prerequisites

Use Python 3.9+ (the project requires it; the container uses Python 3.12), pip, and Git. Docker is optional for network-mode fixtures. Keep the source checkout: manifests, schema snapshots, suites, and the HTML template are repository assets.

## Clone and install

```bash
git clone https://github.com/vaibhavkapur/Agentic-Commerce-Protocol-Test-Lab.git
cd Agentic-Commerce-Protocol-Test-Lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e ".[dev]"
```

The lock file records the fixture environment; editable installation provides the `commerce-lab` command and access to repository assets.

## First run

```bash
commerce-lab validate
commerce-lab profiles list
commerce-lab targets inspect --target local-merchant-corrected
commerce-lab run --suite checkout-core --target local-merchant-corrected --seed 42
```

The default local reference target runs in process. No HTTP service, wallet, database server, or UI server needs to be started. The command prints the run ID and paths to JSON, JUnit, and HTML results. Open `runs/<run-id>/report.html` in a browser.

## Compare a broken and corrected target

```bash
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-broken --run-id demo-broken --seed 42
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-corrected --run-id demo-corrected --seed 42
commerce-lab compare --baseline demo-broken --candidate demo-corrected
```

The broken-target command is expected to exit nonzero. Run the commands separately so that intentional failures do not prevent the comparison. Choose new run IDs for repeated demonstrations.

## Next steps

Read [CLI Reference](cli-reference.md) for command flags and exit behavior, [Configuration](configuration.md) for target manifests, [Reports](reports.md) for result semantics, and [Demos](demos.md) for identity versus authority and capability applicability. This is an independent test suite, not a certification service.
