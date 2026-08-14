# Knowledge Operations P1 Source Connectors and Evidence Readiness

## Status and authority boundary

This P1 implementation is an offline-first, research/competition readiness
surface. It is not a diagnostic, treatment, emergency-triage, or automated
clinical-decision system. Every new record fixes:

- `knowledge_effect=informational_only`;
- `runtime_authority=none`;
- `contains_patient_data=false`;
- `release_ready=false` for draft/release-readiness objects.

Operational `SourcePolicy.live_network_enabled` remains `false`. The existing
`AcquisitionService` remains synthetic/offline-only. The optional live smoke
validator is a separate contract checker with no import or call path to the
ledger, Claim promotion, manifests, product database, pathway, UI, or runtime.

## P1a data flow

```text
typed official identifier
  -> exact endpoint request builder
  -> fake transport + bounded parser (default P1a)
  -> metadata-only structured record
  -> synthetic SourceCandidate / SourceSnapshot
  -> EVIDENCE_CANDIDATE (evc-*; digest + locator, no source text)
  -> machine_draft_claim_v2 (dcl-*; draft, synthetic, non-releasable)
  -> existing ReviewPacketBuilder
  -> existing ReviewEventService + ReviewerVerifier attestation
  -> production_eligible=false / release_ready=false
```

There is no EvidenceCandidate-to-v1-registry path. EvidenceCandidate and
MachineDraftClaim use exact `record_type`, `payload_type`, collection, ID
namespace, and ledger-version checks. The append boundary rejects a synthetic
successor that attempts to become non-synthetic.

## Official metadata connectors

| Source | Exact contract | Retained fields | Explicit exclusions |
|---|---|---|---|
| DailyMed | `GET /dailymed/services/v2/spls/{set_id}/history.json` | SetID, SPL version, publication date, title metadata, locator | label body; automatic assertion of the latest FDA conclusion |
| EMA | documented website medicines JSON data file | product number, medicine name, active substance/status/revision metadata, official locator | PMS/SPOR registration APIs; undocumented XHR/backends; HTML scraping; ePI pilot; document body |
| MedlinePlus | dated official health-topics XML feed | topic ID/title/language/update/canonical link | full summary, patient-facing body, third-party body, translation/adaptation |
| PubMed | E-utilities ESummary JSON for a typed PMID | bibliographic title/date/source and record locator | abstract, article body, clinical conclusion |
| PMC OA | legacy OA service for a typed PMCID | per-article licence label/retraction/locator metadata | article/full-text package; automatic open-licence conclusion |

PubMed and PMC are separate policies and rights scopes. NCBI requests contain
no API key or personal email and are rate-limited to at least 350 ms between
requests. The legacy PMC OA service is documented as scheduled to stop on or
after 2026-08-24; contract failure is a Gap, not a scraping fallback.

## Controlled input boundary

Live builders accept only strict models:

- lower-case UUID-form DailyMed SetID;
- fixed registered EMA dataset ID;
- fixed MedlinePlus feed kind plus a typed date;
- numeric PMID or upper-case `PMC` PMCID.

They do not accept `query: str`, AcquisitionRequest free text, patient
sentences, URLs, or arbitrary search terms. Path and query values are checked
again at the transport boundary. Percent encoding, nested encoding, Unicode
confusables, control characters, case variants outside the ID grammar,
unexpected keys, duplicate keys, and non-allowlisted paths fail before DNS and
before fake request capture.

## DNS/socket/TLS binding and response limits

The optional live transport:

1. requires both global egress and exact case-sensitive Knowledge flag value
   `"true"`, plus an opaque `KnowledgeEgressPermit`;
2. accepts HTTPS on port 443 only, with exact host/path/query and no userinfo,
   fragment, proxy, cookie, redirect, or caller-supplied URL;
3. resolves all DNS answers and rejects the whole request if any answer is not
   globally routable;
4. connects only to a validated IP, performs TLS with the original hostname as
   SNI using `ssl.create_default_context()`, certificate verification,
   hostname checking, and TLS 1.2 minimum;
5. verifies `getpeername()` against the validated DNS set before sending the
   HTTP request, while preserving the original official `Host` header;
6. sends `Accept-Encoding: identity` and rejects compressed or transformed
   responses;
7. does not trust `Content-Length`: it rejects an over-cap declaration, reads
   non-chunked bodies to EOF and chunked bodies through bounded framing, applies
   a streaming hard cap, and rejects declaration/actual-size mismatch;
8. accepts only endpoint-allowlisted JSON/XML MIME types and strict UTF-8;
9. applies bounded JSON depth/container/field/scalar checks and DTD/entity-free
   XML depth/element/attribute/text checks.

At most two retries are possible, only for timeout, HTTP 429, and selected
500/502/503/504 responses. Sleeper, clock, and jitter are injectable. HTTP
401/403/404, redirects, identity failures, parser failures, and policy failures
are never retried. `Retry-After` over 30 seconds is not slept.

