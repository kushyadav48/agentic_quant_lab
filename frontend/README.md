# Quant Lab research console · Phase 22

Phase 23 adds a connected Research panel for server-side Gemini interpretation.
Check cloud configuration, explicitly select/consent to non-sensitive text,
submit a stable AI identity, and separately execute it. Inspect the verified draft
or clarification artifact. Review/approval stays in trusted Python; the optional
AI job binding links an independently approved exact backtest request to that
proposal. All nine ordinary research kinds remain available. Readers can inspect
configuration/jobs/results but cannot submit or execute. Synthetic Demo remains
isolated and exposes no cloud controls. See
[cloud configuration, routes and review instructions](../docs/cloud-llm.md).
Never put a Gemini key in this frontend or a `VITE_` environment variable.

Local React + TypeScript + Vite workspace. Python owns quantitative results,
approval, risk, accounting, operation identities and immutable history.

## Start locally

Use Node 22.16 or newer and the installed Python project. Start the existing
backend on `127.0.0.1:8000` using the instructions in the repository README.
In a second PowerShell terminal:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open `http://127.0.0.1:5173`. Paste an existing API bearer credential into the
password input. Choose Reader to keep research commands disabled, or Operator
when using an operator credential. The server authorizes every request; the
selector does not grant a role. Credentials stay in memory and clear on reload
or Disconnect. No token belongs in a Vite environment variable.

Vite binds loopback and proxies only `/api` to the fixed loopback backend. No
backend CORS change is necessary. `npm.cmd run preview` serves the static build
without an API proxy: production hosting would need an explicitly configured
same-origin reverse proxy. Production deployment is outside this increment.

The default API process has no paper or portfolio owners. A trusted Python
application must supply `ApplicationServices` using `create_app(...,
services_factory=...)` to register those existing owners. The UI cannot create
them. `QUANTLAB_API_JOURNAL_PATH` enables the existing journal owner but does not
import history. See `docs/phase21-report.md` and `tests/api/test_reporting.py`.

The offline debugger works without a token. Select its **OFFLINE DEMO** source
explicitly. The fixture is synthetic deterministic Python test-service output;
it never substitutes for unavailable connected reports.

## Synthetic workstation demo

Choose **Explore synthetic demo** to open all seven pages without a credential.
A persistent amber **SYNTHETIC DEMO** banner, sidebar and source labels identify
the fixed fixture environment. It includes three Python-generated TEST sessions,
84 committed paper records, one portfolio, and verified artifacts for all nine
research kinds. These are illustrative synthetic records, never real financial
results. The bundled strategy example has recorded validation only; edited
content requires the connected API.

Demo is a separate read-only adapter. It never calls `/api`, submits/executes/
cancels research or calculates financial outcomes. Static fixture assets load
only after an explicit mode choice. Its journal filters/paging operate locally
on the recorded dataset and use fixture-bound cursors. Unknown identities fail
instead of falling through to an API request.

Switching from API to demo clears the credential, drafts and inspected operations;
**Exit demo** returns to a disconnected workspace. It never restores a previous
API session. Connected empty catalogues stay empty until the user explicitly
switches environments. Regenerate the fixture from the repository root:

```powershell
python frontend/scripts/export_demo.py
```

The generator uses existing deterministic Python services and an in-memory journal,
with a fixed research clock. Exact record/config references and canonical results
are deduplicated for size; the adapter restores them without calculating deltas.
See [the UI refinement report](../docs/phase22-ui-refinement.md) for verification.

## Pages and contracts

| Page | Supported workflow |
| --- | --- |
| Portfolio | Exact balances, selected paper-account equity history, allocation bars, valuation health, member/instrument table and provenance |
| Paper | Recorded equity chart with exact mark selection, positions/reservations, order/protective-exit records, feed/risk/recovery details |
| Research | All nine advertised kinds through strict JSON inputs: submit only, inspect ID, explicit execute, queued cancel and manual status refresh |
| Strategy | In-memory StrategyContent draft and local identity/version labels; backend validation issues/content digest; local text hypothesis editor |
| Results | Verified canonical metrics, retained equity series where present, ML output tables, provenance and original canonical download |
| Journal | Identity/version/digest/instrument/order/run filters, half-open UTC range, backend keyset pages of 5/10/20 records, summary and research references |
| Debugger | Recorded bid/ask and complete bar closes, actual fill dots, causal timeline selection, zoom/pan controls, exact table and provenance |

