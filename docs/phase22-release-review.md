# Phase 22 — Final frontend integration and release-readiness review

Review date: 13 October 2026. Branch `master`; baseline/HEAD
`687e018132b4808ecddb0a0973bc9a2b73d4d05e`. Existing uncommitted Phase 22 work
was preserved. No frontend redesign, backend implementation changes, Phase 23,
commit or push.

This audit predates the user-requested workstation refinement. Its four fixes
remain preserved. See the subsequent [UI refinement report](phase22-ui-refinement.md)
for the current design, isolated synthetic demo, test/build evidence and Git status.

## A. Verdict — BLOCKED

Automated frontend, production-build and real client/API checks pass after four
small corrections. Release readiness remains **BLOCKED by browser QA**: no real
browser layout, canvas, responsive or keyboard verification was possible.
No unresolved code defect was confirmed in this focused review.

## B. Severity-ranked findings

| Severity | Finding | Outcome |
| --- | --- | --- |
| Release gate | Browser functionality and visual usability remain unverified. Browser inventory was empty and in-app browser creation was unavailable. | Open; complete checklist in D before release sign-off. |
| P2 | Valid `ml_prediction_features` output was rejected by the transport parser. Python encodes `model_digest` as an arbitrary-size integer in `FeatureParameter.value`; the real fixture contains a 77-digit integer. | Fixed; large response integers retain exact text instead of becoming rounded JS numbers. Request admission stays strict. |
| P2 | Result hash verification covered `result_json`, but displayed metrics/details came from the independently supplied `result` projection. A mismatched projection could appear under a verified badge. | Fixed; every displayed result metric/detail now comes from the verified canonical bytes. Original canonical download remains unchanged. |
| P2 | Chart clicks selected the first event sharing the timestamp, so an actual fill could select an earlier decision/session event. | Fixed; hit-test actual recorded points by time and price, then resolve their source identity. Coincident fill points take precedence over observations. |
| P3 | Backend `unauthenticated` and `invalid_request` codes lacked the intended safe frontend explanations. | Fixed; map actual contract codes while retaining sanitized messages and correlation IDs. |

No observed live-trading or unauthorized accounting mutation was involved in
these defects. The provenance finding concerns what the verified UI actually
displays; it does not bypass backend authentication or SHA-256 verification.

## C. Corrections and regression evidence

Changed implementation files: `frontend/src/api/client.ts`,
`frontend/src/pages/Research.tsx`, `frontend/src/debugger/Chart.tsx`.
Regressions were added to the existing API, screen and chart test files.
Documentation now distinguishes strict requests from exact large-integer
response views.

Failures were reproduced before the corresponding fixes: wrong fill identity,
unbound displayed metric, both backend error explanations, and actual ML
provenance-integer rejection. Additional checks cover negative/multiple large
integers, unchanged strict financial/request validation, duplicate keys, canonical
verification with a large integer, and malicious research markup rendered as
literal text with no image element.

| Final check | Evidence |
| --- | --- |
| `npm.cmd test` | **69 passed**, 4 files, 11.62 s |
| `npm.cmd run build` | TypeScript and production Vite build passed; 47 modules; 2.21 s Vite build |
| `npm.cmd run contracts:check` | Generated TypeScript matches checked-in Phase 21 OpenAPI |
| Real HTTP/client/proxy integration | All checks described in E/F passed; frontend HTML returned HTTP 200; authenticated running API OpenAPI exactly matched the checked-in schema |
| Whitespace and Git state | Checked before reporting; no whitespace defects or staged changes |

Backend code was unchanged, so no backend tests or benchmarks were repeated.
The earlier **4,077 Python tests passed** and **npm audit: 0 vulnerabilities**
remain implementation evidence, not newly executed review checks. Dependencies
and lockfile were unchanged by this review.

## D. Browser evidence and manual QA checklist

Computer-use inventory returned `{"apps":[],"browsers":[]}`. Attempting
`cua.createBrowserTab('iab', 'http://127.0.0.1:5173/', {visible:true})` returned
**`Browser is not available: iab`**. No page was opened and no screenshot or
actual canvas interaction was captured. jsdom and mocked chart tests are not
browser verification.

Complete this checklist in a real browser using synthetic registered API
fixtures. Record browser/version, viewport, outcome and screenshots for failures.

- [ ] Navigate all seven screens: portfolio/dashboard, research, strategy,
  results, paper sessions, journal and debugger. Exercise Back/Forward, retained
  drafts, disconnect and reload. Confirm credentials/drafts clear as documented.
- [ ] Connect with reader/operator and invalid tokens. Reader submit/execute/
  cancel controls stay disabled; choosing Operator with a reader token still
  receives server 403. No approval, resume, order-entry or live-trading control.
- [ ] Validate/edit a strategy draft; validation invalidates on edits and never
  implies approval. Submit, inspect, execute and cancel queued research; verify
  truthful failed/completed states and all nine result kinds, including ML features.
- [ ] Inspect result metrics and canonical download; verify SHA-256 independently.
  Confirm long exact decimal/provenance values remain readable without clipping.
- [ ] Inspect paper/portfolio labels, UTC times, missing/stale valuations and
  recovery warnings. Missing data must not look like a zero balance or current mark.
