# Agentic Commerce Protocol Test Lab — Development Plan

## 1. Project summary

Build a **local test environment for agentic commerce implementations**, with merchant simulators, protocol fixtures, failure injection, and evidence-backed reports.

Example developer workflow:

> “Run my merchant adapter against the supported checkout, authorization, retry, and recovery scenarios. Show which requirement failed and the messages that caused it.”

The lab should:

- load an explicit protocol/version profile
- register a local implementation under test
- discover or configure its supported capabilities
- execute positive and negative scenarios
- inject realistic application and network failures
- inspect observable financial and order effects
- export reproducible results with trace evidence
- distinguish protocol violations from application robustness failures

The core product is **a versioned interoperability and failure-testing toolkit for agentic commerce**.

Planning baseline: **25 September 2026**. This is an independent project test suite, not an official certification service.

---

## 2. Why this project is compelling

The other projects demonstrate that you can build agentic commerce systems. This project demonstrates that you can explain and test their guarantees.

It connects:

- protocol schema validation
- state-machine testing
- authorization and signature verification
- payment idempotency and reconciliation
- distributed-systems failure recovery
- compatibility across versioned implementations

The strongest output is a reproducible failure: exact setup, triggering messages, expected behavior, observed behavior, and supporting evidence.

An implementation can pass schema validation while still charging twice after a timeout. The lab should make both kinds of behavior inspectable.

---

## 3. Protocol coverage strategy

### Initial suite

- **UCP:** discovery/capability handling and a limited checkout lifecycle
- **ACP:** the corresponding supported checkout operations under its own schema
- **AP2:** a selected authorization profile and transaction-binding checks
- **TAP:** signed merchant-request verification and negative fixtures

### Second suite

- **x402:** payment requirements, paid retry, settlement evidence, delivery recovery
- **MPP:** charge flow, receipts, and one selected session method

### Additional packs

- **MCP:** tool discovery, input validation, and paid-tool application boundaries
- **A2A:** Agent Cards, quotation tasks, task lifecycle, and quote artifacts
- **Verifiable Intent:** delegation-chain and selective-disclosure verification

Publish support per profile, version, role, capability, and test case. A protocol badge alone does not communicate meaningful coverage.

---

## 4. MVP scope

Build:

- one CLI runner
- one local merchant fixture with separate UCP and ACP adapters
- one local authorization verifier
- one test issuer and agent registry
- positive and negative fixtures for AP2 and TAP
- a network fault proxy
- JSON and JUnit report export
- a simple browser report viewer
- a deliberately broken implementation and a corrected implementation

Limit the initial suite to roughly **20–30 well-specified cases**. This is a planning target, not a coverage claim.

Defer public endpoint scanning, live card processing, production credentials, every protocol extension, and broad automated vulnerability discovery. Controlled local targets make failures reproducible and interpretable.

---

## 5. Recommended technology stack

- **Runner and CLI:** Python
- **Fixture APIs:** FastAPI
- **Scenario format:** YAML validated by a strict schema
- **Assertions:** JSON Schema plus explicit state/invariant checks
- **Payment helper services:** TypeScript where the selected SDK requires it
- **Cryptographic helpers:** maintained libraries and pinned reference implementations
- **Results:** SQLite for local runs; PostgreSQL when integrating multiple services
- **Dashboard:** a static report viewer or small Next.js application
- **Environment:** Docker Compose
- **CI:** a standard workflow that starts fixtures and publishes reports

Keep a common event format across languages. Avoid building a general distributed test platform before the first meaningful suite works.

---

## 6. High-level architecture

```text
CLI / CI → Suite loader → Scenario runner → Protocol driver
                │               │                ↓
                │               │           Fault proxy
                │               │                ↓
                │               │      Implementation under test
                │               │                ↓
                │               └──────── Fixture merchant / verifier / payment service
                ↓
       Version and requirement registry
                ↓
          Assertion engine ← Observable state + protocol events
                ↓
          JSON / JUnit / browser report
```

Keep drivers, fault injection, assertions, and reports separate. A driver formats messages; an assertion decides whether the observed behavior met a requirement.

---

## 7. Version and requirements registry

For each supported profile, record:

- protocol name and release
- immutable specification URL or repository revision
- schema file and checksum
- reference implementation revision
- SDK version and language
- supported transport and role
- enabled capabilities and extensions
- test-case coverage

The ACP repository currently publishes a **2026-04-17** snapshot alongside unreleased work. Use released snapshots deliberately and keep draft coverage separate. [ACP versioned specifications](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol)

