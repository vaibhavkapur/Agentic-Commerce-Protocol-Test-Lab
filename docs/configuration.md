# Configuration

[Documentation home](index.md)

## Repository and output paths

- `COMMERCE_LAB_ROOT`: source root containing `schemas/`, `manifests/`, `suites/`, and `dashboard/`; defaults to the checkout inferred from `lab/paths.py`.
- `COMMERCE_LAB_RUNS`: output root, default `<source-root>/runs`.
- `commerce-lab --runs-dir PATH ...`: per-command output-root override; place it before the subcommand.

Run data includes one shared `lab.sqlite` at the output root and a subdirectory for each run. There is no PostgreSQL or Redis requirement.

## Protocol profiles and suites

`manifests/profiles/` selects exact releases and schema provenance. `manifests/requirements/` maps assertions to source clauses. `suites/` defines scenario steps, faults, and application invariants. Preserve existing protocol version distinctions: this lab's ACP snapshot is `2026-04-17`, while the related procurement project deliberately uses a different release.

Validate every manifest edit with `commerce-lab validate` before interpreting a run. Unknown capabilities and requirement references are errors, not automatic skips.

## Targets

Targets in `manifests/targets/` declare `kind`, revision, profiles, observation channel, and proxy settings. Local reference targets use in-process fixtures. `network-merchant-example` uses `http://localhost:8000` plus the proxy at `http://localhost:8080` and explicitly declares `observation.kind: none`.

That network example cannot prove order or charge counts: affected assertions are `INCONCLUSIVE`. Adding an observation channel requires a target that actually implements the expected observation interface; an arbitrary URL is not sufficient evidence.

## Reproducibility

Keep seed, frozen clock, suite version, target revision, and specification manifest digest with comparisons. The in-process default shares its deterministic clock with fixtures. Independently started network fixtures may use wall time, so coordinate clocks for authorization-expiry cases rather than interpreting clock drift as a protocol defect.