Strategy and operation drafts survive page navigation within the connected
session. They clear on Disconnect/reload. Inspected operation metadata is bounded
to the API's maximum identity count. No list-operations endpoint exists. A
restarted API loses its process-local operations; the UI does not claim durability.

All research kinds use the existing operation request contract rather than new
frontend financial logic. Obtain approved specifications and valid inputs from
the trusted Python research workflow. Empty JSON is deliberately not a runnable
example or a fabricated approval. Inspect `contracts/openapi.json` and the Phase
21 documentation for fields. Submission never executes automatically.

No HTTP approval, image upload, provider inference, order entry, session mutation,
recovery, portfolio mutation, note editing/import or live execution is supported.
Notes and standalone research links have no HTTP read route either. Original
references within returned records remain inspectable.

## Precision and chart evidence

Financial fields display authoritative Decimal strings with grouping only,
without rounding, totals or conversions through JavaScript Number. Missing is
distinct from zero. JSON admission rejects fractional/exponent numeric tokens,
duplicate keys and excessive nesting. Request editors reject unsafe integer
literals. Response views preserve large provenance integers as exact text;
canonical result downloads retain their original integer tokens and bytes.
UTC labels retain source fractional timestamp digits.

Chart coordinates and allocation-bar widths use approximate Number values;
chart time coordinates use Date's millisecond projection. These are pixels only,
never financial totals, P&L or execution calculations. Original timestamp precision
remains in exact mark readouts. Unsupported numeric ranges and duplicate debugger
series times are omitted with a visible count. The exact observation table and original
records preserve those observations, their source timestamps and Decimal strings.
Connecting lines do not assert an intrabar path. The debugger does not construct
OHLC candles, infer missing fills or attach execution prices to decisions.
Partial fills, risk decisions, stops/targets and OCO evidence appear only where
present in selected source records.

The production API lacks standalone historical market-data queries, retained
backtest inputs, paged paper events and a unified debugger-event schema. See the
Phase 22 report for the missing contract; this UI does not add those routes.

## Verification and contract updates

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run contracts:check
npm.cmd run measure
npm.cmd audit
npm.cmd ls --all
```

From the repository root with an installed development Python environment:

```powershell
python frontend/scripts/export_contracts.py
cd frontend
npm.cmd run contracts
```

The exporter uses credential-free synthetic test services, a fixed operation
clock, and the actual app's OpenAPI generator. Review changes to the schema,
generated types and fixture together. `tests/api/test_frontend_contract.py`
checks actual application-schema equality, real service fixture output and
financial/result serialization. The shared Phase 21 schema marks defaulted
JournalQuery fields required despite runtime admission accepting omissions;
the client models those known defaults explicitly as optional request fields.
Legacy serialization also omits optional empty OCO fields; the adapter tolerates
those omissions without creating records.

Rollup uses its official `@rollup/wasm-node` distribution via a pinned package
override because Windows application control blocked the native binary. This
also keeps installation portable; no security setting or vendored package is
modified. npm 10's resolver failed on optional peers during initial installation;
installation with npm 11.6.2 succeeded. The checked-in lockfile pins the tested
dependency graph; use `npm.cmd exec --yes --package=npm@11.6.2 -- npm ci` if an
older npm resolver fails.

Automated component tests use jsdom and a mocked canvas chart surface; they do
not establish browser layout or frame rate. The Phase 22 report records the
current browser QA limitation, exact test/build results and CPU observations.
The subsequent UI refinement has its own verification report; earlier CPU
measurements are historical and were not repeated.