Represent each requirement with a stable lab ID, exact source section, applicability rule, expected behavior, and test IDs. Do not invent a normative MUST where the specification gives an option or leaves behavior to the application.

---

## 8. Two distinct test classes

### Protocol conformance checks

These assert requirements supported by the pinned specification:

- required field and type validation
- negotiated capability behavior
- native state and response handling
- signature and key-binding rules
- supported payment-message serialization

Each case links to a precise normative source.

### Application robustness checks

These test the implementation's declared guarantees:

- no second order after a duplicate completion request
- no overspend under concurrency
- no automatic failover while settlement is uncertain
- recoverable result delivery after payment
- coherent cancellation behavior

Label these as application invariants. A failure may be a serious product defect without being a violation of a particular protocol clause.

---

## 9. Scenario model

Each scenario contains:

- unique case ID and human-readable purpose
- target protocol profile and role
- normative requirement or application invariant
- preconditions and capability requirements
- fixture references
- ordered actions and fault schedule
- observable assertions
- evidence to collect
- cleanup behavior

Illustrative application-level scenario:

```yaml
id: checkout-completion-response-lost
classification: application_robustness
profile: acp-checkout-local
invariant: one-order-and-one-charge-per-operation
preconditions:
  - checkout_ready
  - result_lookup_supported
steps:
  - action: complete_checkout
    operation_key: purchase-001
    fault: drop_response_after_backend_commit
  - action: restart_client_worker
  - action: recover_existing_operation
assertions:
  - metric: merchant_order_count_for_operation
    equals: 1
  - metric: settled_charge_count_for_operation
    equals: 1
  - metric: client_resolved_order_matches_merchant
    equals: true
evidence:
  - redacted_protocol_trace
  - merchant_order_snapshot
  - payment_event_snapshot
```

The runner's action names are lab abstractions, not official protocol methods.

---

## 10. Protocol drivers

Give each protocol an independent driver with a small common interface:

```python
class ProtocolDriver:
    def inspect_target(self, target): ...
    def check_applicability(self, scenario, capabilities): ...
    def prepare_fixture(self, fixture, context): ...
    def execute_step(self, step, context): ...
    def collect_evidence(self, context): ...
```

Avoid mapping every native state into a single universal checkout status. Preserve native status and add a separate lab observation where useful.

Drivers validate outgoing messages so a malformed harness request is not misdiagnosed as a target failure. Negative-test drivers explicitly mark intentional invalidity.

---

## 11. Merchant and payment fixtures

Create a controlled backend with:

- fixed inventory and price scenarios
- versioned checkout records
- an order store
- a simulated payment processor
- deterministic idempotency behavior
- signed event delivery where the tested profile requires it
- read-only observation endpoints for assertions

Expose UCP and ACP separately over the shared business fixture. Their native schemas and state handling remain independent.

The simulator should distinguish payment authorized, submitted, settled, failed, and unknown. Use provider references and durable event records, not a single boolean success flag.

For later testnet suites, replace only the payment adapter and preserve the same application assertions where they remain applicable.

---

## 12. Authorization fixture design

Generate synthetic issuer, user, agent, and merchant keys. Keep their roles distinct.

Test categories:

- valid authorization
- tampered signed content
- unknown issuer
- unsupported algorithm
- wrong agent key
- expired evidence
- checkout/payment mismatch
- missing required disclosure
- authentic agent request with ineligible purchase terms

AP2 and VI fixtures must follow their respective pinned structures. AP2's current documentation defines checkout and payment authorization responsibilities; use those to decide which verifier should reject which input. [AP2 specification](https://ap2-protocol.org/ap2/specification/)

