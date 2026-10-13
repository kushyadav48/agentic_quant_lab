# Phase 24 — CI, deployment and release engineering

Verification date: 13 October 2026 (Asia/Calcutta). Starting branch `master`,
HEAD `e43227b94d11d0a5cd46a50767503e259130faaf`; working tree was clean.
No repository commit, push, tag, cloud inference, publication or external deployment
was performed. Phase 25 is not implemented.
Phase 24 implementation and requested local verification are complete;
GitHub-hosted execution and final release acceptance remain pending.

## A. Implementation and scope

Inspected the roadmap, actual Phase 21 API/security/lifespan and service composition,
Phase 22 relative frontend client/build/contracts, Phase 23 disabled/free-only cloud
configuration, and existing persistence owners. Selected one trusted Windows
workstation process serving production React assets through FastAPI on loopback.
Docker is explicitly deferred in the roadmap; no untested container is offered.
Phases 1–23 financial/provider engines and persistent schemas remain unchanged.

Added build hashes and API contract binding, strict opt-in static configuration,
foreground startup/health scripts, pinned dependency/tool profiles, read-only CI,
offline network protection, candidate artifact checks and operations documentation.
No financial authority, approval route, provider fallback or automatic resume was added.

## B. Exact changed files

Modified existing files:

- `.env.example`
- `.gitignore`
- `README.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `frontend/README.md`
- `src/quantlab/api/app.py`
- `src/quantlab/api/config.py`

Added files:

- `.github/workflows/ci.yml`
- `docs/deployment.md`
- `docs/release-engineering.md`
- `docs/phase24-report.md`
- `requirements/build.txt`
- `requirements/ci.txt`
- `requirements/constraints.txt`
- `scripts/__init__.py`
- `scripts/build_dashboard.py`
- `scripts/check-health.ps1`
- `scripts/check_environment.py`
- `scripts/check_secrets.py`
- `scripts/check_workflows.py`
- `scripts/package_release.py`
- `scripts/start-local.ps1`
- `src/quantlab/api/static.py`
- `tests/conftest.py`
- `tests/deployment/__init__.py`
- `tests/deployment/test_deployment.py`
- `tests/deployment/test_process.py`
- `tests/deployment/test_production.py`
- `tests/deployment/test_release.py`

30 source/config/documentation files total: 8 modified, 22 new. Contract export
was run but produced no semantic checked-in schema/type/fixture change.
Ignored local verification logs/environments/builds are not source changes.

## C. GitHub Actions architecture

PR, master push and manual dispatch triggers. Read-only `contents: read`, no
provider/repository secret references, SHA-pinned GitHub actions, explicit job
timeouts, non-persistent checkout credentials and superseded-PR cancellation.
Download caches use dependency manifests, not copied installed environments.

`validation` verifies dependencies/imports, secrets, YAML/policy and Python advisories;
`regression` runs exactly one authoritative full suite after validation;
`windows` focuses on deployment/lifespan/recovery; `frontend` runs npm ci/tests/
audit/ls, actual schema/fixture export, generated type and Git drift checks, production
build, real asset integration and wheel validation. Manual `release-candidate`
depends on all four jobs and reuses the verified build artifact; it never publishes.
The full job has a 45-minute timeout; baseline local resource cost was about 14
minutes. Hosted runner duration/resource estimates have not been measured.

## D–E. Reproducible installation, startup and shutdown

Supported deployment profile: full CPython 3.11.x x64, Node 22.16.0, npm 11.6.2.
Application/dev/cloud transitive constraints preserve the existing graph; npm ci
uses the unchanged lockfile. Packaging pins are pip 26.2.1, setuptools 83.0.0 and
wheel 0.46.2. They fix auditor findings without application mass upgrades.
Version pins are not a vendored/hash-locked dependency mirror or a guarantee of
byte-identical archives across timestamps/platforms. Other Python minors are not
qualified by this increment, despite broader historical package metadata.

Fresh installation: full Python venv, install `requirements/build.txt`, then
`pip install --no-build-isolation -c requirements/constraints.txt -e '.[dev]'`, pinned npm ci,
frontend tests/contracts/build and `python scripts/build_dashboard.py`.
Runtime can instead install the verified project wheel with constraints and
serve prebuilt assets without Node or the optional Gemini SDK.
See [complete fresh Windows commands](deployment.md).

Inject the existing operator secret securely, set cloud false, then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\start-local.ps1
```

