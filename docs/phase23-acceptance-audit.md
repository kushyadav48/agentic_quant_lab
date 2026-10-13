# Phase 23 — focused final acceptance audit

Audit date: 13 October 2026. Repository baseline/HEAD:
`a62135e1ec48d667c83a4a611e51ba63f3d3488e`, branch `master`.
Phase 23 remains uncommitted. **Verdict: PASS for the bounded focused audit**, after
three confirmed adapter defects were reproduced failing-first and fixed. No
unresolved blocking defect was found. Full browser/provider strategy-research
workflow verification remains a separate manual check; this is not Phase 25
release acceptance.

## Scope and inspected boundaries

The audit inspected the current changed/untracked Phase 23 files, existing strict
interpretation and client contracts, authenticated API job/research routes, trusted
approval handoff, service ownership, frontend client/state and Demo adapters,
generated OpenAPI, configuration, existing test evidence and documentation. It did
not repeat repository-wide engine analysis, implement Phase 24, commit, push or
rerun live inference. The original full Python/front-end/build/security evidence
was retained, not represented as freshly rerun.

| Area | Finding |
|---|---|
| Credentials/logs | Server-only `SecretStr`; configuration serialization excludes the key; API errors/logs use fixed metadata. Decoded-secret output bypass corrected. |
| Free-only admission/cost | Explicit enablement, provider/model allowlists and expiring UTC operator attestation remain mandatory. Two conservative reservations per invocation, failures not refunded, one slot, no paid fallback. |
| Gemini response/provenance | Complete STOP, size/nesting bounds, strict JSON/Pydantic/evidence/vocabulary checks and existing configured identity/prompt/usage provenance remain. Floating-token precision loss corrected. |
| SDK retry boundary | Explicit HTTPX async transport now prevents optional aiohttp from selecting an implicit connection retry and raw-error logging path. Both SDK attempt count and HTTPX connection retries are bounded to one/zero respectively. |
| Authenticated jobs/idempotency | Reader/operator authorization, stable content/key identities, 409 conflicts, terminal replay, 32-job bound and queued-only cancellation remain intact. |
| Deterministic handoff | Exact original proposal ID/version/content digest and independently approved specification required; backtest remains an explicit separate operation. |
| Financial isolation | No LLM approval, paper/account/portfolio mutation, risk/eligibility override, recovery authority or journal history modification introduced. |
| Frontend/Demo | Generated authenticated routes; provider keys absent from browser code; canonical result digest checks; live controls hidden in Demo and inherited cloud methods reject before fetch. No frontend code changed in this audit. |
| Existing/offline research | Nine research kinds still exposed by the actual schema. Targeted integration confirms deterministic operations with disabled cloud and while cloud is pending, plus existing risk/journal provenance. |
| Git artifacts | No credential-pattern/environment-key hits or unexpected databases, logs, datasets, caches, builds or secret files among the proposed changed files. OpenAPI/generated TypeScript and synthetic test fixtures are intentional artifacts. |

These controls do not automatically establish actual provider billing status.
Operator attestation and account/model verification remain necessary. Process
budgets reset on restart and do not account for other clients. Local limits cannot
guarantee zero external charges on a billed provider account. The audit's model
record is smoke evidence, not a permanent free-model allowlist entry.

## Exact defects and corrections

1. **JSON-escaped credential redaction bypass.** An otherwise valid model response
   placed the configured fixture key in `draft.description` using Unicode JSON
   escapes. The raw text contained no literal key, but interpretation decoded it
   into a successful proposal/artifact. Failing-first test observed no rejection.
   Fix: scan decoded output strings for the protected key before typed conversion.
   API regression now confirms terminal `invalid_model_response`, no retained
   result, HTTP 409 on result access, and no key in public output or captured logs.

2. **Silent binary floating-point precision loss.** A numeric JSON threshold
   `1.12345678901234567890123456789` was admitted as Decimal
   `1.1234567890123457`. Failing-first test demonstrated admission; a separate
   deterministic reproduction recorded the changed scalar. Fix: reject floating
   JSON number tokens before Pydantic conversion; exact Decimal values use strings,
   while integer JSON tokens remain supported. Positive regression verifies the
   same threshold string retains every digit. This changes only cloud output
   admission, not existing strategy identities or financial engine arithmetic.