## Stable error and disposition model

The connector taxonomy includes feature/policy, DNS/peer/TLS, HTTP, response,
parser, contract, and rights errors. Each maps to one of `retry`, `gap`,
`abort`, or `not_attempted`. A live report normalizes those outcomes to:

- `validated`;
- `access_blocked`;
- `rate_limited`;
- `network_failed`;
- `contract_changed`;
- `rights_unresolved`;
- `not_attempted`.

The report contains only source, official documentation URL, endpoint
origin/path template, timestamp, HTTP status, normalized MIME, byte count,
whole-response digest, parsed record count, stable error, and limitations. It
cannot contain response bodies or write Knowledge state.

## Rights evidence and manifest history

`source_policies_v2.json` and `bundle_index_v2.json` remain byte-identical.
`source_policies_v2_2.json` is an append-only file-version-2 delta that extends
the exact file-version-1 predecessor. `bundle_index_v2_2.json` pins both source
policy versions and selects version 2 as the current head. The loader
materializes the contiguous policy chain; callers that load the old index still
receive the original eight-policy bundle.

Each new policy records only an official documentation URL, UTC retrieval time,
whole-document byte digest, recorder identity, default-deny conclusion, and
known limitations. No terms page body is committed. Since there is no formal
rights officer, every new policy remains `needs_verification`, and high-risk
reuse is denied or review-required. Metadata discovery is the only automatic
online-relevant operation described by policy; operational live acquisition is
still disabled.

## Core Symptom Catalog v2

The shared catalog is owned by `continucare-shared-terminology`, not a pathway,
and is not imported by UI/runtime/pathway modules.

| Benchmark | v2 status | Existing/candidate reference |
|---|---|---|
| nausea | reused concept | `nausea` |
| vomiting | reused concept | `vomiting` |
| diarrhea | reused concept | `diarrhea` |
| abdominal-pain | reused concept | `abdominal-pain` |
| constipation | reused concept | `constipation` |
| bloating | alias candidate only | `abdominal-distension` |
| decreased-appetite | reused concept | `decreased-appetite` |
| fatigue | reused concept | `fatigue` |
| dizziness | reused concept | `dizziness` |
| dyspnea | reused concept | `dyspnea` |
| chest-pain | internal candidate | no external code/reference |
| rash | alias candidate only | `skin-eruption` |

All mappings remain inherited-unverified or pending-unverified. No new SNOMED,
ICD-11, LOINC, or MedDRA code is claimed. The schema carries semantic
non-equivalence boundaries and rejects clinical fields such as triage,
severity, risk, diagnosis, treatment, recommendation, or clinical rule.

## Synthetic review fixture

The executable test fixture uses a synthetic machine author and a distinct
synthetic clinical reviewer with different identity and principal IDs. It calls
the existing `ReviewPacketBuilder`, `ReviewEventService`,
`InMemoryReviewerDirectory`, and `ReviewLedgerDecisionProvider`. The event has
an ephemeral verifier attestation and cannot count toward release. Same identity,
same principal through another account, forged formal status, incomplete gate,
and synthetic-lineage laundering all fail closed. No demo review helper or
parallel approval system exists.

## Running validation

Default tests must run with `CONTINUCARE_KNOWLEDGE_LIVE_VALIDATION` unset. A
scoped test guard rejects `socket.socket`, `socket.create_connection`,
`SSLContext.wrap_socket`, and any SQLite path outside that test's temporary
root. The cold v1 Knowledge import chain has a pre-existing urllib3 local IPv6
capability probe; the isolated subprocess preloads that third-party module,
then proves that incremental Knowledge Ops imports, bundle/read-model loading,
factories, and the default validator report make zero socket and zero SQLite
calls. No v1 import code was changed to conceal this limitation.

Only after all offline gates pass may the live validator be invoked in a
separate process:

```bash
CONTINUCARE_EXTERNAL_EGRESS_ENABLED=true \
CONTINUCARE_KNOWLEDGE_LIVE_VALIDATION=true \
python -m continucare.knowledge.ops.source_connectors.live_validation
```

The command accepts no URLs, queries, output paths, credentials, or patient
data. It makes at most five fixed metadata requests (one per modeled endpoint),
uses an automatically removed temporary directory, prints one body-free JSON
report, and never changes the parent environment.

## Remaining production blockers

- No formal rights officer decision exists.
- No formal clinical reviewer or pharmacist approval exists.
- Synthetic approvals cannot establish production eligibility.
- EMA and MedlinePlus public datasets may exceed the P1 hard byte cap.
- The legacy PMC OA metadata contract may retire or change.
- Official endpoint schemas and terms can change and therefore require ongoing
  ChangeSet/Gap handling and human review.
- No production Claim, Binding, patient content, KnowledgeRelease, pathway,
  clinical rule, state-machine change, or runtime authority is created here.
