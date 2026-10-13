# Phase 22 — Workstation UI refinement

13 October 2026. Continued from the existing uncommitted `master` working tree,
HEAD `687e018132b4808ecddb0a0973bc9a2b73d4d05e`. All three P2 corrections and
the P3 correction from the focused audit remain intact. No backend implementation,
dependency/lockfile, commit, push or Phase 23 change.

## Implemented

The React workstation now uses compact terminal chrome, SVG navigation icons,
an environment/status strip, blue chart accents, tabular numeric typography,
denser bordered panels, responsive layouts and explicit data-source labels.
Disconnected pages explain the next action instead of implying an account exists.
Connected catalogues without registered owners offer an explicit environment
switch; they never fill themselves with synthetic balances.

- **Portfolio:** balance/equity/exposure/realized metrics, selected registered
  paper-account history, static allocation bars, valuation/risk counts, recent
  committed activity, and an instrument/member/strategy table with exact values.
- **Paper:** recorded equity chart, exact mark selector, position summary,
  recorded order status, protective/OCO details and paged activity.
- **Research:** recorded/inspected identity counts, compact operation cards,
  populated demo backtest example and preserved explicit API execution semantics.
- **Strategy:** content editor, validation/provenance review steps and editable
  demo example with restore. Recorded example validation never implies approval.
- **Results:** chart only when a retained equity series exists, bounded ML output
  tables, canonical-derived metrics, digest verification and original download.
- **Journal:** existing filters, UTC bounds, pages, summaries and links retained;
  offline fixture paging is explicitly distinguished from API paging.
- **Debugger:** taller price/fill chart, denser causal panels and explicit
  synthetic/API/offline source labels. Audited fill-identity hit testing is preserved.

The new SVG equity charts use only recorded account/result marks. All-record,
last-20 and last-10 windows, mouse mark selection and native keyboard-accessible
exact selectors operate on existing points. Connecting lines are visual guides.
Selected paper history is explicitly **not aggregate portfolio equity**. API v1
does not expose that aggregate history; missing history creates a meaningful
empty state. No fabricated candles, prices, fill attribution or P&L calculation.

## Separate deterministic synthetic demo

Choose **Explore synthetic demo** at `http://127.0.0.1:5173/` to explore all seven
pages. The persistent amber banner states that the records are illustrative and
never real financial results. Sidebar, toolbar, page and debugger source labels
identify the environment. Demo remains a read-only reader interface.

`frontend/scripts/export_demo.py` uses existing Python paper, portfolio, journal,
research and validation services with synthetic TEST inputs, an in-memory journal
and a fixed research clock. It produces three sessions, 84 committed records, one
portfolio and all nine completed research artifact kinds. Python owns all balances,
marks, deltas, risk facts, timestamps, IDs and original canonical result bytes.

The separate `DemoClient` cannot call the API, mutate jobs or fall through to
inherited network methods, even if its interface role is changed. It returns copies
of fixed records and rejects unknown IDs. Edited strategy content cannot receive
invented validation. Local journal filtering uses original attribution/UTC bounds;
fixture-bound cursors cannot cross filters or be confused with backend cursors.

The fixture is loaded lazily only after explicit mode selection. Exact immutable
config/source references and result bytes are stored once and restored by lookup,
without browser calculations. Its final JSON is 470,683 bytes; regenerating it
produced identical bytes. Canonical SHA-256 verification passes for all nine kinds,
including the 77-digit ML provenance integer corrected in the audit.

Entering demo clears credentials, drafts and API operation metadata; exiting demo
returns to a disconnected state. Pending authentication is aborted and late API
callbacks cannot replace demo state. There is no automatic fixture fallback or
restoration of an old authenticated connection. Static local asset loading is
distinct from API/service requests.

## Preserved guarantees

