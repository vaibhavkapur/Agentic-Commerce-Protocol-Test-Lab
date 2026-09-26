# Fixtures and provenance

Every profile manifest records the specification URL, repository revision, schema path, SHA-256 checksum, retrieval date, and lab decisions. `commerce-lab validate` recomputes those checksums.

## Pinned specifications

| Profile | Status | Revision | Retrieved | Schemas |
|---|---|---|---|---|
| `acp-checkout-2026-04-17` | released snapshot | `7fdd78df677a94dce04c770644b0fbbb1401272b` | 2026-09-26 | `schemas/pinned/acp/2026-04-17/` |
| `ucp-checkout-2026-08-25` | tagged release | `cd78fb38e819` (see profile manifest) | 2026-09-26 | `schemas/pinned/ucp/2026-08-25/` |
| `ap2-mandates-v0.2.0` | tagged release | `b4587ac1d055` (see profile manifest) | 2026-09-26 | `schemas/pinned/ap2/v0.2.0/` |
| `tap-signed-requests-2025-10-28` | reference sample | `16d59bdf3f8a` | 2026-09-26 | `schemas/pinned/tap/` |

Requirement files under `manifests/requirements/` map each `LAB-…` id to a document, section, quote, keyword (`MUST` / `schema` / invariant), and applicability rule. The lab does not invent a normative MUST where the specification leaves behaviour optional or application-defined.

## Synthetic trust material

`fixtures/issuers/trust_material.py` generates distinct keys for issuer, rogue issuer, user device, agent (Ed25519 and P-256), rotated agent, rogue agent, merchant, and credential-provider. Roles are not interchangeable: a valid agent signature is not purchase authority, and a valid mandate for checkout A is not authority for checkout B.

Keys are derived from the run seed. They are test fixtures only. Exported traces redact `Authorization`, bearer tokens, API keys, PEM private keys, and JWK private members (`d`, `p`, `q`, …).

## Merchant and payment fixture

Inventory is fixed: `sku_tee_blue_m`, `sku_socks_best`, `sku_shoe_gold_9`, `sku_ebook_guide`. Prices and stock can be changed through `/_lab/admin/inventory/{sku}` so a scenario can inject a stale price or stock change *before* the signed content of a later request is built.

Payment tokens:

| Token | Simulator outcome |
|---|---|
| `tok_ok` | submitted, then settled |
| `tok_decline` | failed |
| `tok_unknown` | submitted; state stays `unknown` |
| `tok_slow` | authorized until `settle_pending()` |

Processor-side idempotency is a separate switch from HTTP `Idempotency-Key`. The broken target disables the processor key so a retried completion settles twice even if the merchant later notices the session is complete.

## Observation endpoints

| Path | Oracle |
|---|---|
| `GET /_lab/observe/orders` | merchant order records |
| `GET /_lab/observe/payments` | payment-provider records and states |
| `GET /_lab/observe/checkouts/{id}` | versioned checkout |
| `GET /_lab/observe/events` | merchant and payment journals |
| `GET /_lab/observe/verifiers` | TAP/AP2 decisions |
| `GET /_lab/observe/registry` | agent directory snapshot |

These endpoints are the evidence source for “exactly one order / exactly one settled charge.” They are not protocol surface.

## Reference target variants

See `reference_targets/__init__.py`. The broken and corrected implementations share one codebase; only the option presets differ, so a comparison report can name the exact behaviours that separate the two results.
