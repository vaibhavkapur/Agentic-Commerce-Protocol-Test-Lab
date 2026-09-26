# CLI Reference

[Documentation home](index.md)

## Interface

`commerce-lab --help` is the implemented control interface. A REST run-management API is not implemented. Commands and defaults are defined in `lab/cli/main.py`.

## Discovery and validation

```bash
commerce-lab profiles list
commerce-lab profiles show acp-checkout-2026-04-17
commerce-lab targets list
commerce-lab targets inspect --target local-merchant-corrected
commerce-lab suites
commerce-lab cases --suite checkout-core
commerce-lab validate
```

`validate` checks manifests, requirement references, suites, target profile IDs, and pinned schema checksums. Discovery/listing commands support `--json` where shown by their help. `cases --format md` emits a Markdown inventory.

## Run

```bash
commerce-lab --runs-dir ./runs-review run \
  --suite checkout-core --target local-merchant-corrected \
  --seed 42 --run-id checkout-review --format json,junit,html
```

`--runs-dir` is a global option and belongs before the subcommand. Use `--case` repeatedly to select cases, `--stop-on-harness-error` to stop after a harness failure, and `--frozen-clock` for explicit epoch seconds. Runs default to a frozen clock at `1790294400`; `--wall-clock` opts into real time.

By default `run` exits 1 for any `FAIL`, `HARNESS_ERROR`, or `INCONCLUSIVE`. `--allow-inconclusive` excludes inconclusive cases from that exit calculation; it does not turn them into passes. Manifest and missing-file errors handled by the CLI exit 2.

## Reports and comparison

```bash
commerce-lab runs
commerce-lab report --run RUN_ID --format html
commerce-lab compare --baseline BASELINE_ID --candidate CANDIDATE_ID --fail-on-regression
```

Report formats are `json`, `junit`, and `html`. Comparison formats are `text`, `json`, and `html`; use `--out` to choose a file for HTML comparison. `--fail-on-regression` makes a regression exit 1. See [Reports](reports.md) for the meaning of each result.

## Network fixtures

`serve-target` runs a local reference target with `--target`, `--host`, `--port`, `--seed`, and optional `--frozen-clock`. `proxy` requires `--upstream` and optionally accepts `--host` and `--port` (default 8080). These expose fixture and fault-control endpoints; use the local topology in [Deployment](deployment.md).
