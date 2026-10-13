# Local deployment and operations (Phase 24)

Supported target: one trusted Windows x64 workstation, one foreground Python
process, authenticated FastAPI and production React assets at
`http://127.0.0.1:8000`. API-only operation and the existing Vite development proxy
remain available. Public/network hosting, Windows services, multiple workers,
containers, TLS termination and automatic trading recovery are unsupported.
Docker is deferred: this workstation does not need a second runtime or database.
This is release engineering for V1.0, not Phase 25 release approval.

## Fresh Windows installation

Install Git, a full CPython **3.11.x** x64 installation (including pip/venv), and
Node **22.16.0** x64. These are the supported Phase 24 profile; the package's
historical `>=3.11` metadata is broader than the verified deployment profile.
Use current security fixes within 3.11 when available. The local baseline was
3.11.9; other Python minors are not qualified here. npm **11.6.2** is selected to
avoid the previously observed npm 10 optional-peer resolver failure.
Initial downloads require internet, but ordinary cloud-disabled startup does not.
Do not use the Windows Store Python alias or an embeddable distribution for
normal installation. Start PowerShell in your chosen checkout directory.

```powershell
git clone <your-repository-url> agentic_quant_lab
cd agentic_quant_lab
py -3.11 -m venv .venv
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m pip install -r requirements/build.txt
& $python -m pip install --no-build-isolation -c requirements/constraints.txt -e '.[dev]'
& $python -m pip check
& $python scripts/check_environment.py
cd frontend
npm.cmd exec --yes --package=npm@11.6.2 -- npm ci
npm.cmd test
npm.cmd run contracts:check
npm.cmd run build
cd ..
& $python scripts/build_dashboard.py
```

`requirements/constraints.txt` pins the existing resolved application/dev/cloud
graph; it does not force optional dependencies into runtime installations.
Install the pinned build tools first and use `--no-build-isolation` for this
project's source/editable installs; otherwise pip creates a separate backend
environment that does not use the already-installed setuptools pin.
`pywin32` has a Windows marker. Always pass the constraints on application installs.
The application graph is preserved from Phase 23, not mass upgraded. Packaging
tools are narrowly pinned to pip 26.2.1/setuptools 83.0.0/wheel 0.46.2 to address
advisories found in old packaging tools. This is version
reproducibility, not a vendored or hash-locked offline dependency mirror. CI audits
advisories separately. Packaging/audit tools live in separate requirement files;
their transitive dependencies are not part of the application pin guarantee.
`frontend/package-lock.json` and `npm ci` fix the frontend graph. The existing
Rollup WASM override remains. A build manifest binds every served file's SHA-256
and the actual generated OpenAPI schema. Local stamping rejects known credential
formats in generated assets/manifest before marking the build usable. This bounded
guard does not identify every arbitrary opaque secret. No generated dist or environments enter Git.

For runtime-only installation after building a wheel:

```powershell
& $python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
# In a fresh full-Python runtime venv, choose the exact wheel filename:
& $python -m pip install -c requirements/constraints.txt dist/agentic_quant_lab-0.1.0-py3-none-any.whl
```

Runtime dependencies do not include the Gemini SDK. Add cloud capability only
deliberately with `pip install --no-build-isolation -c requirements/constraints.txt '.[cloud]'` from
the source tree, or `agentic-quant-lab[cloud]==0.1.0` against a locally supplied
wheel (`--find-links` its directory). `.[dev]` includes the SDK for mocked SDK
tests; installing it never enables inference.

## Configuration, startup and shutdown

Environment variables are consumed directly. `.env.example` documents names;
neither launcher loads `.env`. Inject bearer credentials from a trusted secret
manager or a password prompt, never command-line literals, source, logs or `VITE_`
variables. Example for an existing securely retained operator token:

```powershell
$credential = Read-Host 'Operator bearer credential' -AsSecureString
$env:QUANTLAB_API_OPERATOR_TOKEN = [System.Net.NetworkCredential]::new('', $credential).Password
$env:QUANTLAB_CLOUD_ENABLED = 'false'
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\start-local.ps1
```

