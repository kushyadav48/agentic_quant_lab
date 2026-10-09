# Phase 21 — FastAPI application and backend

Implementation date: 9 October 2026. Baseline: `master` at
`1dcbb3ace23cf2d20280480755c46d25a88bfdfc`, initially clean. No commit or push.

The subsequent bounded security/integration audit and its corrections are recorded
in [section L](#l-focused-security-and-integration-audit). Earlier full-suite and
benchmark observations below describe the implementation before this audit.

## A. Implementation summary

`quantlab.api` exposes a bounded local HTTP backend for existing deterministic
research and reporting services. It implements an application factory, explicit
lifespan, injected services, strict JSON schemas, reader/operator authentication,
structured correlations, explicit operation lifecycle routes, and read-only
paper/portfolio/journal reports. Phase 22 can consume this contract without adding
financial logic to a dashboard. Phases 1–20 quantitative owners are unchanged.

FastAPI and Uvicorn are added to runtime dependencies; HTTPX is declared in the
development extra for real ASGI tests. No PostgreSQL, migrations, worker queue,
provider invocation, dashboard or brokerage integration is introduced.

## B. Exact files changed

Modified:

- `.env.example`
- `README.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `pyproject.toml`
- `tests/orchestration/test_architecture.py` (declared dependency inventory only)

Added:

- `src/quantlab/api/__init__.py`
- `src/quantlab/api/__main__.py`
- `src/quantlab/api/app.py`
- `src/quantlab/api/config.py`
- `src/quantlab/api/dependencies.py`
- `src/quantlab/api/errors.py`
- `src/quantlab/api/routes.py`
- `src/quantlab/api/schemas.py`
- `src/quantlab/api/security.py`
- `src/quantlab/api/services.py`
- `tests/api/__init__.py`
- `tests/api/helpers.py`
- `tests/api/test_lifecycle.py`
- `tests/api/test_openapi.py`
- `tests/api/test_operations.py`
- `tests/api/test_reporting.py`
- `tests/api/test_security.py`
- `benchmarks/phase21_api.py`
- `docs/phase21-report.md`

The ignored virtual environment received FastAPI 0.143.0 and annotated-doc 0.0.5.
An ignored `tmp/update_phase21_docs.py` helper updated existing status statements;
it is not a deliverable or Git change. No existing domain engine or storage owner
was edited. The existing orchestration architecture test's dependency inventory
now includes the two authorized HTTP runtime dependencies; its isolation checks
are unchanged. There are 25 changed/added deliverable files.

## C. Route and schema inventory

All domain routes use `/api/v1`. Every route except health requires a bearer
credential. Operators also have reader permissions. Mutation here refers only
to process-local research operation metadata and deterministic research execution.

| Method | Route | Authority | Input → output |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | Public | None → `Health` |
| GET | `/api/v1/ready` | Reader | None → `Health`, or 503 |
| GET | `/api/v1/capabilities` | Reader | None → `Capabilities` |
| POST | `/api/v1/strategies/validate` | Reader | `StrategyValidationRequest` → `StrategyValidationResult` |
| POST | `/api/v1/research/operations` | Operator | `OperationSubmission` → `OperationStatus`, 201 |
| GET | `/api/v1/research/operations/{operation_id}` | Reader | 64-character digest → `OperationStatus` |
| POST | `/api/v1/research/operations/{operation_id}/execute` | Operator | Digest → `OperationStatus` |
| POST | `/api/v1/research/operations/{operation_id}/cancel` | Operator | Digest → `OperationStatus` |
| GET | `/api/v1/research/operations/{operation_id}/result` | Reader | Digest → discriminated `ResearchResult` |
| GET | `/api/v1/paper/sessions/{session_id}/snapshot` | Reader | Identity → `PaperReport` |
| GET | `/api/v1/portfolios/{portfolio_id}/snapshot` | Reader | Identity → existing `PortfolioSnapshot` |
| POST | `/api/v1/journal/trades/query` | Reader | `JournalQuery` → existing `TradeHistoryPage` |
| POST | `/api/v1/journal/research/query` | Reader | `JournalQuery` → existing `ResearchHistoryPage` |
| GET | `/api/v1/journal/sessions/{session_id}/summary` | Reader | Identity → existing `SessionSummary` |
| GET | `/openapi.json` | Reader | Authenticated OpenAPI 3.1 document |

The OpenAPI document has 14 domain paths; its own route is excluded from the
schema. Interactive `/docs` and `/redoc` are disabled. HTTP bearer security,
request schemas, response schemas, discriminators and typed error responses
are documented, including strict JSON-mode input decoding. Core end-anchor
patterns are converted to ECMA-compatible `$` in transport schemas only.

`OperationSubmission.operation.kind` supports the nine existing kinds:

| Kind | Existing application service | Result envelope |
| --- | --- | --- |
| `backtest` | `mcp.tools.run_backtest` | `BacktestResultResponse` |
| `holdout` | `mcp.tools.run_holdout` | `HoldoutResultResponse` |
| `walk_forward` | `mcp.tools.run_walk_forward` | `WalkForwardResultResponse` |
| `robustness` | `mcp.tools.run_parameter_robustness` | `RobustnessResultResponse` |
| `ml_dataset` | `mcp.tools.build_ml_dataset` | `DatasetResultResponse` |
| `ml_training` | `mcp.tools.train_ml_model` | `TrainingResultResponse` |
| `ml_prediction` | `mcp.tools.predict_ml_oos` | `PredictionResultResponse` |
| `ml_prediction_features` | `mcp.tools.ml_predictions_to_features` | `PredictionFeaturesResultResponse` |
| `performance` | `mcp.tools.analyze_performance` | `PerformanceResultResponse` |

Each result envelope carries the operation identity, kind, result digest, original
canonical `result_json` and a typed parsed `result` using the existing adapter
model. SHA-256 binds the exact UTF-8 canonical text, not a client's reserialization.
Returning both forms costs additional serialization but enables typed consumption
and lossless digest verification. Status omits retained result text and carries
state, failure classification, strategy/workflow bindings and the existing audit.

`PaperReport` wraps the unchanged `SessionSnapshot` and reports the existing
`operator_required` recovery gate. HTTP cannot clear it. Portfolio snapshots and
journal summaries retain existing economics, valuation status and provenance.
Journal query routes use POST to carry typed filters/cursors without putting
private identities in URL query strings. They are read-only.

## D. Service integration architecture

```text
FastAPI router / authentication / strict boundary schemas
                │
       injected ApplicationServices
                │
     one dedicated bounded service thread
       ├─ ResearchOperations → existing Phase 17 adapters → core services
       ├─ registered PaperSession / DurablePaperSession public snapshot
       ├─ registered Portfolio public snapshot
       └─ existing SQLiteJournal query / summary methods
```

`create_app(settings=None, services_factory=None)` performs no owner construction
or I/O until lifespan startup. Configuration defaults to explicit environment
loading; a programmatic `APISettings` can be supplied and is revalidated at the
factory boundary, including instances made with unchecked copy/update APIs.
`services_factory` is a zero-argument callable invoked on the service thread. It returns
`ApplicationServices(operations=..., paper_sessions=..., portfolios=...,
journal=..., close_owners=...)`.

Construct/open injected owners on that thread and hand them to the application
exclusively. Existing paper/portfolio owners require serialized access and the
journal uses a single-thread SQLite handle. External writer threads must not share
these owners. The factory is responsible for closing partially constructed
resources if it raises before returning the bundle. `close_owners` releases
registered paper stores or other owned resources; the bundle closes its journal
in a `finally` block even if that callback fails.

Default services create only a fresh `ResearchOperations` namespace and an optional
existing Phase 20 journal. They create no paper account, portfolio, dataset store
or external feed. Reporting owners are populated by trusted application code
using existing construction/recovery contracts. `/capabilities` reports configured
identities and absent services. No automatic journal capture/import is performed.

Strict request decoding uses each model's original `model_validate_json(...,
strict=True)` on raw bytes. This retains valid enum/tuple/Decimal/timestamp wire
forms while keeping strict integer/boolean and domain validation. It avoids
FastAPI's Python-mode strict conversion mismatch. No HTTP request invokes MCP
transport, dynamically imports code, supplies a callable or chooses a database.

## E. Security and authority boundaries

The API trusts one local operator namespace. A separate optional reader credential
permits reporting and deterministic strategy validation. Both roles see all data
in that namespace; there is no per-user tenant isolation. Operator credentials can
submit existing research specifications carrying approval records, preserving
Phase 17's trusted-operator model. Approval identity/version/digest integrity is
checked by existing contracts; cryptographic approver authentication is not added.

HTTP exposes no approval transition, paper admission, financial command, market
delivery, operator recovery, portfolio mutation or journal write. A draft strategy
cannot execute successfully; incompatible approvals are rejected; causal errors
fail existing research services. Risk denial is recorded in a completed research
result with no unauthorized fill, rather than being represented as a failed job.

Controls:

- Mandatory operator secret: 32–256 non-whitespace ASCII characters. Optional
  reader secret must differ. No credential defaults or generated server secrets.
- Constant-time secret comparison; invalid credentials return 401 and readers
  attempting operator actions return 403 before decoding/admission/execution.
- Launcher uses one process on `127.0.0.1`, no reload/debug, no access logs, and
  no proxy-header trust. Trusted hosts are localhost and loopback IP forms.
- CORS is absent by default. Optional origins must be exact HTTP(S) loopback
  origins without paths, credentials or wildcards. No credentialed CORS cookies.
- Actual body bytes are limited even for chunked requests without Content-Length.
  Ambiguous/invalid lengths and compressed bodies are rejected. JSON requires
  `application/json`; duplicate keys, fractional numeric tokens, NaN/infinity and
  nesting beyond 64 levels are rejected. Decimal values should be JSON strings;
  integral JSON numbers remain exact. Input schemas reflect integer-only numbers.
  Numeric strings are checked for bounded Decimal coefficients/expansion before
  any domain validator or strategy digest can expand a sparse exponent. The guard
  also applies to numeric-looking opaque strings; 8,192 wire characters and
  4,096 expanded digits are the maximum accepted numeric-string bounds.
- At most 16 concurrent HTTP requests by default, configurable from 1 to 64.
  A service lane admits one operation at a time with no backlog; excess requests
  return 503. Request bytes default to 1 MiB and can only be reduced to ≥1 KiB.
- Existing research limits remain: 32 retained operations, 4 MiB retained result,
  5,000 bars/rows, bounded feature width, fold count and robustness candidates,
  plus existing canonical admission limits. Operation identities are never evicted.
- Reporting catalogues each contain at most 32 owners; paper snapshots require
  configurations bounded to 2,000 inputs. HTTP journal pages default to 10 and cap
  at 20 while retaining original filters and keyset cursors. Serialized reports
  cap at 12 MiB and Decimal expansions at 4,096 digits. Large reports return
  sanitized 503; smaller journal pages can be requested. Snapshot paging is deferred.
- Generated 32-character request IDs are returned as `X-Request-ID`; caller
  request IDs are not echoed. Responses use `Cache-Control: no-store` and nosniff.
- Error messages and validation issues use fixed text and schema-only field
  locations. Logs contain no body, raw input, credential, submitted URL/path or
  exception text. Operation outcome logs retain identities and digests.
- The launcher uses a safe Uvicorn formatter because Uvicorn can emit traceback
  strings as log messages. It retains server event level, suppressing raw details.

This is not production multi-user security. Local bearer credentials grant broad
namespace access. An embedding application must keep equivalent host/logging
configuration and exclusive ownership; the factory alone cannot enforce how an
external ASGI server binds. TLS, tenant ACLs, rotation/revocation APIs, distributed
rate limiting and protection against long-lived network clients remain deferred.
Private datasets have no unauthenticated query or download surface.

## Configuration and local startup

| Variable | Behavior |
| --- | --- |
| `QUANTLAB_API_OPERATOR_TOKEN` | Required; missing/invalid fails before startup |
| `QUANTLAB_API_READER_TOKEN` | Optional distinct read-only secret |
| `QUANTLAB_API_PORT` | Default 8000; accepted range 1024–65535 |
| `QUANTLAB_API_JOURNAL_PATH` | Optional trusted local Phase 20 path; opens/verifies existing journal or creates a new journal |
| `QUANTLAB_API_CORS_ORIGINS` | Optional comma-separated exact local origins |

`.env.example` is a template, not an implicit loader. Programmatic settings also
permit smaller request-byte and concurrent-request limits. API input never sets
these values. No provider or brokerage credential is needed.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
$env:QUANTLAB_API_OPERATOR_TOKEN = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.\.venv\Scripts\python.exe -m quantlab.api
```

From another shell with the same securely supplied credential:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
$apiHeaders = @{ Authorization = "Bearer $env:QUANTLAB_API_OPERATOR_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready -Headers $apiHeaders
Invoke-RestMethod http://127.0.0.1:8000/openapi.json -Headers $apiHeaders
```

Use the authenticated OpenAPI document to generate/inspect client contracts.
The schema contains no token or private owner contents. The default application
has no registered paper/portfolio reports and no journal unless configured.

## Error contract

Application route errors return `ErrorResponse {code, message, request_id, issues}`. Validation
issues use bounded schema-only locations and fixed messages. No raw exception,
body, unknown submitted key or sensitive filesystem path is returned.

| Status | Meaning |
| --- | --- |
| 400/405 | Host/HTTP rejection; middleware host/CORS rejections use Starlette's fixed diagnostics |
| 401 | Missing/invalid bearer credential; includes `WWW-Authenticate: Bearer` |
| 403 | Reader attempted an operator action |
| 404 | Unknown route/operation or unregistered reporting owner |
| 409 | Idempotency conflict, illegal execute/cancel state, or result unavailable |
| 413 | Actual/declared request body exceeds the configured byte limit |
| 415 | Non-JSON body on a typed body route, or any content encoding |
| 422 | Strict contract validation or existing journal query rejection |
| 500 | Unexpected service failure with a fixed sanitized response |
| 503 | Not ready, busy/capacity, absent journal, recovery required or output too large |

Strategy semantic validation returns its existing `valid=false` result with
sanitized issues at HTTP 200. Executing a research operation that receives a
known domain rejection returns HTTP 200 with `state=failed` and
`failure=domain_rejection`. Unexpected execution exceptions return HTTP 500;
the existing operation owner first records terminal `internal_failure`. Repeated
execute/status calls retain that failure and never retry quant execution.

## F. Job lifecycle semantics

Submission records canonical immutable intent and a `queued` snapshot. HTTP does
not schedule it. Exact idempotent retries return the same identity/current state
with 201; changed intent under the same key returns 409. Calling `/execute`
explicitly waits for the existing bounded service to finish on the service thread.

Legal transitions remain `queued → running → completed/failed`, or
`queued → cancelled`. Queued cancellation is idempotent. Running/terminal
operations cannot be cancelled. Completed/failed execution retries return the
unchanged terminal snapshot. Result access requires completion and preserves
original result bytes/digest. Metadata polling works while execution runs.

Disconnecting or cancelling the HTTP await does not stop quant execution or free
the service lane. Subsequent execution attempts receive busy while it drains.
Shutdown awaits pending real work before owner cleanup.
Startup adopts returned owners on the service thread before an interrupted await
can lose them. Shutdown retains one shielded cleanup task, drains work and closes
owners once even if its caller is repeatedly cancelled; cancellation propagates
after cleanup completes. There is no deadline,
hard-kill API or running-operation cancellation. Process termination can lose
operations; operation identities, idempotency keys and results are process-local
and do not survive restart. These are explicit research operations, not durable
or autonomously background jobs. Use one server process; multiple workers would
create unrelated namespaces and unsafe persistence ownership.

## G. Persistence and lifecycle behavior

No new database connection is opened per request. Optional journal construction,
existing verification, bounded query transactions and close all occur on one
service thread. The application does not import its research outcomes into the
journal automatically and does not change journal/paper persistence formats.

Configured/injected owner failures block startup or fail a request with a fixed
diagnostic. Readiness probes a bounded public journal read and checks registered
durable owners' existing recovery flags. An uncertain paper/journal commit fails
closed with 503. A recovered ACTIVE paper owner may still expose its readable
snapshot and `operator_required=true`; there is no HTTP command to resume it.
Operator intervention remains a trusted local workflow. The API does not acquire
an interprocess database/financial-owner lock; exclusive use is a deployment
precondition. Stores must not be shared with a parallel external writer.

## H. Verification

Pre-audit targeted run: **85 API tests plus 15 existing orchestration architecture
tests passed (100 total)** in 17.12 seconds, using FastAPI
TestClient and HTTPX AsyncClient with actual ASGI transport. Tests remain offline
and use real Phase 17–20 services/fixtures, including a recovered durable session
and an existing journal reopened from disk.

Coverage includes every implemented route's authentication, reader/operator
authority, strict schemas/JSON/size bounds, exact Decimal and UTC handling, all
nine operation kinds, immutable results and audit chains, idempotency/conflicts,
capacity, cancellation, approval/risk/causality rejection, journal filters/cursors,
paper/portfolio economics, startup/shutdown/failure cleanup, dependency isolation,
streaming/concurrent ingress, nonblocking status polling, disconnected execution
and draining shutdown, recovery restrictions, sanitized logs/errors, loopback
launcher and OpenAPI reference/discriminator/security/error contracts.

An initial full run produced 4,056 passes and one failure in the pre-existing
orchestration test that pinned the three pre-Phase-21 runtime dependencies. Only
that expected inventory was extended to include FastAPI/Uvicorn. The corrected
architecture test and all API tests then passed together: **92 passed** in 19.79
seconds. No domain behavior was changed to satisfy the assertion.

The finalized ingress guard adds eight cases covering exponent expansion,
coefficient length, whitespace, underscores, Unicode digits and representation
overflow before any strategy digest is called.

**Pre-audit stable full regression: 4,065 passed in 630.37 seconds (10:30). No
failures, skips or xfails.** Command: `.venv\Scripts\python.exe -m pytest -q`.
`pip check` reports no broken requirements. `git diff --check` and the additional
CRLF-aware whitespace check of every untracked deliverable pass. The earlier
dependency-inventory failure is fully resolved in this final run.

## I. Performance observations

`benchmarks/phase21_api.py` ran with Python 3.11.9 and FastAPI 0.143.0 using an
in-process ASGI transport. Fixture: four committed paper records, one enrolled
portfolio member and a four-bar approved backtest. Each reporting path had one
warmup and 30 sequential measurements; backtest admission/execution had 10.

| Operation | Median ms | p95 ms | Maximum ms |
| --- | ---: | ---: | ---: |
| Health | 0.305 | 0.400 | 0.417 |
| Status | 0.407 | 0.501 | 0.528 |
| Strategy validation | 1.107 | 1.577 | 2.262 |
| Paper snapshot | 20.262 | 30.601 | 85.402 |
| Portfolio snapshot | 1.583 | 2.365 | 2.369 |
| Journal page | 40.193 | 51.222 | 55.258 |
| Backtest submit + execute | 8.994 | 10.720 | 10.720 |

These final observations were gathered alongside targeted test verification,
rather than under isolated load. The snapshot and journal paths exercise existing validation and explicit history
materialization. Status never copies a retained result; reports avoid creating
new owners/connections or performing quant replays. Large quant work, input
validation and large serialization use the dedicated service thread. Concurrent
tests verify status/health progress while that thread is blocked. Python's GIL
still limits CPU parallelism; this is not a throughput, TCP, production-load or
maximum-fixture benchmark. Occasional snapshot latency variation is visible in
the measured maximum. Snapshot pagination and broader capacity profiling remain
deferred.

## J. Limitations and remaining risks

- Local shared namespace and bearer roles; no multi-user or internet deployment
  claim, cryptographic approval signatures, distributed workers or job durability.
- Explicit synchronous-to-completion research execution, no running cancellation,
  no hard deadline/CPU kill, and no automatic scheduling/capture.
- Existing owners require exclusive service-thread and process use. Factories
  must clean partial construction and supply paper-store cleanup callbacks.
- Whole paper snapshots are limited to owners configured for ≤2,000 inputs;
  large history-bearing reports can exceed the 12 MiB response cap. Journal query
  pages can be reduced; snapshot pagination remains deferred. No performance
  claim is made for maximum admitted research inputs or retained histories.
- Default startup does not recover/create paper sessions or reconstruct a
  portfolio; trusted code must inject existing configured owners.
- Missing journal session summaries are existing owner query rejections (422),
  while missing operation/catalogue identities are 404. These stable contracts
  intentionally do not parse exception text to invent finer domain errors.
- No HTTP strategy approval, paper/portfolio/journal writes, live trading,
  autonomous AI trading, provider/image interpretation, private dataset download,
  dashboard, unrelated engine refactor or Phase 23 overhaul.

## K. Git status and commit readiness

The original baseline remains at the supplied commit on `master`. Only the 25
listed Phase 21 deliverables are intended changes: six modified files and 19 new
files, all unstaged. No commit/push or deployment was performed. The final full
suite passed before the bounded audit; its post-correction targeted evidence is
recorded below. Dependency compatibility and whitespace checks pass; the patch is ready
for review and a user-controlled commit. Runtime owners are not left running
after verification.

Implementation references: FastAPI's official
[lifespan documentation](https://fastapi.tiangolo.com/advanced/events/) informed
the explicit startup/shutdown boundary; actual behavior is verified by the ASGI
tests above.

## L. Focused security and integration audit

Audit date: 9 October 2026. Scope: the 25 uncommitted Phase 21 deliverables and
necessary existing operation, approval/risk, journal-query and owner-lifecycle
contracts. No benchmark or full regression was repeated. No Phase 22 work,
financial-engine refactor, commit or push was performed.

### A. Verdict

**PASS after correction.** Three confirmed medium-severity defects were reproduced
and corrected. No unresolved material blocker was found within the documented
single-process local deployment and trusted-owner contract.

### B. Severity-ranked findings

| Rank | Severity | Confirmed defect | Correction |
| --- | --- | --- | --- |
| 1 | Medium | `create_app` accepted unchecked copied settings. Short/missing operator secrets, identical reader/operator credentials, remote CORS origins and invalid bounds could bypass startup validation. Identical credentials promoted the reader credential to operator authority. This required trusted programmatic misconfiguration, not HTTP control of settings. | Always revalidate supplied `APISettings`, including field bounds, required secrets and defaults. |
| 2 | Medium | Cancelling lifespan startup while the factory was running discarded its returned bundle before assignment. Shutdown drained the worker but skipped owner cleanup. | Validate and adopt the returned bundle inside the service-thread construction callback before its await can be cancelled. |
| 3 | Medium | Cancelling lifespan shutdown during pending work skipped owner cleanup; repeated cancellation could also abandon the pool-shutdown await. | Retain one shielded cleanup task, wait through repeated caller cancellation, then propagate cancellation. Repeated close reuses that task and does not close owners twice. |

No high/critical finding was confirmed. The settings correction protects the
composition boundary; it does not add a new remotely editable configuration API.

### C. Corrections and regression evidence

Audit edits are confined to `api/app.py`, `api/config.py`, `api/services.py`, the
existing lifecycle/security test files and this report. Other Phase 21 deliverables
and all underlying quantitative/persistence owners remain unchanged by the audit.

Before corrections, the startup-cancellation regression and seven unchecked-copy
cases produced **8 failures**. The shutdown regression then independently failed
because the owner-close callback had not run after cancellation. These tests
exercise real lifespan tasks, blocked worker execution, repeated cancellation,
cleanup thread identity and repeated close, rather than only mocking the lane.

After corrections:

- Lifecycle/security checks: **69 passed in 5.40 seconds**.
- Complete API plus relevant existing operation/architecture checks: **160 passed
  in 17.47 seconds**, comprising 94 API tests, 51 operation tests and 15
  orchestration architecture tests. No failures, skips or xfails.
- Command: `.venv\Scripts\python.exe -m pytest tests/api tests/mcp/test_operations.py tests/orchestration/test_architecture.py -q`.
- `pip check`, tracked `git diff --check` and CRLF-aware checks of every untracked
  deliverable pass.

The earlier **4,065-test full regression** remains pre-audit evidence. It was not
rerun: the corrections affect only API configuration and resource lifetime, which
the complete API set exercises; no shared domain behavior was changed. Nine
new regression cases bring the API count from 85 to 94. Benchmarks were not rerun.

### D. Authentication and execution authority

All 14 protected surfaces, including OpenAPI, enforce reader authentication;
submission, execution and cancellation additionally require operator authority.
Health alone is public. Missing/invalid credentials fail with 401, reader operator
attempts fail with 403, and interactive documentation stays disabled. Secrets,
unknown input keys, client request IDs and exception details are excluded from
HTTP diagnostics and application/server logs. Loopback launch, trusted hosts,
restricted CORS and bounded ingress match the stated deployment policy.

Research dispatch invokes existing approved-strategy services and mandatory risk
gates. Regressions cover draft/stale approval rejection, causality rejection and
risk denial without fills. The operator remains the existing trusted supplier of
research approval records; no signature verification is claimed. HTTP cannot
approve a strategy, issue paper/live orders, resume/recover a session, mutate a
portfolio or write journal records. Reporting delegates to public owner methods;
query values are bound SQL parameters and no HTTP input selects a file/database.

### E. Job concurrency and application lifecycle

Admission and identity/key ownership are atomic under the existing operation
lock. Identical accepted retries return the retained identity/state; conflicting
intent fails without mutation. The bounded service lane rejects overlapping work
with 503, while metadata reads and queued cancellation use the existing lock.
Terminal snapshots, canonical result bytes and digests remain immutable. Only
queued jobs can be cancelled; a disconnected HTTP await does not stop real work.

Quant execution, strict decoding, SQLite reads and large output serialization run
on one worker thread, keeping them off the ASGI event-loop thread. Python's GIL
still limits CPU parallelism. Startup/readiness failures and interrupted lifespans
now clean acquired owners; shutdown drains work, closes owners on their owning
thread, and joins the dedicated pool before completing. Factories still own
cleanup of partial construction before returning a valid bundle. Application
instances have separate namespaces and lifetimes. Exact Decimal serialization,
UTC handling, bounded pages/cursors and sanitized error codes pass integration
checks.

### F. Remaining risks and supported limitations

The earlier limitations in section J remain accurate: one local shared namespace,
one process and exclusive injected-owner access; no tenant isolation, cryptographic
approval authentication, job durability or worker coordination. Restart loses
process-local operations and idempotency history; existing journal/paper durability
does not make API jobs durable. Running work has no hard cancellation/deadline and
shutdown waits for it. Forced process termination cannot guarantee cleanup.
External ASGI hosting must preserve the launcher's host/logging policy. Slow-client
timeouts, internet-facing security and maximum-fixture performance guarantees
remain unsupported. Large snapshots may exceed the response bound; pagination
exists only for journal queries.

### G. Git status and commit readiness

`master` still points to `1dcbb3ace23cf2d20280480755c46d25a88bfdfc`. There are
**25 unstaged deliverables: six modified tracked files and 19 untracked files**;
the audit adds no new deliverable file. No core service was edited. With the three
findings corrected and the targeted/dependency/whitespace checks passing, Phase 21
is ready for a user-controlled commit within its documented bounded scope.
Nothing was staged, committed or pushed.
