# Phase 22 — Professional dashboard and visual strategy debugger

Implementation: 9–10 October 2026. Baseline `master`:
`687e018132b4808ecddb0a0973bc9a2b73d4d05e`, initially clean.
One local increment; no Phase 23 implementation, commit, push or publication.

The subsequent [focused release-readiness review](phase22-release-review.md)
records four corrections, 69 passing frontend tests, real client/API fixture
verification and the remaining browser release gate. Its final assessment
supersedes this implementation report's original verification/commit-readiness
summary; the Python and performance measurements below are historical evidence.
The later [UI refinement report](phase22-ui-refinement.md) records the authorized
terminal redesign and isolated synthetic workstation demo.

## A. Implementation summary

An isolated React/TypeScript/Vite dashboard connects to the actual Phase 21 HTTP
surface. Seven pages cover portfolio, research operations, strategy validation,
completed results, paper sessions, journal queries and historical debugging.
Quantitative Python remains the sole authority for approval, calculations, risk,
orders, accounting, provenance and durable history.

The coherent dark financial workspace has a sidebar, connection toolbar, responsive
grids, exact financial facts, readable tables, identity/digest details, bounded
previews, accessible controls and consistent error/loading/empty states. Empty
catalogues remain empty: no demo balance, position, fill or profitability figure
is substituted for missing connected data.

## B. Files changed

Modified: `.gitignore`, `README.md`, `docs/architecture.md`, `docs/roadmap.md`.
Added `docs/phase22-report.md`, `tests/api/test_frontend_contract.py`, and:

```text
frontend/README.md
frontend/package.json
frontend/package-lock.json
frontend/index.html
frontend/tsconfig.json
frontend/vite.config.ts
frontend/contracts/openapi.json
frontend/scripts/export_contracts.py
frontend/scripts/check-contracts.mjs
frontend/scripts/measure-ui.mjs
frontend/src/main.tsx
frontend/src/App.tsx
frontend/src/styles.css
frontend/src/components.tsx
frontend/src/format.ts
frontend/src/api/client.ts
frontend/src/api/generated.ts
frontend/src/pages/Reports.tsx
frontend/src/pages/Research.tsx
frontend/src/pages/Strategy.tsx
frontend/src/pages/Journal.tsx
frontend/src/pages/Debugger.tsx
frontend/src/debugger/Chart.tsx
frontend/src/debugger/data.ts
frontend/src/debugger/offline.json
frontend/src/test/setup.ts
frontend/src/test/helpers.ts
frontend/src/test/fixtures.json
frontend/src/test/api.test.ts
frontend/src/test/screens.test.tsx
frontend/src/test/debugger.test.tsx
frontend/src/test/chart.test.tsx
```

No existing backend implementation, financial engine, Python dependency declaration
or earlier service contract changes. Build output, dependencies, caches and the
temporary verification Python environment are ignored.

## C. Frontend architecture

React 19.3.0, TypeScript 5.9.3 and Vite 7.3.7 are locked in the frontend workspace.
Lightweight Charts 5.2.1 supplies the interactive historical chart; native HTML
controls and custom shared components avoid a UI framework dependency. Hash routes
work with static hosting without a server router. React component state and an
abortable resource hook supply predictable local state, errors and manual refresh.
Editors remain mounted to preserve session drafts; large histories do not.

The real app's OpenAPI document generates `src/api/generated.ts` through
openapi-typescript 7.13.0. Python service fixtures are exported under a fixed
research clock. Tests bind the schema and fixture to actual backend output; a
generation check prevents checked-in TypeScript drift. Phase 21's shared request
serialization schema marks JournalQuery defaults required even though runtime
admission permits omission; the request alias explicitly makes these known defaults
optional. Legacy omitted OCO fields are handled without creating events.

One typed client uses fixed same-origin API paths, encoded identities and guarded
SHA-256 operation IDs. Reader clients deny mutating research calls locally, while
the server checks every credential/action. There is no frontend role-discovery
claim: the role selector expresses the requested interface restriction. Choosing
Operator with a reader token still receives server 403 responses.

Vite proxies `/api` only to `http://127.0.0.1:8000`, binds `127.0.0.1:5173`, and
requires no backend CORS change. The build uses Rollup's official WASM distribution
(`@rollup/wasm-node` 4.64.3) through an override because this machine's Windows
application-control policy blocked the native Rollup binary. No policy setting or
vendored source was modified. Initial npm 10.9.2 optional-peer resolution failed;
npm 11.6.2 completed installation. The generated lockfile's clean-install dry run
succeeds with npm 10.9.2.

