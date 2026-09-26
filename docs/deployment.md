# Deployment and CI

[Documentation home](index.md)

## In-process execution

The normal deployment is an editable local checkout with `commerce-lab` installed. CI can run the same commands without starting containers:

```bash
commerce-lab validate
python -m pytest -q
commerce-lab run --suite checkout-core --target local-merchant-corrected --seed 42
```

Archive run bundles even when the suite exits nonzero. JUnit is written to the run directory; the browser report is a self-contained HTML file. The exact output paths are printed by the CLI.

## Network mode

```bash
docker compose up --build
```

The supplied stack serves the corrected merchant at `8000` and its fault proxy at `8080`. In another terminal with the CLI installed:

```bash
commerce-lab run --suite checkout-core --target network-merchant-example --seed 42
```

The example declares no observation channel, so some results are intentionally inconclusive and the default run exit can be nonzero. Use [Configuration](configuration.md) to understand clocks, target declarations, and evidence requirements.

## Operational scope

These services expose synthetic fixture administration and fault injection. Run them in a local test environment against owned targets. The lab does not provision a hosted run-management API, external certification service, or live payment processor. See [Limitations](limitations.md).
