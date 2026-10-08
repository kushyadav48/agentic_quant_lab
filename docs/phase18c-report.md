# Phase 18C implementation report

Verified on 2026-10-08. The initial branch was master with a clean working tree.
Phase 18B was confirmed committed at dbf1b86 (deterministic paper account and
position accounting), following Phase 18A at 67117c7. No commit or push was made.

## A. Exact files created and modified

Modified:

- README.md
- docs/architecture.md
- docs/roadmap.md
- src/quantlab/paper/__init__.py
- src/quantlab/paper/accounts.py
- src/quantlab/paper/models.py

Created:

- docs/paper-strategy-runtime.md
- docs/phase18c-report.md
- src/quantlab/paper/admission.py
- src/quantlab/paper/runtime.py
- src/quantlab/paper/strategy_models.py
- src/quantlab/paper/strategy_orders.py
- tests/paper/strategy_helpers.py
- tests/paper/test_strategy_admission.py
- tests/paper/test_strategy_integrity.py
- tests/paper/test_strategy_orders.py
- tests/paper/test_strategy_opening_transactions.py
- tests/paper/test_strategy_runtime.py

Ignored local verification scripts/logs are under tmp/; they are not project
source, dependency or configuration changes. No existing test file was modified.

## B. Admission architecture

admit_strategy returns an immutable content-addressed accepted/denied record.
StrategyRuntime construction rechecks that boundary; an admission record cannot
activate a runtime by itself. Phase 5 approval must bind the exact requested
strategy ID, version and digest. Separate immutable policy and eligibility records
bind that identity, reviewed research references and dataset versions. Drafts,
invalidated approvals, mismatched identities and incompatible account/strategy
intent fail closed with stable reasons. Existing Phase 7 approval/intent checks
and Phase 6 feature declaration validation are reused.

## C. Research eligibility verification

EligibilityPolicy has explicit versioned evidence requirements and financial
thresholds; no threshold is silently selected. Explicit None disables a financial
metric requirement. Admission verifies previously produced BacktestResult,
HoldoutResult/WalkForwardReport and RobustnessReport artifacts with separate
verification status, verifier, request/result digests and dataset version references.
It checks strategy/result/analytics associations, chronological OOS membership,
fold plans, approved robustness baseline/variant structure, costs and risk settings.
Verification precedes eligibility and activation; the verifier differs from the
eligibility reviewer. Duplicate result evidence cannot inflate observation counts.
No research replay or model inference occurs during activation.

The existing report contracts do not embed authenticated dataset provenance.
The new read-only evidence envelopes retain explicit trusted application
attestations; they do not authenticate reviewers or independently recompute numeric
research results. Missing evidence is rejected rather than inferred.

## D. Causal runtime behavior

Only explicitly delivered canonical completed bars enter evaluation. Availability,
delivery, bar-series chronology and logical sequence checks precede publication.
Existing RuleEvaluator implements TRUE/FALSE/UNAVAILABLE, comparisons, groups,
offsets and crossings. Late bars cannot emit retrospective signals; future data
cannot change retained decisions. Raw OHLC feature aliases use the existing
single-bar pipeline; external feature overrides and batch/ML delivery are rejected.
Exactly one attributed entry intent may be emitted. Dependencies and feature
provenance remain in immutable decision/snapshot records. Indexed dependencies and
a fixed-length read-only bar view avoid repeated history copying.

## E. Paper order/account integration

StrategyOrderAdapter accepts only its runtime's retained intent and an actual,
on-time close quote. Deterministic submission identity binds admission/policy,
account/session, strategy version/digest and decision causation. PaperAccount's
private batch stages the quote, existing risk/order acceptance and shared-policy
reservation before one publication. Funding/preparation failures leave state and
indexes unchanged.

An explicit OpeningDelivery has no completed OHLC. It must identify the retained
source close and the adjacent next opening, with matching timeframe/instrument,
on-time delivery/availability and a genuine locked quote equal to opening_price.
This preserves BAR_CLOSE -> NEXT_BAR_OPEN, including equal timestamps ordered by
sequence. It never turns an arbitrary later quote into a bar opening. Existing
account-owned pre-fill risk, quote pricing and atomic fill/settlement remain the
execution authority. Funding gaps cancel and release reservations atomically.
Standalone Phase 18A behavior is preserved.

The independent audit found one HIGH defect: process_open previously committed the
owned order/account result before allocating an adapter-local opening acknowledgement.
An allocation error could therefore leave a fill or cancellation committed, lose its
opening record and make exact retry fail at the terminal guard. Two focused regression
cases reproduced this before the correction, for a charged fill and unfunded cancellation.

The correction removes the adapter-local acknowledgement index. PaperAccount prepares
an immutable acknowledgement containing the original opening, canonical payload,
derived kernel delivery and exact result tuple. Canonical checks, acknowledgement
construction, retry-index allocation and candidate publication allocation all precede
publication. Its immutable publication now selects account state, owned kernel state
and opening acknowledgements together. Financial journal/index staging retains the
existing rollback boundary; the final single pointer swap makes all public results
visible together. No acknowledgement allocation or indexing follows that swap.
Exact retries read the same result tuple from the owner before terminal or late-input
guards. Any preparation/staging/publication error preserves or restores prior state
and leaves the input safely retryable.