An operator token is required: 32–256 non-whitespace ASCII characters, randomly
generated and retained securely. No built-in token exists. Optional reader token
must meet the same format and differ. Provide the corresponding token manually
in the dashboard's password input; it remains in browser memory until reload or
Disconnect. Selecting Operator in the UI does not confer authorization.
On a fresh machine, generate/store a high-entropy token in your password manager
and use the password prompt above. For an ephemeral local session, this creates
a random token directly in the environment without printing it to the terminal:

```powershell
$env:QUANTLAB_API_OPERATOR_TOKEN = (& $python -c "import secrets; print(secrets.token_urlsafe(48))")
```

Retain it securely for entry into the dashboard; it is not embedded in the build.

Open `http://127.0.0.1:8000`. `start-local.ps1 -Port 8001 -JournalPath 'C:\QuantLabState\journal.sqlite'`
selects another port or trusted journal path; its parent directory must exist.
Use an absolute journal path outside OneDrive/network sync for persistent data.
Without `-JournalPath`, an existing environment path is retained; otherwise no
journal opens. `-Python` can select a different installed full-Python environment.
The script owns no background process/PID file and starts no paper owner/feed.
The command above selects RemoteSigned for that process only, because fresh
Windows PowerShell commonly refuses unsigned local scripts under Restricted.
It changes no persistent policy. Respect organization policy; if it overrides
local script execution, use the equivalent direct Python launcher with the
documented environment or arrange authorized signing. Do not weaken system policy.
Repeated startup after a clean stop follows the same path. An occupied port fails
with sanitized diagnostics; do not kill unrelated processes to free it.

Press **Ctrl+C in the server terminal** to stop. Wait for `shutdown_complete` and
the prompt: existing research/cloud work drains before owners close. There is no
new HTTP stop endpoint and no automatic queued execution. Do not close the terminal
or use `Stop-Process` as the ordinary shutdown method. Clear credentials when done:

```powershell
Remove-Item Env:QUANTLAB_API_OPERATOR_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:QUANTLAB_API_READER_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
```

| Variable | Meaning/default |
| --- | --- |
| `QUANTLAB_API_OPERATOR_TOKEN` | Required server operator secret; missing/invalid fails closed |
| `QUANTLAB_API_READER_TOKEN` | Optional separate read-only secret |
| `QUANTLAB_API_PORT` | 8000; validated 1024–65535 |
| `QUANTLAB_API_FRONTEND_PATH` | Optional verified build path; launcher script sets `frontend/dist` |
| `QUANTLAB_API_JOURNAL_PATH` | Optional trusted SQLite journal file; not browser controlled |
| `QUANTLAB_API_CORS_ORIGINS` | Empty by default; only exact loopback origins admitted |
| `QUANTLAB_CLOUD_ENABLED` | `false`; only literal `true`/`false` accepted |
| `QUANTLAB_CLOUD_PROVIDER`, `MODEL`, `MODEL_ALLOWLIST` | Gemini and explicit model admission; use full `QUANTLAB_CLOUD_` prefix |
| `QUANTLAB_CLOUD_FREE_VERIFICATION` | Current strict free-tier/billing-disabled evidence JSON |
| `QUANTLAB_CLOUD_MAX_REQUESTS`, `TIMEOUT_MS` | 20 reserved requests/session, 20000ms; strict bounds |
| `GEMINI_API_KEY` | Optional server secret; never bundled |

See [cloud-llm.md](cloud-llm.md) for exact free evidence, zero-cost admission,
token caps, consent and explicit execute requirements. Invalid cloud syntax fails
configuration; an enabled but inadmissible/unavailable provider reports a bounded
state without paid fallback. Cloud-disabled readiness does not require a key or
contact Gemini. Cloud budgets and jobs reset on restart; this is not a durable
cross-process spending ledger.

## Health and security boundaries

