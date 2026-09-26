# Reports

[Documentation home](index.md)

Each run writes a self-contained directory under `runs/<run-id>/`:

| File | Contents |
|---|---|
| `bundle.json` | Full machine-readable run: cases, steps, protocol events, fault events, evidence |
| `report.json` | Summary plus per-case result, reason, requirement source, evidence references |
| `junit.xml` | CI-oriented export |
| `report.html` | Single-file browser viewer (`dashboard/template.html` with the bundle embedded) |
| `evidence/*.json` | One redacted blob per reference |

The shared index is `runs/lab.sqlite`, one level above the individual run directories. It stores `test_runs`, `case_results`, `protocol_events`, and `fault_events`. `--runs-dir` changes the root for both the database and run directories.

Regenerate a format:

```bash
commerce-lab report --run <run-id> --format html
commerce-lab compare --baseline <id> --candidate <id> --format html
```

## Pass rate

Displayed as `PASS / (PASS + FAIL)` over executed, applicable cases. `NOT_APPLICABLE`, `NOT_RUN`, `INCONCLUSIVE`, and `HARNESS_ERROR` are shown as separate counts and are not used to inflate the percentage. Conformance, application robustness, and (if ever added) performance stay in separate groupings.

## JUnit mapping

| Lab result | JUnit |
|---|---|
| `PASS` | passed `testcase` |
| `FAIL` | `<failure type="FAIL">` |
| `INCONCLUSIVE` | `<failure type="INCONCLUSIVE">` — CI must not go green on missing evidence |
| `HARNESS_ERROR` | `<error type="HARNESS_ERROR">` |
| `NOT_APPLICABLE`, `NOT_RUN` | `<skipped>` with the reason |

## Redaction

Headers `Authorization`, `X-Api-Key`, `Cookie`, and `Set-Cookie` are replaced with `[REDACTED]`. JSON keys `token`, `api_key`, `secret`, `private_key`, `password`, and JWK private members are stripped. Bearer tokens and PEM private keys in strings are scrubbed. The unredacted payload digest stays on the protocol event so fixture-side records can be correlated without exporting secrets.

## Comparison

`commerce-lab compare` requires the same suite id to be meaningful and warns when `spec_manifest_digest` differs. A result is an improvement when it moves toward `PASS` (for example `FAIL` → `PASS`) and a regression when it moves away. `--fail-on-regression` exits `1` when any case regressed.
