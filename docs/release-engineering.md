# CI and candidate release runbook

`.github/workflows/ci.yml` runs on PRs, pushes to master and manual dispatch.
All jobs have explicit timeouts, read-only contents permission, full SHA action
pins and non-persistent checkout credentials. No repository/provider/broker secrets
are referenced. Only superseded PR runs cancel. No `pull_request_target`, tag,
publication or deployment action is used. Cloud stays disabled, and the deterministic
pytest fixture rejects outbound socket connections while permitting loopback tests.
Do not put live provider smoke tests into ordinary pytest.

| Job | Work |
| --- | --- |
| validation | Python install/constraints, pip check, all imports, tracked secret guard, YAML/policy check, pip-audit, diff whitespace |
| regression | After validation: exactly one complete Python regression run, no sharding/reduced coverage |
| windows | Focused Windows deployment, lifespan and persistence recovery defects |
| frontend | Python schema/fixtures export, npm ci/tests/audit/ls, generated contract drift, tsc/Vite build, real static integration, wheel/source validation, short-lived build inputs |
| release-candidate | Manual boolean only, after all four checks pass; reuse verified frontend/wheel artifacts and produce unapproved bundle |

Each isolated runner installs its own required environment once. Pip/npm caches
cache downloads rather than installed environments; dependency manifests key them.
Full-suite job budget is 45 minutes; the baseline local suite took about 14 minutes.
Hosted Ubuntu/Windows x64 runners need several GB RAM and storage for dependencies,
fixtures and builds. Frontend/Windows jobs overlap validation; full regression begins
after validation. Audit outages fail checks rather than silently skipping security.
Resource/runtime estimates are not measured GitHub execution evidence.

Security checks combine current tracked-path/content guards, Python advisory audit
and npm audit. They never print matched secret values. The custom guard covers
private keys, common cloud token formats, credential assignments, browser secret
variables and forbidden credential/storage paths. It is intentionally bounded,
not exhaustive history scanning: enable GitHub native secret scanning/push protection
where available, review new files and rotate any accidentally exposed secret.
No tool can promise detection of every arbitrary bearer value. The guard scans
tracked and proposed nonignored working contents; candidate packaging scans committed source. Ignore rules
exclude `.env*` except the empty example, `.aws`, keys, databases, outputs and builds.