Primary dependency references: [React](https://react.dev/),
[Vite](https://vite.dev/guide/),
[Lightweight Charts](https://tradingview.github.io/lightweight-charts/).
The chart's built-in TradingView attribution remains enabled.

## D. Screens, workflows and local startup

| Screen | Implemented behavior |
| --- | --- |
| Portfolio overview | Capability-discovered IDs; exact balance, allocation, collateral, reservations, fees, realized P&L; member strategies/versions; current equity/exposure suppression for stale/missing valuations; recorded risk breaches |
| Research workbench | All nine advertised kinds through strict JSON input; idempotency key; submit-only; inspect known ID; explicit queued execute/cancel; manual status; strategy/workflow/audit provenance |
| Strategy studio | Session-local StrategyContent JSON, local identity/version labels, API validation issues/digest, visible Draft/Validated distinction, text hypothesis editor |
| Results explorer | Selected completed operation; canonical result SHA-256 verification; bounded server scalar metrics, issues/risk/result details and exact canonical download |
| Paper sessions | Read-only registered snapshot, unmistakable paper label, historical equity mark, balances, fees, positions, reservations, execution/protective-order and risk/feed details, operator recovery warning, 20-row activity pages |
| Journal workspace | Trade/research switch; identity/version/digest/instrument/order/run filters; inclusive UTC start/exclusive end; 5/10/20-row backend cursor pages; read-only details, imported operation references, open/incomplete position summary |
| Visual debugger | API paper-record source or explicitly chosen offline fixture, historical observations and actual fill prices, causal event selection, zoom/pan, exact source table and dataset/strategy provenance |

Research kinds: `backtest`, `holdout`, `walk_forward`, `robustness`, `ml_dataset`,
`ml_training`, `ml_prediction`, `ml_prediction_features`, `performance`. The editor
accepts established request fields, excluding kind. Python validates and executes
those inputs; the dashboard does not create approval-bearing research payloads.
JSON input is deliberate initial scope, rather than separate graphical builders
for all nine complex requests.

Start the installed Python API using the existing README instructions and a valid
server-configured operator secret. From a second PowerShell terminal:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open `http://127.0.0.1:5173`, then enter an existing reader/operator token. Tokens
stay in memory. No Vite token environment variable or persisted credential is used.
The offline debugger requires no credential. The default API registers no paper
sessions or portfolios; trusted Python applications must inject existing owners
through `ApplicationServices`. A configured journal path supplies the existing
journal service, without automatic imports. `npm.cmd run preview` serves static
build output but provides no API proxy. Production hosting needs an explicitly
configured same-origin reverse proxy and remains outside this increment.

See [frontend/README.md](../frontend/README.md) for detailed startup, regeneration,
workflows and verification commands.

## E. API integration and lifecycle semantics

| Method | API path | Dashboard use |
| --- | --- | --- |
| GET | `/api/v1/capabilities` | Authenticate and discover research kinds, limits and registered reporting owners |
| POST | `/api/v1/strategies/validate` | Existing StrategyContent validation |
| POST | `/api/v1/research/operations` | Operator submission; metadata only |
| GET | `/api/v1/research/operations/{id}` | Reader-compatible manual status inspection |
| POST | `/api/v1/research/operations/{id}/execute` | Explicit operator execution; bodyless POST |
| POST | `/api/v1/research/operations/{id}/cancel` | Explicit queued cancellation; bodyless POST |
| GET | `/api/v1/research/operations/{id}/result` | Completed discriminated result and original canonical JSON/digest |
| GET | `/api/v1/paper/sessions/{id}/snapshot` | Read-only paper/debugger source |
| GET | `/api/v1/portfolios/{id}/snapshot` | Read-only consolidated/member reporting |
| POST | `/api/v1/journal/trades/query` | Strict read-only filtered cursor query |
| POST | `/api/v1/journal/research/query` | Strict read-only filtered research query |
| GET | `/api/v1/journal/sessions/{id}/summary` | Backend-derived completeness and realized summary |

There is no list-operations route. The workspace retains at most the advertised
identity count in memory and can inspect known/imported IDs. Submission does not
execute. Execute synchronously enters the single service lane; a busy lane returns
503. Running work cannot be cancelled. A browser disconnect does not terminate
Python execution; the unreachable-request notice tells the operator to refresh
the retained identity before retrying. Late connection-generation callbacks cannot
publish old operation metadata into a new authenticated session. API restarts lose
process-local jobs; no durable or automatic background-execution claim is made.

## F. Visual debugger functionality and missing contract

The source adapter traverses actual `SessionRecord` market events, decisions,
order events and OCO revisions. Quote bid/ask are plotted at their recorded quote
timestamps; complete bar closes use bar end timestamps and retain price-basis
labels in the exact table. Actual `execution_price`, `execution_time`, quantity
and action alone populate fill points. Decisions, transitions and risk records
have no invented execution price. Partial fills retain each actual event/quantity;
selected causal context exposes stops/targets, OCO, order/risk and account evidence
where present. The baseline demo has an entry and open position; it does not
pretend to demonstrate exits, partial fills or protective orders absent from it.

Zoom, pan, fit-all, mouse chart interaction and keyboard-accessible controls are
provided. Timeline buttons expose original identities, logical sequence, exact
timestamp, selected record and session context. Chart clicks select only recorded
points at the clicked chart time and price, using source identities; coincident
fill markers take precedence over observations. Data tables are the accessible exact-value
alternative to the canvas. No OHLC candles are fabricated, no missing fills are
inferred, and connecting lines do not assert an intrabar path.

Chart pixels alone use approximate Number values and Date's millisecond timestamp
projection. Non-finite/underflow or duplicate series coordinates are omitted and
counted. All exact observations, including duplicate times, remain in the table;
original source records preserve full timestamps and Decimal strings. No fictitious
sub-timestamps are introduced to satisfy the chart library's increasing-time rule.

The offline source is explicitly selected and prominently labeled OFFLINE DEMO.
Its quote/decision/fill records come from real deterministic Python test services,
not hand-authored financial totals. A fixed operation clock makes regeneration
repeatable. It is never substituted for connected reports or described as live.

Phase 21 lacks standalone historical market-data retrieval, retained backtest input
bars, paginated paper-event retrieval and a unified debugger-event endpoint. A
future production contract would need authenticated bounded queries keyed by
source/session/run and time/cursor; exact price basis, instrument and dataset
version digest; immutable event identity, logical sequence, causation, occurrence/
availability/delivery/effective timestamps; strategy/version/digest; and only
recorded execution/protective/risk facts. This is a documented gap, not a new route
or a Phase 23 implementation.

## G. Security and financial correctness

- Tokens remain in React/client memory, clear on Disconnect/reload, and are never
  logged or placed in browser persistent storage. Requests use same-origin fixed
  paths, omit browser cookies, refuse redirects and disable response caching.
- Backend roles remain authoritative. Reader UI actions cannot submit/execute/
  cancel; an Operator preference does not elevate a reader credential.
- Financial labels use original Decimal strings. Grouping does not round or compute
  totals; missing is distinct from zero. Fractional/exponent numeric tokens,
  duplicate keys and excessive nesting are rejected. Requests reject unsafe
  integer literals; response views preserve arbitrary-size provenance integers
  as exact text. Request UTF-8 byte limits use advertised bounds.
- UTC display retains fractional source digits. Visual time coordinates are a
  documented approximation, with original causal timestamps available in details.
- Portfolio stale/missing status suppresses current equity, P&L and exposure.
  Paper equity is explicitly a recorded historical mark. Backend summary determines
  incomplete/open position status; the browser does not calculate closed outcomes.
- Canonical result JSON SHA-256 must match both response and selected status before
  metrics render, and metrics are parsed from those verified canonical bytes.
  Result downloads preserve canonical bytes; general record JSON downloads use
  presentation serialization, including text for large provenance integers.
- Errors show fixed client messages and bounded sanitized correlation IDs, never
  raw server response messages, private tracebacks or credential/payload logs.
- React text rendering escapes untrusted content. No HTML injection, arbitrary
  filesystem access, upload, provider inference, direct order entry, automatic
  approval or unsupported mutation control exists.

This is a trusted local bearer interface to one backend namespace. It supplies no
new production tenancy, credential signing, hardened web deployment, durable job
worker or security guarantee beyond Phase 21's documented scope. Readers can
download sensitive returned research/account records locally; they should treat
those artifacts according to their existing data policies.

## H. Exact verification

Verification environment: Windows, Node 22.16.0; temporary Python 3.13.2 environment
under ignored `tmp/phase22-venv`, Pydantic 2.14.0, FastAPI 0.143.0, pytest 9.1.1.
The original `.venv` pointed to a missing Python 3.11 interpreter. No repository
Python requirement was changed. Initial native-Rollup startup failure was resolved
with the official WASM override; an initial debugger assertion was corrected to
respect actual duplicate quote times, with complete exact-table retention added.

| Check | Result |
| --- | --- |
| Affected backend: `python -m pytest tests/api tests/mcp/test_operations.py tests/orchestration/test_architecture.py -q` | 160 passed in 20.77 s, before the new artifact tests |
| New artifact checks: `python -m pytest tests/api/test_frontend_contract.py -q` | 3 passed in 3.39 s |
| Frontend final `npm.cmd test` | 62 passed across 4 files; 11.93 s total |
| Production `npm.cmd run build` | TypeScript check and Vite build passed; 47 modules; 3.55 s Vite build |
| `npm.cmd run contracts:check` | Generated types match checked-in OpenAPI |
| `npm.cmd audit` | 0 vulnerabilities |
| `npm.cmd ls --all` and `npm.cmd ls --depth=0` | Exit 0; no invalid required dependencies. Optional packages for other OSes/unused features are absent as expected. |
| `npm.cmd ci --dry-run --ignore-scripts --no-audit --no-fund` | Exit 0; lockfile supports clean installation |
| `python -m pip check` | No broken requirements found |
| Full `python -m pytest -q` | 4,077 passed in 983.93 s (16:23); no failures, skips or xfails; single full run |
| Local preview HTTP readiness | HTTP 200 at `127.0.0.1:5173` |
| `git diff --check` and untracked-file `git diff --no-index --check` | Passed; no whitespace errors |

Frontend tests cover actual endpoint inventory, role/auth states, strict Decimal
transport/formatting, duplicate/unsafe JSON, in-memory credentials, request bounds,
UTC precision, empty/error/loading states, stale/missing valuation and operator
gates, explicit job lifecycle, validation invalidation, canonical digest failure,
journal filter/cursor behavior and page bounds, accessible navigation/draft
retention, chart controls/event selection/source provenance, complete exact tables
and absence of unsupported/fabricated execution controls/records.

The full Python suite runs once after implementation stabilization, including
the new API-artifact tests. Subsequent frontend presentation/transport assertions
do not alter backend code or Python fixtures; no repeated full run is justified.

**Browser visual QA limitation:** the browser-control entry point timed out before
opening the local page. No screenshot, real canvas interaction, desktop/mobile
visual review or browser frame-rate claim is made. jsdom tests mock the canvas
library and separately verify wrapper interaction/lifecycle behavior. Responsive
CSS and accessible component checks pass; browser layout/canvas QA remains a
review step for this environment. No broad Phase 23 end-to-end flow was added.

## I. Performance observations

`npm.cmd run measure` loads the actual debugger adapter/components through Vite
without starting a public server. Warm medians of five samples after one warm-up:

| Source records | Transform median | Bounded SSR render median | Rendered HTML |
| --- | --- | --- | --- |
| 4 actual demo records | 0.05 ms | 5.87 ms | 4.6 KiB |
| 2,001 synthetic scaling records | 5.94 ms | 17.89 ms | 7.2 KiB |

The scaling input repeats actual fixture records solely for CPU measurement; it is
not a financial scenario or UI data source. SSR excludes canvas/effects/network
and does not measure end-user responsiveness. CPU data suggests bounded rendering
cost; it does not establish browser frame rate or production latency.

Paper, debugger-event and exact-observation views render 20 rows at a time.
Journal HTTP pages are bounded to 20 and cursor navigation to 100 pages. Inspected
operation metadata uses the API's maximum identity count. Transforms/record text
are memoized; result/detail previews cap display at 24,000 characters, with full
downloads available. The initial bundle excludes chart code and offline data.
Final production chunks: initial JS 259.94 kB / 81.13 kB gzip; CSS 10.26 kB /
3.11 kB gzip; lazy debugger JS 177.22 kB / 57.78 kB gzip; offline fixture JS
37.50 kB / 4.48 kB gzip; HTML 0.57 kB / 0.35 kB gzip. The Vite production build
took 3.55 s with 47 modules and no chunk-size warning. These are build observations,
not browser download/interaction timings.

## J. Limitations

- Typed JSON research inputs and local strategy labels are functional first-pass
  interfaces, not graphical strategy/rule builders or persisted specification IDs.
- Approved specifications remain in trusted Python review; validation is not
  approval. No approval button or mutation endpoint is invented.
- Text research input is session-only. No image uploads, provider calls, inference
  or persistence are claimed.
- No order/account/session/portfolio mutation, HTTP recovery, journal note/import
  write, durable/background job, live broker control or provider dataset download.
- Notes and standalone research-link queries are not exposed by HTTP. Only returned
  references and imported-operation links are shown.
- Paper records arrive in a bounded snapshot; there is no production event-paging
  or standalone market-data/backtest-input chart contract.
- No automatic refresh/execution. A changed reporting catalogue requires reconnect;
  operation history in the UI clears on disconnect/reload and the API loses jobs
  on restart.
- The Vite local proxy is development-only. Production hosting/auth/TLS/reverse-proxy
  deployment remains a later explicitly scoped action.
- Browser visual/canvas QA is unverified because the available control tool timed
  out. No claim that responsive layout or graphical behavior was visually reviewed.

## K. Git status and commit readiness

Branch remains `master`, HEAD remains the supplied baseline. Changes are unstaged
and new frontend/report/test files are untracked; no commit or push occurred.
All automated checks/builds passed. The increment is ready for code review with
browser visual/canvas QA explicitly unverified. There are 38 changed source files:
4 modified existing files, 32 new frontend files, the new report and new Python test.
Dependencies, build outputs, secrets, databases and the temporary Python environment
are ignored. Stop after verification and reporting; no Phase 23 or publication.