In a second PowerShell with the same securely injected credential:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\check-health.ps1
```

Checks public liveness, authenticated readiness, authenticated OpenAPI and root
HTML. `/api/v1/ready` checks existing owner recovery gates; a busy lane can return
503. Startup checks build hashes/contract and journal integrity before admission.
`tests/deployment/test_production.py` additionally verifies real script/style
responses against built assets; the lightweight health script does not substitute
for that check or a connected browser walkthrough.

Only liveness and explicitly allowlisted dashboard assets are public. All API
data/readiness/schema access keeps bearer authentication and mutation keeps
operator authorization. Static serving has no catch-all SPA fallback and cannot
serve `.env`, manifest, storage, unknown asset files or unknown API routes.
TrustedHost, exact local CORS, request-size/concurrency bounds, no-store/nosniff,
safe request/server logging and disabled proxy-header trust remain in force.
Keep the build directory immutable while running; stop before replacing files.
SHA-256 detects corruption/drift, not a malicious trusted workstation administrator.
The dashboard and dependencies share the local workstation trust boundary.

Do not bind `0.0.0.0`, add forwarding/tunnels or publish to the internet. A public
deployment requires separate auth/session design, TLS, origin policy and a threat
model review. The browser has no approval, order/account/session mutation or
financial computation authority. No brokerage/live execution capability exists.

## Persistence, restart and recovery

| State | Actual restart boundary |
| --- | --- |
| HTTP research operation identities, queue/status/results | Process-local; lost on restart; submission never auto executes |
| AI jobs, inputs/results, idempotency keys, cloud budget | Process-local; lost/reset; no resumption or inference at startup |
| Browser token/drafts/selected reports | Browser memory; reload/disconnect clears them |
| Default API paper/portfolio catalogues | Empty; launcher registers no trading owner or recovered session |
| Standalone paper/session owners | In-memory unless explicitly wrapped in existing Phase 18E/F durable store |
| Existing durable paper SQLite store | Committed log/checkpoints survive; trusted explicit reopen verifies/replays; recovered ACTIVE requires recorded operator pause/resume |
| Phase 20 journal | Survives only with explicit file path and explicit imports; reopen verifies log/indexes; no automatic research-job persistence |
| Phase 19 portfolio views/event history | In-memory; caller-retained journal/replay can reconstruct; no independent durable portfolio service |
| Market datasets/provenance | Existing file-backed SQLite dataset stores survive; in-memory stores do not |
| Research/audit exports | Only explicitly retained/imported files or journal artifacts survive; HTTP results are not automatically exported |

Do not infer journal availability means new operations are automatically archived.
There is no transaction across paper/research/journal stores. Existing SQLite
schemas/recovery and Decimal/risk/causality checks are unchanged; no new migration
framework or financial resume path is added. Trusted Python composition may register
existing owners via `create_app(..., services_factory=...)`; their existing admission,
recovery gates and explicit close callback still apply.

Back up with owners stopped and closed. Copy complete store files and retained
datasets/artifacts to a separate secure local backup; preserve any SQLite sidecars
if shutdown was interrupted. Do not delete sidecars or edit recovery-required stores.
Restore a copy into a separate location and use the existing verified store reopen
procedures before pointing the API at it. Never automatically resume trading.
See [paper-session-persistence.md](paper-session-persistence.md),
[phase20-report.md](phase20-report.md) and [portfolio-management.md](portfolio-management.md).

## Troubleshooting

- Missing Python / broken `.venv`: reinstall full Python 3.11 and create a new venv;
  environments are machine specific, not portable artifacts.
- npm 10 resolver failure: use the pinned npm 11.6.2 command above. Locked esbuild
  during `npm ci`: stop your own development/build process and retry; do not kill
  unrelated user processes. An isolated source copy is also suitable for validation.
- Invalid build: run the production build and `build_dashboard.py` with matching
  sources/contracts. Stale, missing, extra or changed files fail startup.
- 401/403: inject/paste the matching reader/operator token; role selector is not auth.
- 503: check lifespan completion, busy work or trusted storage recovery. Public
  health alone does not prove readiness.
- Empty paper/portfolio/journal views: expected without explicit trusted registrations
  and journal imports; the launcher never fabricates a connected session.
- Gemini disabled/rejected/unavailable: inspect authenticated configuration, verify
  server evidence/bounds and optional SDK. No retries or paid fallback are implied.

## CI and release preparation

See [release-engineering.md](release-engineering.md) for CI architecture, artifact
contents, validation, notes, rollback and Phase 25 gates. GitHub-hosted execution
must be recorded separately from local workflow validation.