Action release/pin provenance:
[checkout v6.0.2](https://github.com/actions/checkout/releases/tag/v6.0.2),
[setup-python v6.1.0](https://github.com/actions/setup-python/releases/tag/v6.1.0),
[setup-node v6.1.0](https://github.com/actions/setup-node/releases/tag/v6.1.0),
[upload-artifact v6.0.0](https://github.com/actions/upload-artifact/releases/tag/v6.0.0),
[download-artifact v7.0.0](https://github.com/actions/download-artifact/releases/tag/v7.0.0).
Local workflow checks include YAML/policy parsing and
[actionlint v1.7.7](https://github.com/rhysd/actionlint/releases/tag/v1.7.7)
with optional shellcheck/pyflakes disabled. GitHub expression/runner/action/network
behavior is still pending until the committed workflow runs there.

## Local preparation

Keep version `0.1.0` until a separately approved version change; API schema version
`1.0.0` identifies the HTTP contract, not final product release approval. Wheel
version comes from `pyproject.toml`; frontend version is `0.1.0`. Do not tag/publish
automatically. Prepare release notes describing the final supported scope, exact
tests/CI run link, dependency audit results, compatibility/security boundaries,
known unsupported workflows and the remaining approval gates.

```powershell
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m pip install -r requirements/build.txt
& $python -m pip check
& $python scripts/check_environment.py
& $python scripts/check_secrets.py
& $python scripts/check_workflows.py
& $python -m pip install -c requirements/constraints.txt -r requirements/ci.txt
& $python -m pip_audit --progress-spinner off
& $python frontend/scripts/export_contracts.py
cd frontend
npm.cmd exec --yes --package=npm@11.6.2 -- npm ci
npm.cmd test
npm.cmd run contracts:check
npm.cmd audit --audit-level=low
npm.cmd ls --all
npm.cmd run build
cd ..
git diff --exit-code -- frontend/contracts frontend/src/api/generated.ts frontend/src/test/fixtures.json frontend/src/debugger/offline.json
& $python scripts/build_dashboard.py
& $python -m pytest tests/deployment -q
& $python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
& $python scripts/package_release.py --check-build
# Run the full suite once after stabilization, not after each small change:
& $python -m pytest -q --junitxml=tmp/regression.xml
git diff --check
git status --short
```

Run commands individually and stop on failure; Windows PowerShell does not stop
automatically for every nonzero native command. Install build tools before wheel
creation. Ensure `dist` contains exactly one project wheel; move older generated
wheels to a separate ignored archive before rebuilding.

After authorized changes are reviewed/committed and the tree is clean, manually
dispatch Phase 24 CI with `prepare_release=true`. The candidate depends on passing
validation/regression/Windows/frontend jobs for that same run. For manual local
packaging, record and independently verify a successful CI run for the exact commit:

```powershell
& $python scripts/package_release.py --ci-run-url '<verified GitHub Actions run URL>'
& $python scripts/package_release.py --verify '<candidate zip path>'
Get-FileHash -Algorithm SHA256 '<candidate zip path>'
```

The local CLI records the supplied CI URL; it does not authenticate/check remote CI
status. The workflow's job dependencies provide that gate for automated candidates.
No dirty override exists. Current Phase 24 edits deliberately remain uncommitted,
so packaging this working tree must refuse; build-input validation remains possible.

## Candidate contents and installation

Candidate ZIP includes `source.zip` from clean committed Git source, one wheel under
`wheels/`, verified dashboard under `dashboard/`, `INSTALL.md`, and `manifest.json`
with source commit, version, CI URL, `release_approved=false` and all payload hashes.
A sibling `.zip.sha256` covers the ZIP. Wheel validation compares every Python
package source byte and metadata version; build validation checks exact asset set,
hashes and schema identity. Candidate verifier rejects altered/missing/extra payloads.
Build-input validation also rejects unexpected wheel paths and scans uncompressed
wheel files, dashboard files and the build manifest with the bounded credential
guard. Integrity hashes alone do not establish absence of embedded credentials.
Hashes establish integrity, not signatures or provenance authentication.
No databases, environments, tokens, private datasets, uploads or user cloud evidence
are included. Dependencies are not bundled; installation downloads the pinned graph.
Workflow artifact visibility follows repository read access; these artifacts are
not guaranteed private. Users signed into GitHub with repository read access can
download them, including readers of a public repository. See
[GitHub artifact access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

Verify the outer digest against a trusted record, run `--verify`, extract into a
new directory, then extract `source.zip` there. Install the wheel into a fresh
Python 3.11 venv using source `requirements/constraints.txt`. Copy `dashboard/`
to that source tree's `frontend/dist/` and run the normal `start-local.ps1` with
secure environment injection. It needs no Node build at runtime. Installation
instructions are in [deployment.md](deployment.md). Do not overlay an old running
environment or overwrite durable stores during upgrade.

## Rollback and Phase 25 gates

Stop gracefully, preserve separately backed-up state, and restart the previous
verified source/wheel/dashboard/environment together. Keep credentials outside
bundles and avoid reusing cloud budget state as a durable spending guarantee.
Phase 24 changes no persistent schema; do not assume future versions permit a
database downgrade. After uncertain/crashed shutdown, use existing verified
paper/journal recovery on a restored copy; no automatic financial resume.

Before any final V1.0 approval, retain these open Phase 25 gates:

1. Successful **post-fix live Gemini smoke**, explicitly authorized outside CI.
   Prior smoke: Gemini `gemini-3.1-flash-lite`, input 23/output 25 tokens,
   4543.863ms, financial mutations 0, approval false. It preceded final adapter fixes.
2. Real authenticated connected-browser research walkthrough: explicit consent,
   cloud interpretation/result inspection, trusted review/approval, explicit local
   dataset/feature selection, deterministic backtest and supported results/journal
   inspection. Default launch has empty paper/portfolio catalogues, no HTTP approval
   and no automatic journal imports; unsupported steps require trusted Python work.
3. Final responsive/chart/keyboard browser QA and actual passing GitHub CI.
4. Final release notes/version/licensing/artifact review and explicit approval.

These are pending gates, not fabricated acceptance evidence. Phase 24 never invokes
cloud inference, creates tags, publishes a GitHub release or deploys externally.