- [ ] Page journal trades/research forward/back using backend cursors; apply new
  filters and UTC boundaries. Check empty history and open/incomplete summaries.
- [ ] Select actual debugger quotes/fills and timeline events, including a fill
  sharing a decision timestamp. Verify selected IDs, causal context and original
  UTC timestamps; exercise real mouse zoom/pan plus toolbar zoom/pan/fit.
- [ ] Inspect injected stop/target/partial-fill OCO sessions. Only actual fills
  create entry/exit markers; sibling cancellation/revisions must not create fills.
  Empty price history must show no invented candles. Offline source is visibly
  OFFLINE DEMO and never substitutes for a connected report.
- [ ] Check 360, 768 and 1440 px widths: menu, headings, editors, tables, long
  digests, notices and chart. Confirm readable contrast and no inaccessible overflow.
- [ ] Use only Tab/Shift+Tab, Enter/Space and native select keys: skip link,
  navigation, menu toggle, dialogs/details, forms, paging and debugger controls.
  Confirm visible focus, logical order and no keyboard trap.
- [ ] Stop the fixture API and retry read/execute requests; inspect loading,
  unreachable, rejected-cursor, 404, digest-mismatch and empty-catalogue states.
  Disconnected execution must not be described as cancelled or successful.

## E. Frontend/API contract assessment — PASS within tested scope

The actual frontend `APIClient` was loaded through Vite and exercised over HTTP
through its proxy into `create_app` with existing Python services. The review
used an in-memory journal and synthetic fixtures from `tests/api/test_reporting.py`,
`tests/mcp/research_helpers.py`, and existing paper/OCO helpers. No private journal,
provider data or user credential was loaded.

Existing ports 8000/5173 were occupied. Their processes were preserved; review-owned
services used loopback 8001/5174 with the same Vite configuration apart from port
and proxy target. The review-owned processes were stopped after verification.

Verified: actual OpenAPI equality; reader strategy validation; reader rejection of
all three operator operations even with an Operator UI preference; invalid auth;
all nine research queue/admit, idempotent replay, explicit execution, causal audit,
reader status/result and canonical digest paths; queued cancellation; failed
unapproved research and unavailable results. Submit never executes implicitly.
Execute/cancel use the contract's bodyless POSTs.

Paper, portfolio and session-summary reads passed. Four real one-record journal
cursor pages matched every original record ID in order, without duplicates or
omissions. Changed-filter cursor use was rejected; research history, unknown IDs,
empty filters and 404 paths passed. Component tests cover loading, empty/error
presentation and cursor resets. No unsupported backend action was added or claimed.

## F. Financial correctness and security — PASS within tested scope

- Real backtest output preserved `1000.12345678901234567890123456789` exactly.
  Decimal strings remain strings; formatting groups without rounding or browser
  P&L calculation. Original canonical bytes/digests remain verifiable.
- API audit/equity timestamps were UTC; display labels explicitly identify UTC
  and retain source fractional precision. Local datetime filters are labeled UTC.
- Missing/stale portfolio valuations suppress current equity, exposure and net
  P&L; paper equity is labeled as a recorded historical mark. Paper/offline labels
  are explicit. Python remains authoritative for risk, accounting and approval.
- Actual target, stop and target-then-stop Python fixtures each produced three
  recorded fill markers, including two partial exits. Adapter checks matched exact
  IDs, prices, quantities, actions and execution timestamps; OCO revisions created
  no duplicate markers. Missing observations create no candles/fills.
- Tokens are held in component/client memory; no persistence or logging path was
  found. Tests cover storage/log avoidance and disconnect behavior. Fixed same-origin
  routes omit cookies, refuse redirects and use no-store requests.
- Research markup is escaped as text. Safe error messages omit raw server/private
  payloads. Real server checks denied reader submission/execution/cancellation.
  No approval, live broker action, HTTP recovery or financial mutation was introduced.

These findings combine source inspection, component regressions and actual HTTP
authorization checks. Browser storage inspection and keyboard/visual behavior
remain part of the open browser gate in D.

## G. Known limitations

Browser QA remains open. Chart numbers/time coordinates are approximate pixels;
exact strings and original causal timestamps remain in tables/details. Duplicate
series times and unsupported numeric ranges are omitted with a count. Coincident
markers can be selected separately through the event timeline.

Large response provenance integers are text in the JavaScript view. Canonical
downloads retain their original integer tokens/bytes; presentation JSON may emit
text. Strict request editors still reject unsafe integer literals.

Phase 21 provides bounded paper snapshots, not standalone price-history/backtest
input retrieval or paper-event paging. Operations are process-local, execute
synchronously and require manual refresh; there is no durable worker or live
trading. Production same-origin hosting/TLS/proxy setup is outside this review.

## H. Git status and commit readiness

`master` and HEAD remain unchanged at the supplied baseline. All changes are
unstaged: four modified tracked files and 35 untracked source/document files,
**39 files total**, including this review. Backend implementation is unchanged;
the earlier new API artifact test is preserved. Ignored dependencies, builds,
temporary QA scripts/fixtures/environment are excluded.

**Ready for code review; release/commit sign-off is blocked pending browser QA.**
No commit or push was performed. Existing API/dev processes were preserved.
Stop after this report; no Phase 23 work.