3. **Optional SDK transport could bypass the no-retry policy.** SDK 2.30.0 selects
   aiohttp automatically when that optional package is present and no explicit
   HTTPX transport is supplied. Its connection-error path sleeps/retries once and
   logs the raw connection exception independently of `HttpRetryOptions(attempts=1)`.
   The real SDK transport-selection rule reproduced the policy violation with
   optional availability simulated; aiohttp was absent from the audited runtime.
   Fix: explicitly own an HTTPX async client using `AsyncHTTPTransport(retries=0)`
   and pass it through the supported SDK `HttpOptions.httpx_async_client`. Keep SDK
   attempts at one and the existing whole deadline. Both async transport and unused
   synchronous SDK client are closed. The SDK-wire smoke test's mock was adjusted
   for the explicit client. No additional runtime dependency was introduced.

The failing-first run recorded **3 failed, 1 passed** before corrections. A
partially constructed SDK object in that first test also produced a test-only
destructor warning; the selection check now calls the real method against a plain
namespace, avoiding SDK resource/destructor construction. It is not a production
defect or a remaining warning.

Documentation also incorrectly retained `PENDING` live-smoke status and one
obsolete statement that real adapters were planned. Current status statements were
corrected using the supplied evidence, with historical test results retained and
full browser verification explicitly distinguished.

## Tests and checks performed

```powershell
tmp/phase13-python311/python.exe -m pytest tests/llm/test_phase23_acceptance.py tests/llm/test_gemini.py tests/llm/test_gemini_sdk.py tests/api/test_ai.py -q --junitxml=tmp/phase23-acceptance.xml
git diff --check
```

Final targeted result: **56 passed in 12.62 seconds**, zero failures/errors/skips/
xfails. Provider calls used deterministic doubles or HTTPX MockTransport. The
tests cover the corrected output/transport boundaries and their downstream AI job,
idempotency, authorization, approval, risk, backtest, journal and offline behavior.
The frontend, API contracts and financial engines were not changed; their full
suites/builds were not rerun. No full Python rerun was necessary for these bounded
adapter changes. Final `git diff --check` passed.

Read-only artifact checks verify actual Python OpenAPI exactly equals the exported
schema and still exposes nine operation kinds. Changed-file scans check Google/
hosted/AWS key patterns, private-key blocks and actual configured credential values
without printing any match value. No unexpected generated/private artifacts were
found. Evidence is in ignored `tmp/phase23-acceptance-tests.log`,
`tmp/phase23-acceptance.xml` and `tmp/phase23-acceptance-scan.json`.

Prior evidence retained: **4,128 Python tests passed** (existing JUnit verifies zero
errors/failures/skips), **97 frontend tests passed**, production build/contract
checks passed, dependency consistency and advisory checks passed. These are the
pre-audit verification results, not a claim that 4,128 tests ran again after the
adapter corrections. Advisory checks remain point-in-time evidence.

## Live smoke evidence and remaining manual verification

Accepted user-supplied **PASS** evidence:

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

The user supplied these fields; the auditor did not run or independently observe
the live invocation. It establishes real hosted connectivity and the small smoke
schema. It does not verify the larger Phase 14 strategy schema or complete
authenticated browser research path. The code changes have post-fix offline SDK
smoke/serialization coverage; no new live smoke was requested or run.

Remaining manual verification: with current free-only account/model verification,
perform one explicitly consented, non-sensitive strategy request in the connected
Research dashboard; inspect the draft/clarifications and provenance; review and
approve through the existing trusted Python workflow; supply actual selected local
bars/features/config/risk; explicitly submit and execute the bound backtest;
inspect verified results and, if explicitly imported by the trusted journal owner,
journal history. Check reader controls and switching to the read-only Demo. Do not
infer success from the connectivity smoke alone. This walkthrough is outside this
audit's prohibition on further live inference.

## Files changed by this audit and commit readiness

Modified during this audit:

- `src/quantlab/llm/gemini.py`
- `tests/llm/test_gemini_sdk.py`
- `README.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/cloud-llm.md`
- `docs/phase23-report.md`

Added during this audit:

- `tests/llm/test_phase23_acceptance.py`
- `docs/phase23-acceptance-audit.md`

Nine audit files; the entire uncommitted Phase 23 increment now contains **33**
changed/added files, inventoried in [phase23-report.md](phase23-report.md).
HEAD remains the baseline, branch `master`; no staged changes, commit or push.
**Ready for a user-controlled commit** within the documented bounded scope.
The manual browser/provider walkthrough and Phase 25 release review remain separate.