Tokens remain memory-only. Server authorization and reader/operator guards remain
authoritative. Decimal labels are original strings with grouping only; UTC readouts
retain source fractional precision. Numeric conversions change chart/allocation
pixels only, never monetary labels or accounting. Stale/missing valuation suppression,
historical paper labels, safe error messages and escaped research rendering remain.

Displayed result metrics/details still come from the bytes whose digest was
verified, and canonical downloads preserve original bytes. Large provenance integers
remain exact text in JavaScript views. No approval, broker order, accounting mutation,
HTTP recovery or trading authority was added.

## Exact verification

| Check | Final result |
| --- | --- |
| `npm.cmd test` | **83 passed**, 5 files, **15.92 s**; all 69 audit-era tests preserved plus 14 demo/chart tests |
| `npm.cmd run build` | TypeScript and Vite passed; **50 modules**, **3.00 s** Vite build; no chunk-size warning |
| `npm.cmd run contracts:check` | Generated TypeScript matches unchanged checked-in Phase 21 OpenAPI |
| Fixture regeneration | Byte-for-byte deterministic; same three sessions, 84 records and nine artifacts |
| Existing local preview | HTTP **200** at `http://127.0.0.1:5173/`; existing server preserved |
| Git/whitespace | No staged changes; CRLF-aware whitespace checks passed |

New tests cover no API/storage use in demo; mutation/network rejection; all nine
digests and rendered result kinds; exact large integers; copied data and unknown
identities; complete paging without duplicates; cursor/filter binding; UTC boundaries
and attribution; recorded validation; all seven pages; explicit source switching;
late authentication responses; chart windows, keyboard activation, exact Decimal/
timestamp readouts and absent history. The debugger canvas remains mocked in
component tests; SVG marks/selectors are tested as DOM components.

Backend implementation was unchanged, so the Python suite and benchmarks were
not rerun. The exporter invokes existing services to generate fixtures; that is
not a backend test-suite run. Dependency inputs were unchanged.

Production observations: initial JS 282.01 kB / 87.48 kB gzip; CSS 29.72 kB /
7.29 kB gzip; lazy debugger 175.55 kB / 57.19 kB gzip; lazy demo fixture 474.96 kB /
44.32 kB gzip. Demo data and chart-library code are excluded from the initial JS.
These are build sizes, not browser performance measurements.

## Remaining browser QA

This session's computer-use inventory returned `{"apps":[],"browsers":[]}`.
HTTP readiness and jsdom do not verify layout, contrast or real canvas behavior.
No screenshot or visual/browser sign-off is claimed for the refined version.

- Review all seven pages at 360, 768, 1440 and 1920 px: panel density, table/editor
  scrolling, sidebar/menu, long Decimal/digest wrapping, axes and contrast.
- Enter/exit demo, connect reader/operator credentials, and switch from an empty
  API catalogue to demo. Confirm persistent source labels, cleared drafts/token,
  no `/api` demo requests and no synthetic fallback in connected pages.
- Exercise mouse and keyboard chart windows/exact selectors; real debugger zoom,
  pan, fit and coincident fill selection. Check source timestamps and tooltip/readout
  precision against original records.
- Use only Tab/Shift+Tab, Enter/Space and native select keys for navigation, editors,
  details, paging and mark selection. Verify visible focus and no keyboard traps.
- Inspect every result kind and canonical download. Exercise empty history/catalogues,
  stale/missing marks, invalid auth, busy/unreachable API, cursor rejection and
  digest mismatch. No failed/queued job should appear completed.

Code and automated validation are ready for review. **Visual release sign-off
remains pending these browser checks.** Existing local services were not restarted
or stopped during refinement.

## Working tree

Branch and HEAD are unchanged. Four tracked files remain modified, and 41
source/document files are untracked: **45 files total**, all unstaged. The previous
reports and API artifact test are preserved. The new report, demo generator/client/
fixture/test, and equity-chart component account for the six additional files.
Ignored environments, dependencies, builds and temporary files remain excluded.
No commit or push.
