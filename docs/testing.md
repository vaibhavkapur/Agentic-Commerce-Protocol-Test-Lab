# Testing

[Documentation home](index.md)

## Harness checks

```bash
python -m pytest -q
commerce-lab validate
```

Pytest checks driver output, independent cryptographic oracles, applicability, fault controls, manifest validation, redaction, reports, and reproducibility. These tests verify the harness itself; a target's conformance report is produced separately by `commerce-lab run`.

## Target checks

```bash
commerce-lab run --suite checkout-core --target local-merchant-corrected --seed 42
commerce-lab run --suite demo-identity-vs-authority --target local-merchant-corrected --seed 42
commerce-lab run --suite demo-applicability --target local-merchant-minimal --seed 42
```

The [case inventory](test-case-inventory.md) explains coverage. Use `commerce-lab cases` to inspect the current manifests instead of relying on a historical test count.

## Interpreting evidence

`PASS` and `FAIL` cover executed applicable checks. `NOT_APPLICABLE`, `NOT_RUN`, `INCONCLUSIVE`, and `HARNESS_ERROR` remain separate. An unfired fault cannot prove recovery, and a missing observation channel cannot prove exactly one order. See [Reports](reports.md) and [Limitations](limitations.md).