Open `http://127.0.0.1:8000`; paste the appropriate API credential. Stop with
Ctrl+C in the foreground terminal and wait for `shutdown_complete`. No background
PID service, automatic trading startup, queue execution or remote deployment exists.
RemoteSigned applies only to this process; no persistent execution policy changes.
Organization policy remains authoritative; direct Python startup is available.

## F. Security/credentials

Operator credential is required, no default; optional reader credential differs.
Reader/operator guards, local TrustedHost/CORS, strict Pydantic/JSON contracts,
request/concurrency limits, safe request/server logging and no proxy-header trust
remain intact. Only liveness and explicitly listed static HTML/assets are public.
No private files, catch-all SPA/API fallback or diagnostics route is added.
Build validation rejects missing/altered/extra assets and API contract mismatches.
Keep build files immutable while serving; hashes are integrity checks, not signatures.

Secrets stay in server environment; no implicit `.env` loader or `VITE_` credential
configuration exists. Ignore rules cover `.aws`, local environments, secrets, keys,
SQLite stores and outputs. The bounded secret guard prints filenames/rules only,
scans tracked/proposed nonignored files, and is not exhaustive history scanning.
GitHub native secret scanning/push protection is recommended where available.
Cloud remains disabled/free-only with explicit limits and manual execution/consent;
CI needs no key, paid inference, broker/subscription or trading service.

## G. Persistence/recovery guarantees

HTTP research jobs/results and AI jobs/idempotency/budgets are process-local and
reset at restart. Browser credentials/drafts are memory-only. Default paper and
portfolio catalogues are empty. Standalone paper owners and portfolio views are
in-memory; existing durable paper stores preserve committed journals/checkpoints
only through their explicit verified recovery boundary. ACTIVE recovery retains
recorded operator pause/resume gates. File-backed journal/datasets and explicitly
retained exports survive; in-memory equivalents do not. API operations are not
automatically journaled and stores have no cross-owner atomic transaction.
No schema migration or automatic trading recovery was introduced.
Back up stopped/closed stores separately, preserve interrupted-shutdown sidecars,
and recover a copy through existing verified reopen procedures.

## H. Operational checks

Existing public `/api/v1/health`, authenticated `/api/v1/ready` and `/openapi.json`
remain. Readiness preserves busy/recovery gates and is independent of Gemini.
The PowerShell health script verifies those endpoints and production root HTML
without printing secrets. Production tests check actual script/style responses,
schema equality, authentication, unknown-path rejection and disabled cloud.
Real loopback process tests cover authenticated readiness, restart and Uvicorn's
graceful exit/draining; existing lifecycle tests cover cancellation and uncertain
owner cleanup. These do not establish connected-browser layout/workflow QA.

## I. Exact local verification

| Check | Result |
| --- | --- |
| Baseline API contract/lifespan targeted checks | 14 passed in 5.76s |
| Deployment + API security/lifespan during stabilization | 81 passed in 27.74s |
| Implementation-stage deployment/production/process/release tests | 21 passed in 36.85s; final audit follow-up below |
| Frontend clean isolated npm 11.6.2 ci | 185 packages installed; 186 audited; 0 vulnerabilities |
| Frontend Vitest | 6 files, 97 tests passed; duration 14.44s |
| TypeScript + Vite 7.3.7 production build | Passed; 51 modules; Vite build 2.02s |
| Actual OpenAPI/fixtures export + generated contracts + Git drift | Passed; no semantic changes |
| npm audit / npm ls --all | 0 vulnerabilities / passed; restored original dependency tree also passed ls |
| YAML, permissions, triggers, SHA pins, full-suite policy | Passed |
| actionlint 1.7.7 | Passed; shellcheck/pyflakes optional integrations disabled |
| Python AST checks for added code/tests | 13 files parsed successfully |
| Original environment pip check and import/constraint check | Passed |
| Fresh full-Python runtime venv, installed wheel, pip check and all imports | Passed; no Gemini SDK installed |
| Real runtime-only server + PowerShell health + graceful shutdown | Passed |
| Pinned application/dev/cloud + fixed tooling advisory audit | 70 dependency entries; 0 known vulnerabilities |
| Installed supported runtime advisory audit | 62 entries; 0 known vulnerabilities; local project absent from PyPI is advisory-skipped |
| Wheel source/version + dashboard manifest checks | Passed |
| Dirty current-tree release packaging | Correctly refused; no candidate claimed |
| git diff --check | Passed |
| Full Python regression, one run | **4154 passed in 913.42s (15m13s); no failures or skips** |

