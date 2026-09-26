# Failure and recovery demonstrations

[Documentation home](index.md)

These are the four demos from the development plan. Commands assume the package is installed and the working directory is the repository root.

## Demo A — broken versus corrected checkout

Lose a completion response after the backend commits, restart the client worker, and replay the same operation. The broken merchant charges twice. The corrected merchant produces one order and one settled charge, and the client resolves the same order id the merchant holds.

```bash
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-broken --run-id demo-a-broken --seed 42
commerce-lab run --suite demo-broken-vs-corrected --target local-merchant-corrected --run-id demo-a-corrected --seed 42
commerce-lab compare --baseline demo-a-broken --candidate demo-a-corrected
```

Expected: six `FAIL` on broken (duplicate settled charges, missing `Idempotent-Replayed`, legacy error bodies); six `PASS` on corrected. Open `runs/demo-a-corrected/compare-demo-a-broken.html` after:

```bash
commerce-lab compare --baseline demo-a-broken --candidate demo-a-corrected --format html
```

The lead case is `acp-complete-response-lost-replay`. Evidence includes the redacted protocol trace, merchant order snapshot, payment event snapshot, and the fault log proving `drop_response` actually fired.

## Demo B — identity versus purchase authority

A TAP-signed merchant request from the registered agent is accepted. An AP2 mandate that authenticates successfully but is bound to a *different* checkout is rejected.

```bash
commerce-lab run --suite demo-identity-vs-authority --target local-merchant-corrected --run-id demo-b --seed 42
```

Expected: all six cases `PASS` on the corrected target. `tap-forwarded-host-spoof-rejected` also `FAIL`s on the broken target, which trusts caller-supplied `X-Forwarded-Host` instead of the authority the proxy observed.

## Demo C — applicability

Run the same four cases against a merchant that simply omits optional discounts, then against a merchant that claims the UCP checkout profile but does not advertise the required checkout capability.

```bash
commerce-lab run --suite demo-applicability --target local-merchant-minimal --run-id demo-c-minimal --seed 42
commerce-lab run --suite demo-applicability --target local-merchant-misdeclared --run-id demo-c-misdeclared --seed 42
```

Expected on minimal: `acp-discount-extension` and `ucp-discount-capability` are `NOT_APPLICABLE`; discovery and create-checkout `PASS`. Expected on misdeclared: ACP cases `NOT_RUN` (profile not claimed); UCP cases `FAIL` because a required capability is missing.

## Demo D — payment without delivery

Settle a controlled paid request, drop the result, and recover without another charge. This is the same recovery path as Demo A (`acp-complete-response-lost-replay`, `ucp-complete-response-lost-replay-same-key`, `ucp-complete-response-lost-get-then-replay`) plus `acp-complete-request-dropped-before-delivery` (the request never reaches the merchant, so there must be zero charges).

```bash
commerce-lab run --case acp-complete-request-dropped-before-delivery --target local-merchant-corrected --seed 42
commerce-lab run --case acp-complete-response-lost-replay --target local-merchant-corrected --seed 42
```

On the corrected target both pass: no extra charge after recovery, and no charge at all when the request was dropped before delivery. On the broken target the lost-response replay settles twice.

## Reproducing a CI-style bundle

```bash
commerce-lab run --suite checkout-core --target local-merchant-corrected --run-id demo-full --seed 42
open runs/demo-full/report.html
```
