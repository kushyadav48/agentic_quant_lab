# Phase 23 — end-to-end cloud research integration

Implementation date: 13 October 2026. Baseline verified: branch `master`, HEAD
`a62135e1ec48d667c83a4a611e51ba63f3d3488e`, clean working tree. No commit or push.

**Focused final acceptance audit: PASS.** The implementation and offline regression
coverage are present. The user supplies a successful real Gemini smoke on
`gemini-3.1-flash-lite`; that evidence supersedes the initial disabled preflight.
Three adapter defects found during final audit were reproduced failing-first
and corrected. Full browser/provider strategy-research workflow verification
remains a separate manual check; Phase 25 V1.0 release acceptance is not implied.
See [the final audit](phase23-acceptance-audit.md) and
[configuration instructions](cloud-llm.md).

## A. Implemented increment

The official Gemini cloud adapter implements the existing Phase 13 async provider
protocol. It feeds the unchanged Phase 14 strict natural-language interpretation,
evidence and strategy-vocabulary contracts, creating only an unapproved proposal
or clarification questions. Seven authenticated API routes add bounded explicit
AI jobs and an approved exact-proposal backtest handoff. The React Research panel
uses generated contracts and retains explicit consent/submission/execution/review.
The existing quant services, approvals, risk, financial owners, journal and all
nine ordinary research kinds are reused. No Phase 24 implementation is included.

## B. Exact changed-file inventory

Modified:

- `.env.example`
- `README.md`
- `docs/architecture.md`
- `docs/phase21-report.md`
- `docs/roadmap.md`
- `frontend/README.md`
- `frontend/contracts/openapi.json`
- `frontend/src/api/client.ts`
- `frontend/src/api/generated.ts`
- `frontend/src/pages/Research.tsx`
- `frontend/src/test/fixtures.json`
- `pyproject.toml`
- `src/quantlab/api/app.py`
- `src/quantlab/api/config.py`
- `src/quantlab/api/errors.py`
- `src/quantlab/api/routes.py`
- `src/quantlab/api/schemas.py`
- `src/quantlab/llm/client.py`
- `tests/api/test_openapi.py`

Added:

- `benchmarks/phase23_integration.py`
- `docs/cloud-llm.md`
- `docs/phase23-report.md`
- `docs/phase23-acceptance-audit.md`
- `frontend/src/pages/AIResearch.tsx`
- `frontend/src/test/ai.test.tsx`
- `src/quantlab/api/ai.py`
- `src/quantlab/llm/cloud.py`
- `src/quantlab/llm/gemini.py`
- `src/quantlab/llm/smoke.py`
- `tests/api/test_ai.py`
- `tests/llm/test_gemini.py`
- `tests/llm/test_gemini_sdk.py`
- `tests/llm/test_phase23_acceptance.py`

33 reviewable changed/added files after the final audit. Ignored `tmp/` contains test logs/XML,
benchmark/advisory JSON, portable-runtime support and downloaded verification tools;
these are not staged or proposed repository changes. The generated fixture change
replaces the connected API's obsolete unsupported inference label with
`cloud_multimodal`; the isolated Phase 22 demo fixture remains unchanged.

## C. Provider architecture

`GeminiProvider` uses `google-genai>=2.30,<3`; SDK 2.30.0 was tested. The optional
`cloud` extra enables runtime inference; the `dev` extra supplies the SDK for
offline SDK-wire tests. Importing/starting offline services does not require a
provider connection or key. Async countTokens plus generateContent use a whole
deadline, one candidate, JSON Schema and disabled automatic function calling.
SDK retries are set to one attempt; the application performs no cloud retry.
The existing neutral request/response/invocation artifacts preserve provider/model,
schema/prompt provenance and token usage. Thinking usage counts toward output limits.
Future providers can use `LLMProvider` without changing interpretation or quant code.

Strict complete STOP output, JSON/Pydantic validation and deterministic strategy
conversion are mandatory. Size, nesting and sparse Decimal preflight runs before
domain construction. Provider errors are normalized from status/structured detail
objects; provider exception messages never enter HTTP, logs or artifacts. Cloud
codes are reconstructed by the neutral client while its existing redaction remains
intact. No silent provider/model switching occurs.