The original `.venv` pointed to missing CPython 3.11.9; the Windows Store alias
was unusable. Official portable Python was downloaded under ignored `tmp/phase24`.
The full regression uses the original pinned dev packages with that 3.11.9 runtime.
A separate official full-Python runtime created a fresh venv for wheel/runtime-only
installation, actual process health and advisory verification. The old environment
itself was not repointed or mass upgraded.

Root npm ci encountered a pre-existing locked esbuild executable. Validation used
a clean isolated copy of tracked frontend sources; npm ci, tests, build, audit and
ls succeeded. Missing original dependency files were restored from that identical
lockfile install without stopping the user's pre-existing process; root npm ls and
contracts subsequently passed. Initial sandbox/esbuild and PowerShell Restricted
policy failures are environmental diagnostics, not reported as passing checks.

Initial old-environment audit found 10 advisories in pip 24.0/setuptools 65.5.0.
A subsequent tool audit identified wheel 0.45.1; all were addressed in the supported
packaging profile. No application dependencies were upgraded. See the
[pip changelog](https://pip.pypa.io/en/stable/news/),
[setuptools advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c)
and [wheel advisory](https://github.com/pypa/wheel/security/advisories/GHSA-8rrh-rw8j-w5fx).
Audit findings are date-specific and must be rechecked before release.

## J. CI status

Local syntax/policy/actionlint checks passed. **No GitHub-hosted run was executed.**
Runner/action/cache/upload/download behavior and full hosted Linux/Windows execution
remain pending until this uncommitted workflow is authorized, committed and run
on GitHub. No claim of passing GitHub Actions is made.

## K. Packaging status

Validated ignored build products: `dist/agentic_quant_lab-0.1.0-py3-none-any.whl`
and `frontend/dist` with 7 served files plus build manifest (8 files total).
Final wheel SHA-256:
`1e2e3bb8fc86247865cb12a34d9e00a6c5405179a7e7361257e981435b294e3c`
(rebuilt during the final audit to include corrected README metadata).
Build manifest contract SHA-256:
`ee130adada69c2b22f80fc01dd3048d011c23e5056ea24407991beb2c5116970`.
Wheel contents and metadata match sources; fresh installation was verified.

Clean fixture repositories test source archive, wheel/dashboard payloads,
manifest/outer hashes, dirty/untracked refusal, corrupt assets/wheels, secret
rejection and altered candidate detection. Current-tree packaging correctly
refuses because requested changes remain uncommitted. No final release candidate,
GitHub release, tag or signed installer is claimed. Manual candidate workflow will
produce a short-lived artifact after its dependent CI checks pass; artifact access
follows repository visibility/read permissions and is not guaranteed private.
Local CLI records a supplied CI URL but does not verify remote status itself.
See [artifact contents/install/rollback runbook](release-engineering.md).

## L. Limitations and Phase 25 gates

No public hosting, tested container, multiple workers, Windows service or distributed
job/budget durability. No HTTP approval, cloud image upload, paper mutation/recovery,
automatic journal imports or broker/live execution. Trusted composition is still
required for connected paper/portfolio registrations and local approval/data selection.

Retain Phase 25 gates: explicitly authorized successful post-fix real Gemini smoke;
real authenticated browser research/cloud/result/trusted approval/local dataset/
feature/backtest/supported journal walkthrough; final responsive/chart/keyboard QA;
actual passing GitHub CI and final release/version/licensing/artifact approval.
Prior smoke (23 input/25 output tokens, 4543.863ms, 0 financial mutations,
approval false) preceded final adapter fixes and does not close those gates.

## M. Git/commit readiness

HEAD remains the requested baseline on master. Changes are local and uncommitted;
no push occurred. Source/config/docs are ready for review after the recorded local
checks; GitHub-hosted CI and Phase 25 approval remain pending. Build artifacts,
temporary runtimes, audit logs, databases and virtual environments stay ignored.

## Final bounded pre-commit audit — PASS

Performed on 13 October 2026 against the same baseline with the 30 Phase 24 files
uncommitted. No full Python regression, npm tests/build or TypeScript generation
was repeated. The prior 4,154/97 regression results remain historical evidence;
this audit adds six focused regression cases. No cloud service was invoked.

Confirmed defects were reproduced failing-first:

1. **Pinned backend bypass:** default editable installs create a separate build
   environment despite seeded setuptools 83.0.0. A no-index/no-deps dry-run fixture
   failed resolving isolated setuptools; the same fixture succeeded with
   `--no-build-isolation`. The new CI policy regression also failed before correction.
   All four application-install jobs and supported README/runbook source installs
   now use the seeded build tools without isolation. The policy guard checks this.
   See [pip build isolation](https://pip.pypa.io/en/stable/reference/build-system/).
2. **Unchecked wheel payloads:** an extra `credentials.json` outside package/metadata
   was accepted. Wheel validation now rejects unexpected, noncanonical or duplicate
   paths and checks every uncompressed file with the bounded credential guard.
3. **Generated credential inclusion:** synthetic Gemini-format canaries in wheel
   metadata and correctly hashed dashboard HTML were accepted by packaging; the
   local stamping command also accepted a canary in JavaScript. These probes used
   fake format canaries, not real credentials. Both build stamping and release input
   validation now scan generated payloads/manifest and fail without echoing values.
   No actual credential was found in the real artifacts. The guard remains bounded,
   not proof that arbitrary opaque secrets can always be identified.
4. **Test-child leak after shutdown timeout:** the original communicate-only cleanup
   left its owned child alive. A failing-first probe confirmed it and cleaned up its
   own child. The test supervisor now kills/reaps only its own timed-out child and
   preserves the failure. Application shutdown/draining and financial owners are
   unchanged; no unrelated user processes were stopped.

Also corrected the assertion that Actions artifacts are inherently private.
Artifact access follows repository read permissions, including public-repository
readers signed into GitHub. This documentation correction is supported by
[GitHub artifact access documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
No release publication or external deployment is automated.

Corrections within the existing file inventory: `.github/workflows/ci.yml`,
`README.md`, `docs/deployment.md`, `docs/release-engineering.md`, this report,
`scripts/build_dashboard.py`, `scripts/check_workflows.py`,
`scripts/package_release.py`, `tests/deployment/test_deployment.py`,
`tests/deployment/test_process.py`, `tests/deployment/test_release.py`.
No new source/config/documentation files were added by this audit.

Exact validation:

- Failing-first artifact probes: 3 failed as expected; backend policy probe: 1
  failed as expected; timeout cleanup: 1 failed as expected; local stamp canary:
  1 failed as expected. All six pass after correction.
- Combined deployment/API security/lifecycle checks: **95 passed in 44.98s**.
- Subsequent complete deployment checks, including the final local stamp correction:
  **27 passed in 46.80s**. This includes real process readiness/restart/shutdown,
  timeout reaping, cloud-disabled startup, authenticated static integration and
  release rejection/integrity cases.
- Actual project no-index/no-deps source metadata dry-run with seeded backend and
  `--no-build-isolation`: passed; installed backend is setuptools 83.0.0.
- Supported runtime `pip check` and all-package import/constraint checks: passed.
- Updated stamping command on existing production assets: passed, including actual
  OpenAPI equality and file/credential checks. React/TypeScript sources and assets
  were unchanged, so npm/contract-generation checks were not repeated.
- Wheel rebuilt once offline with pinned tooling because README metadata changed;
  wheel source/version/path/credential and dashboard integrity validation passed.
- Updated YAML/permissions/pins/backend policy and actionlint 1.7.7: passed
  (optional shellcheck/pyflakes integrations disabled).
- Tracked/proposed file credential guard and `git diff --check`: passed.
- No remaining audit-owned Python subprocesses were observed after test completion.

Reviewed unchanged reader/operator authentication, explicit loopback binding,
no public API fallback, strict validation, disabled/free-only cloud defaults,
foreground process ownership and existing persistence/recovery documentation.
No paper, backtest, risk, journal, portfolio, persistence or LLM engine files changed
relative to the baseline. No trading authority, schema migration or auto-resume
was introduced. Original dependency pins/audit results are unchanged; dependency
audits were not rerun because no dependency/version change was made.

**Commit readiness: PASS for local review/commit.** HEAD is still
`e43227b94d11d0a5cd46a50767503e259130faaf` on master; 8 modified and 22 new files
remain uncommitted. No commit/push/tag was performed. Actual GitHub-hosted
Ubuntu/Windows jobs, clean installs/caches, artifact upload/download round-trip,
and manual candidate packaging for the same committed SHA remain required.
Those runs have not executed and are not claimed to pass. Phase 25 live
Gemini/browser QA and final release approval gates remain open.