For VI, add independent chain/disclosure cases in the later pack. Its published baseline is draft v0.1, so expected behavior must be attached to a revision. [VI reference implementation](https://github.com/agent-intent/verifiable-intent)

---

## 13. TAP test pack

Use the official sample as a starting point for the test registry and signed-request verifier. [TAP reference implementation](https://github.com/visa/trusted-agent-protocol)

Include cases for:

- recognized agent and valid request
- unknown key
- expired or otherwise stale signature context
- mutation of a covered request component
- wrong merchant context
- replay behavior under the selected profile
- key rotation under the lab's declared trust policy

Test request canonicalization at the proxy boundary. The verifier must use the actual externally signed request context rather than blindly trusting caller-supplied forwarding headers.

Local key revocation and key-cache behavior are application policy unless explicitly required by the pinned profile. Label those assertions accordingly.

---

## 14. Fault injection

Support named, deterministic fault points:

- before request delivery
- after backend commit but before response delivery
- during response streaming
- after payment submission but before client persistence
- before webhook acknowledgment
- during worker restart

Fault types:

- dropped response
- bounded delay
- duplicate delivery
- reordered events
- connection interruption
- process crash
- stale price or stock change

Log when and where each fault actually fired. If a fault was never injected, the scenario cannot claim to have tested recovery from it.

Do not modify signed content unless signature failure is the intended case. For a validly signed malicious request, construct the altered fixture first and sign it using the appropriate synthetic key.

---

## 15. Independent assertions and oracles

Use multiple evidence sources:

- protocol response validation
- merchant order records in owned fixtures
- payment-provider or simulated settlement records
- budget journal entries
- receipt verification
- delivery-result checksums

A client log saying “succeeded” is not enough to prove that the merchant created exactly one order.

For black-box external targets, report only observable properties. If order count or settlement state cannot be established, mark the assertion inconclusive rather than assuming success.

Use published vectors, a second verifier, or independently implemented assertions for critical cryptographic cases. Comparing an implementation with itself provides weak evidence.

---

## 16. Result classification

Use explicit results:

- `PASS`: applicable assertions passed with required evidence
- `FAIL`: evidence demonstrates a violated requirement or invariant
- `NOT_APPLICABLE`: optional capability was not advertised and the case does not apply
- `NOT_RUN`: the case was not executed
- `INCONCLUSIVE`: the observable outcome is insufficient
- `HARNESS_ERROR`: the runner or fixture failed

If a required capability is missing for a claimed profile, record a failure; do not hide it as not applicable.

Separate target timeouts from harness infrastructure outages. Only enforce a specific timing threshold when the protocol or declared application contract supplies one.

---

## 17. Data model and reports

### `test_runs`

- `id`, `suite_version`, `target_id`, `target_revision`
- `spec_manifest_digest`, `fixture_seed`, `environment`
- `started_at`, `finished_at`, `run_status`

### `case_results`

- `run_id`, `case_id`, `classification`, `requirement_id`
- `result`, `expected`, `observed`, `duration_ms`
- `capability_snapshot`, `evidence_references`

### `protocol_events`

- `run_id`, `case_id`, `event_id`, `direction`
- `protocol`, `profile_version`, `operation`
- `timestamp`, `redacted_payload_reference`, `payload_digest`

### `fault_events`

- `case_id`, `fault_type`, `trigger_point`, `fired_at`
- `affected_operation`, `confirmation_evidence`

Export machine-readable JSON and JUnit plus a browser report. Include raw evidence only when it is synthetic or appropriately protected.

---

## 18. CLI and optional API

Proposed command interface:

```text
commerce-lab profiles list
commerce-lab targets inspect --target local-merchant
commerce-lab run --suite checkout-core --target local-merchant
commerce-lab run --case checkout-completion-response-lost --seed 42
commerce-lab report --run run-001 --format html
commerce-lab compare --baseline run-001 --candidate run-002
```

If a server mode is useful later:

```http
POST /v1/runs
GET  /v1/runs/{id}
GET  /v1/runs/{id}/cases
GET  /v1/runs/{id}/evidence
POST /v1/runs/{id}/cancel
```

Cancellation stops new tests and performs cleanup. It must not erase evidence or assume an already submitted payment was reversed.

---

## 19. Reproducibility and isolation

Each run should have:

- isolated fixture data or a unique run namespace
- deterministic seed
- configurable test clock
- known synthetic keys
- pinned container and dependency versions
- captured capability snapshot
- reset and cleanup hooks

Recreate signed fixtures with the intended clock and validity interval. Do not accidentally test only expired historical tokens.

Keep local deterministic suites separate from live testnet suites. Testnet runs need real timing, balance checks, bounded spend, and explicit recording of network/provider conditions.

---

## 20. Dashboard and comparison design

The dashboard should answer:

- which profile and revision were tested?
- what was applicable?
- what passed or failed?
- which cases were skipped or inconclusive?
- what exact message or state caused failure?
- how does the candidate differ from the baseline?

Compute pass rate only over executed, applicable `PASS` and `FAIL` cases, while displaying all other counts alongside it. Never advertise a high percentage without the supported-case denominator.

Separate conformance results, application robustness results, and measured performance. If performance is shown, include sample size, workload, environment, units, and aggregation method.

---

## 21. Phased delivery plan

### Phase 1 — runner and one recovery case

Build scenario loading, local fixture, event capture, assertions, and JSON reports.

Success: the deliberately broken merchant charges twice after a lost response and the lab detects it.

### Phase 2 — UCP and ACP packs

Add native drivers, versioned schema validation, capability applicability, and checkout lifecycle cases.

Success: unsupported optional features and genuine required-field failures are classified differently.

### Phase 3 — AP2 and TAP packs

Add real signed fixtures, independent verification checks, and the local registry.

Success: tampering and wrong-context requests fail with precise evidence.

### Phase 4 — CI and report viewer

Add JUnit export, baseline comparison, trace inspection, and reproducible run bundles.

Success: a clean checkout can reproduce the documented failure and corrected result.

### Phase 5 — machine-payment packs

Add x402 and MPP charge cases, then one MPP session pack.

Success: payment and delivery outcomes remain separate under injected failures.

MCP, A2A, and VI packs extend this foundation afterward.

---

## 22. Suggested roadmap and repository

Planning estimate: **four weeks for the initial suite; six to eight weeks for broader payment coverage**, assuming reusable fixtures from the other projects.

- Week 1: runner, failure proxy, event schema, first independent oracle.
- Week 2: UCP/ACP drivers and requirement registry.
- Week 3: AP2/TAP fixtures and negative cases.
- Week 4: CI, reports, corrected/broken comparison, documentation.
- Later weeks: x402/MPP, session recovery, additional protocol packs.

```text
agentic-commerce-lab/
  lab/{cli,runner,assertions,reports,requirements}/
  drivers/{ucp,acp,ap2,tap}/
  suites/{conformance,robustness}/
  fixtures/{merchants,issuers,registries,payments}/
  fault-proxy/
  reference-targets/{broken,corrected}/
  schemas/
  manifests/
  tests/{runner,drivers,oracles}/
  dashboard/
  docs/
  docker-compose.yml
```

---

## 23. Testing the lab and demo scenarios

Test the testing system itself:

- invalid scenario definitions are rejected
- a missing fault trigger cannot produce a false pass
- an unavailable fixture becomes `HARNESS_ERROR`
- unknown capabilities are not silently assumed
- assertion failures survive report export
- secrets are redacted from exported traces
- seeded runs reproduce the same local behavior
- negative controls fail and corrected controls pass

### Demo A — broken versus corrected checkout

Lose a completion response and retry. Show duplicate effects in the broken target and one operation in the corrected target.

### Demo B — identity versus purchase authority

Authenticate the agent request successfully, then reject authorization bound to a different checkout.

### Demo C — applicability

Run against a merchant lacking an optional capability. Explain why that case is not applicable while missing a required profile feature is a failure.

### Demo D — payment without delivery

Settle a controlled paid request, drop its result, and verify recovery without another charge.

---

## 24. Definition of done and portfolio framing

Ship a bounded, documented suite with immutable protocol references, evidence-backed assertions, correct applicability handling, a deliberately broken target, and a reproducible corrected result.

Required artifacts:

- supported-profile manifest
- test-case inventory and source mapping
- fixtures and provenance notes
- machine-readable reports
- redacted protocol traces
- CI example
- architecture and limitations document
- short failure/recovery demonstration

Portfolio wording after completion:

> Built a versioned agentic-commerce test lab with protocol drivers, signed authorization fixtures, network fault injection, and evidence-backed reports for checkout integrity, payment idempotency, and recovery behavior.

Describe exact supported profiles instead of claiming universal conformance or certification.

---

## 25. Primary references and immediate next steps

- [UCP specification](https://ucp.dev/specification/overview/)
- [ACP released specifications](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol)
- [AP2 specification](https://ap2-protocol.org/ap2/specification/)
- [TAP reference implementation](https://github.com/visa/trusted-agent-protocol)
- [x402 payment flow](https://docs.x402.org/core-concepts/http-402)
- [MPP documentation](https://mpp.dev/)
- [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [A2A concepts](https://a2a-protocol.org/latest/topics/key-concepts/)
- [Verifiable Intent](https://github.com/agent-intent/verifiable-intent)

Start with one deliberately broken checkout, one lost-response scenario, and an independent count of resulting orders and charges. Build the runner around evidence that can prove that defect before expanding protocol coverage.

**One-sentence summary:** A reproducible lab that shows which agentic-commerce guarantees an implementation actually satisfies, under both ordinary and failure conditions.