The correction changes only accounts.py, strategy_orders.py, the runtime documentation
and this report, and adds test_strategy_opening_transactions.py. Existing public APIs,
immutable financial contracts, admission, timing, pricing and risk decisions retain
their behavior. No dependency or Phase 18D–18F capability is added.

## F. Safety and authority boundaries

No LLM calls, network requests, broker operations, sleeps, workers, API endpoints
or MCP trading tools were added. Agents cannot inject risk approval, approved
strategy records, fills, quantity/direction overrides or mutable features through
this adapter. Accounts and runtime records remain strict/frozen; duplicate inputs
replay their original outcomes and conflicting identities fail explicitly. New
external reservations or changed account exposure invalidate exclusive execution.
Trusted local Python owners are required; arbitrary private-object mutation,
reflection and authenticated remote authorization are outside this phase.

## G. Exact verification results

The correction's required checks ran without skips or xfails. Core regression groups
used the existing repository-local Windows Python; orchestration/MCP and the full
suite used the existing Ubuntu WSL Python 3.11 environment.

| Check | Environment | Result |
| --- | --- | --- |
| Focused opening transaction tests | Windows | 20 passed in 2.05s |
| All Phase 18C tests | Windows | 172 passed in 10.75s |
| Existing Phase 18A and 18B tests | Windows | 298 passed in 3.51s |
| Backtesting and risk tests | Windows | 468 passed in 2.54s |
| Orchestration and MCP tests | WSL | 753 passed in 147.73s |
| Full pytest suite | WSL | 2,943 passed in 190.16s |
| git diff --check and new-file whitespace checks | Windows | Passed |

The full suite contains 2,771 existing tests and 172 Phase 18C tests, including 20
new transaction cases. The focused tests cover acknowledgement allocation and
serialization, opening retry-index allocation, financial index staging, candidate
publication allocation and pointer failures before/after assignment. Both charged
fills and unfunded cancellation/releases are checked at each failure point.

After failure, exact equality is asserted for account/order state, public audit
snapshots, journals, reservations, positions, fees and all financial/kernel/opening
idempotency indexes; the publication pointer remains the original object. The same
input then succeeds, an exact retry returns the identical retained result tuple,
and conflicting content is rejected without further changes. Independent replay
produces identical execution/accounting/opening records and canonical audit output.
Provenance checks bind the retained original opening and its quote/sequence to the
kernel delivery, risk record, fill/position or cancellation/release. Separate tests
verify old terminal accounting adapters preserve the opening acknowledgement.

Windows .venv points to a missing Python installation. Host Application Control
blocks rpds imports in the repository-local Windows interpreter; the existing WSL
environment supplies the full dependency set without exclusions or dependency changes.
The full-suite command was:

~~~powershell
wsl.exe -d Ubuntu --cd /mnt/c/Users/KUSH/OneDrive/Desktop/agentic_quant_lab -- /home/kush/venvs/agentic-quant-lab/bin/python -B -m pytest -q -p no:cacheprovider
~~~

## H. Performance observations

An offline WSL measurement prebuilt 1,000 observations, then measured process()
only, excluding fixture research/initialization and snapshot materialization.
Three runs per case gave:

| Rule offset | Median for 1,000 events | Median mean per event |
| --- | --- | --- |
| 0 | 0.358414 seconds | 358.4 microseconds |
| 100 | 0.383898 seconds | 383.9 microseconds |

These are local observations, not a latency guarantee. Financial results never
use wall-clock timing. A 250-event work-bound test checks one-bar feature calls
and no full snapshot serialization during processing; a 150-event offset-100
check validates indexed historical dependencies without copying the full journal.
Events are bounded to at most 10,000 and feature/parameter width to 32.

## I. Limitations and remaining risks

- Prefunded linear EQUITY research units, one exact instrument, MID bars, one
  exclusive strategy/account adapter and one entry only.
- Only causal raw OHLC feature aliases; batch indicators and ML delivery require
  a future provenance-preserving causal adapter.
- Actual on-time adjacent locked opening quotes and a separately delivered close
  quote are required. Unlocked/gapped/delayed openings and arbitrary quotes cannot
  execute an entry. No calendar or feed/session service is supplied.
- A missing opening leaves an accepted entry unfilled with its reservation retained;
  there is no automatic expiry, replay recovery or session cancellation service.
- No executable exits, stops/limits/partials, reversals, pyramiding, allocation,
  rebalancing, database recovery, real broker connection, dashboard or API.
- Approval and evidence verification are immutable local attestations, not signed
  authentication or an external live revocation registry. Source numeric provenance
  must be verified and retained by the trusted application before activation.
- Serialized in-memory ownership only; durable crash recovery and distributed or
  concurrent execution remain outside scope.

Phase 18D must supply genuine causal close/open observations and session/calendar
lifecycle. Phase 18E persistence/recovery and Phase 18F advanced matching remain
planned. See paper-strategy-runtime.md for the complete supported contract.

## J. Git status and diff summary

HEAD remains dbf1b86 on master. All changes remain unstaged and uncommitted:
6 modified tracked files and 12 new files. No dependency was added; pyproject.toml
and existing tests, MCP/orchestration surfaces and other quant engines are unchanged.

Tracked-file diff: 209 insertions and 31 deletions. Including all new files and
this report: 2748 insertions and 31 deletions across 18 files.
New-file whitespace checks reported no whitespace errors. No commit or push.