Official compatibility sources: [SDK documentation](https://googleapis.github.io/python-genai/),
[structured output](https://ai.google.dev/gemini-api/docs/structured-output),
[pricing](https://ai.google.dev/gemini-api/docs/pricing),
[billing](https://ai.google.dev/gemini-api/docs/billing), and
[terms](https://ai.google.dev/gemini-api/terms). The current SDK still supports
generateContent; no assumption about permanent free-tier model availability is made.

## D–E. Functional end-to-end path and frontend/API boundaries

Authenticated operator-selected text → explicit queued AI job → explicit async
Gemini generation → strict interpretation → typed DRAFT → trusted human review and
existing exact-version approval → explicit AI-bound research submission with actual
caller-selected local bars/features/configuration → existing deterministic backtest
and risk → existing status/result digest → optional explicit trusted journal ingest
→ existing dashboard results/journal pages.

The integrated offline test constructs the proposal through the actual adapter,
rejects an unapproved strategy and a changed version, independently approves it
through the existing test review helper, executes two causal fills, ingests the
actual completed operation through Phase 20, and checks journal provenance through
authenticated queries. A second run applies the existing quantity risk limit and
records rejection with no fills. No dataset, approval or result is invented by the
LLM. Deterministic fixtures are marked synthetic and are used only for tests/demo.

Readers may inspect provider configuration, AI status and completed results;
operator-only actions remain enforced by FastAPI. Stable request identities,
content conflicts, terminal replay, 32-job capacity and queued-only cancellation
are explicit. Disconnect does not cancel running inference. The async cloud lane
does not block ASGI on network I/O or occupy the quant/SQLite worker. A test runs a
deterministic operation while cloud inference is pending. No background execution
queue, automatic polling, HTTP approval or durable AI job store is added.

The dashboard selects text, requests explicit external-transmission consent, checks
safe cloud configuration, separates Submit from Execute, shows safe failure states,
verifies the canonical artifact digest, and allows explicit job binding to an
independently approved backtest request. UI tests cover ready/disabled/missing
credentials/unverified provider/budget, pending/failed/completed, quota/rate-limit,
validation, digest integrity and reader/demo isolation. The demo adapter rejects
inherited cloud methods before fetch. See [route contracts](cloud-llm.md#http-routes-and-contracts).

## F–H. Authority, security, privacy, costs and offline behavior

Credentials stay in server environment `SecretStr`, excluded from serialization
and repr. No frontend provider calls or key controls exist. Selected text alone
is transmitted; identity/version/input reference stay application metadata. Local
datasets, private archives, filesystem contents, journals and account state are
never automatically included. Explicit consent does not imply that sensitive text
is suitable for Google's unpaid service; users must select non-sensitive input.
Configured key occurrences in input/output are rejected. Logs retain fixed metadata.

The LLM has no approvals, paper mutation, trading, capital, eligibility, causal
timing, recovery, journal-writing or financial-calculation capability. All Phase
18–20 authorities remain unchanged. Prompt/output injection cannot invoke tools,
read files, obtain owners or escape the bounded typed proposal contract.

Cloud defaults disabled; provider/model allowlists and a current (at most seven-day)
UTC operator verification of free eligibility/disabled billing/structured-text
capability are mandatory. No hardcoded list asserts that a model is free. Budget
defaults: 20 reserved count+generate wire requests, one concurrency slot, 32,768
input tokens including schema/framing allowance, 4,096 output tokens including
thinking, 49,152 prompt bytes, 65,536 output bytes, 20-second invocation deadline.
Reservations include failed requests and are not refunded. Defaults permit at
most ten interpretations per process; budgets reset on restart and do not track
other clients. No paid mode, tools, billing setup or fallback is enabled.

Operator attestations cannot independently verify provider billing or prevent
later account changes. **Local controls cannot guarantee zero external charges if
the account has billing enabled.** Unpaid provider terms allow product/model
improvement and human review; regional terms may differ. No zero-retention claim
is made. The operator must verify current eligibility, privacy and account status.

Missing/invalid credentials, unsupported/unverified models/providers, DNS/transport
failures, timeouts, 429/quota errors, server errors, bad JSON/schema, truncation,
oversized/sparse output and missing usage fail safely. Terminal errors never retry
or become fabricated success. Ordinary local research, reports/history and Demo
remain available with cloud disabled or failed. Journal ingestion remains an
explicit trusted operation, with no cross-store atomicity or HTTP write route.

## I. Implementation verification results (before final acceptance audit)

Verification used the repository's portable Python 3.11.9 runtime because both
normal virtual environments reference unavailable host Python installations.
Dependencies were installed only into ignored `tmp/` support paths. Pydantic
2.13.5, FastAPI 0.143.0, SDK 2.30.0; frontend tooling reports Vite 7.3.7,
Vitest 4.1.11 and openapi-typescript 7.13.0. No live API access is needed for pytest. The original full-suite/build/security
results below are retained historical evidence; the final audit ran 56 targeted
tests in 12.62 seconds, not another full suite or live inference.

| Check | Exact outcome |
|---|---|
| AI/service/API/orchestration/MCP/journal targeted run | 1,512 passed in 228.19 s; no failures/skips/xfails |
| Final AI/interpretation/API focused run after SDK corrections | 603 passed in 37.93 s |
| Prior full Python regression after implementation stabilization | 4,128 passed in 840.10 s (14:00); 0 failures/errors/skips/xfails |
| Prior frontend tests | 6 files, 97 tests passed in 18.25 s |
| Production build | TypeScript check passed; Vite build passed, 51 modules, 2.35 s |
| Generated contracts | TypeScript matches export; Python OpenAPI matches export exactly (21 routes, 237 definitions) |
| `pip check` | No broken requirements found |
| npm advisory audit | 0 vulnerabilities across 212 dependency entries |
| PyPI exact-version advisory metadata | 67 resolved app/dev packages checked, 0 query errors, 0 vulnerable packages |
| Whitespace | `git diff --check` passed after documentation updates |

Commands run from the repository root (frontend commands from `frontend/`):

```powershell
tmp/phase13-python311/python.exe -m pytest tests/llm tests/interpretation tests/orchestration tests/mcp tests/api tests/journal -q --junitxml=tmp/phase23-targeted.xml
tmp/phase13-python311/python.exe -m pytest tests/llm tests/interpretation tests/api/test_ai.py -q
tmp/phase13-python311/python.exe -m pytest -q --junitxml=tmp/phase23-full.xml
tmp/phase13-python311/python.exe frontend/scripts/export_contracts.py
npm.cmd test
npm.cmd run build
npm.cmd run contracts:check
npm.cmd audit --json --cache ../tmp/phase23-npm-cache
tmp/phase13-python311/python.exe -m pip check
git diff --check
```

The Python advisory check queried PyPI's exact-version vulnerability metadata with
bounded parallelism; its JSON evidence is `tmp/phase23-python-audit.json`.
`pip-audit` could not launch because the portable distribution lacks stdlib `venv`,
so no successful pip-audit run is claimed. The direct advisory check completed for
every resolved dependency. Advisory databases and unconstrained future dependency
resolutions can change; these checks are point-in-time evidence, not guarantees.

Failing-first regressions found and corrected the broad typed-error redaction
regression, Developer API countTokens system-instruction incompatibility, nested
SDK quota details, sparse Decimal output, HTTP 400 API_KEY_INVALID classification,
and the smoke's empty-system count message. The incomplete first full-suite process
was stopped for the final two SDK corrections; only the stabilized complete run is
reported as full-suite evidence. Initial sandbox denial of esbuild configuration
access was resolved by authorized unsandboxed build/test verification. There are
no remaining unexplained verification failures.

## J. Real Gemini smoke — PASS, user-supplied evidence

The user provided the following PASS fields during the final acceptance audit:

```text
provider=gemini
model=gemini-3.1-flash-lite
schema_version=1
input_tokens=23
output_tokens=25
requests_reserved=2
elapsed_ms=4543.863
financial_mutations=0
approval=false
```

This is accepted as evidence of successful real hosted inference. The audit did
not rerun live inference or independently observe the original run. The earlier
`PENDING / cloud_disabled` output was an initial preflight and is superseded.
The model ID is recorded as observed evidence, not hardcoded as permanently free.
Current account/model verification and billing restrictions still apply.

The smoke establishes bounded connectivity, schema-v1 acknowledgement validation
and provider/model/usage evidence without approval or financial mutation. It does
not test the full Phase 14 strategy schema, authenticated AI jobs, browser controls,
trusted approval, local feature/data selection, backtest or journal workflow
against the real provider. Those boundaries have offline integration coverage;
one full browser/provider research walkthrough remains manual verification.

## K. Local performance evidence

`benchmarks/phase23_integration.py` ran ten iterations of six synthetic bars through
authenticated TestClient, the scripted SDK-shaped adapter, exact trusted review,
real backtest and local SQLite journal. Measurements include TestClient dispatch
and local validation; the provider double has no network/inference delay.

| Stage | Median ms | Maximum ms |
|---|---:|---:|
| Input/identity admission | 2.498 | 79.470 |
| Mock cloud call + interpretation/validation | 61.304 | 72.692 |
| Result revalidation/canonical serialization | 50.375 | 67.823 |
| Bound research submission/validation | 53.693 | 137.955 |
| Existing deterministic backtest | 4.758 | 7.783 |
| Existing journal ingestion/persistence | 9.925 | 13.766 |

Evidence: `tmp/phase23-benchmark.json`. The later user-supplied smoke reports 4,543.863 ms aggregate elapsed time across
its invocation. Network and provider inference latency were not measured
separately and cannot be separated from that aggregate evidence. The mock stage
is aggregate local processing, not cloud speed. These small synthetic measurements
do not establish production throughput; they demonstrate bounded local integration.

## L–M. Limits, acceptance gaps and Git readiness

Implemented and tested: adapter/strict proposals; free-only admission and budget
controls; secure authenticated jobs; independent explicit approval/bound research;
frontend contracts/status/results; offline reliability; journal compatibility;
preserved deterministic financial authority and demo isolation.

Remaining manual verification: a full browser/provider strategy-research
walkthrough, distinct from the passed smoke. The focused Phase 23 audit has no
unresolved blocking defect; Phase 25 release acceptance remains separate. Unsupported: paid inference, other hosted adapters, cloud
multimodal uploads, autonomous approval/financial actions, HTTP journal writes,
durable AI jobs, deployment/Phase 24, production multi-user security and remote
market-data inference. Semantic review, truthful billing verification and selecting
privacy-appropriate input remain operator responsibilities.

HEAD/branch are unchanged; all work remains unstaged and uncommitted. Implementation
is ready for a user-controlled commit after the focused final acceptance audit.
No commit or push was made. The audit made no provider request, billing change,
approval or financial mutation; the user-supplied real smoke is recorded above.
