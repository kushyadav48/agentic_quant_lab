# Phase 18F bounded implementation report

Sections A–M preserve the original stage results; section N records the retained-history remediation. Section O records two-child protective OCO. Section P records durable advanced strategy entries and supersedes earlier unsupported-entry and incomplete-scope statements. Historical results below remain preserved. Section Q records the final Phase 18A–18F integration audit and supersedes prior readiness statements; performance measurements and limitations remain in section P.

## A. Actual baseline and workflow

Baseline: `504606b3c47dab589b56ebc4a6707492048bbf3f` on master, committed as
"feat: add durable paper session persistence and recovery". Git status was empty
before any project modification. Phase 18E was therefore confirmed committed and
master clean. The actual hash was read from Git, not inferred from documentation.
HEAD remains that hash. No commit or push was performed.

One implementation task was divided into contracts/matching, account integration,
position-linked sessions/recovery, regressions, profiling and final verification.
The shell helper could not start; local Node child processes ran Git and the existing
embedded CPython instead. No dependency was installed or account/service connected.

## B. Files created and modified

Created:

- src/quantlab/paper/advanced.py — shared matching/pricing/validation policies.
- tests/paper/test_advanced.py — advanced contracts, matching, quantities, financial fixtures, risk and atomicity.
- tests/persistence/test_advanced.py — v2 session, protective, recovery and fault-window regressions.
- tests/persistence/fixtures/phase18e-v1.json — frozen actual-baseline v1 journal/checkpoints.
- benchmarks/phase18f_advanced.py — latency/throughput, real SQLite recovery and CPU profiling.
- docs/paper-advanced-orders.md — supported policies and boundaries.
- docs/phase18f-report.md — this report.

Modified:

- README.md; docs/architecture.md; docs/roadmap.md.
- src/quantlab/paper/__init__.py; models.py; orders.py; account_models.py;
  accounting.py; accounts.py; session_models.py; sessions.py; strategy_models.py.
- src/quantlab/persistence/contracts.py; store.py; paper.py.

Existing test files were not weakened or edited. Pricing, risk, backtesting,
strategy/feature/ML engines and MCP/orchestration implementations were not replaced.
Ignored tmp files retain test logs, verification JSON, profiles, benchmark output,
the baseline archive and temporary fixture-export script/database.

## C. Advanced execution architecture

The existing PaperOrderKernel remains the matching/state owner. Explicit v2
configuration/submission types select the new policy; legacy types still reject
advanced parameters. PaperAccount owns continuation accounting, reservations,
position reduction and observation liquidity in one immutable publication root.
PaperSession constructs private candidate owners and stages the exit kernel and
required audit record. DurablePaperSession reuses the existing SQLite commit and
publication-uncertainty boundary. No second financial or persistence engine exists.

There is one active advanced account-owned order at a time, one instrument and one
owned position/strategy. A subsequent order can reduce residual exposure after the
previous order is terminal, subject to the existing 32-kernel retention limit.

## D. Lifecycle and matching policies

SUBMITTED, ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED and EXPIRED are
supported. Acceptance records a distinct activation. A stop-crossing quote records
another trigger identity; a later eligible delivery can execute. Same UTC time is
allowed when recorded sequence proves the later delivery. No OHLC trigger ordering
or fabricated opening is used.

MARKET fills at the first subsequent eligible observation. LIMIT BUY requires
ask <= limit and SELL requires bid >= limit; configured adverse slippage is capped
at the limit. STOP_MARKET triggers BUY at ask >= stop or SELL at bid <= stop and
fills only on a later eligible delivery. STOP_LIMIT activates first and applies
limit matching later; price gaps can leave it resting.

GTC rests within the recorded processing bound. IOC executes once on the first
eligible later quote and expires the remainder atomically, including a nonmarketable
limit. Stale observations do not consume an IOC opportunity. DAY/GTD/FOK and IOC
stops are explicitly unsupported. V1 cancellation still acknowledges immediately;
v2 requests remain pending/executable until acknowledgement. Recorded fill/ack
sequence decides the race. Exact retries return original records; identity conflicts
fail without financial publication, and committed fills are never erased.

## E. Partial-fill accounting and financial evidence

An explicit increment-aligned simulated per-observation budget enables partial
fills. Ordinary MarketQuote provides no trustworthy size. Without a budget the
existing declared full-fill assumption remains. Budgets are shared across account
orders and recovered sequential children, bound to the canonical quote. Redelivery
cannot replenish them, and a consumed quote cannot switch to a different budget or
the full-fill assumption. Independent account/session authorities remain isolated.

Fills record original submission, executed/cumulative/remaining quantities, actual
pricing/fees, activation/trigger IDs and liquidity policy. Each settlement updates
quantity/fees once. Commission is per unit; fixed fees are per execution. Order-level
fixed fees are not implemented. Observed spread is not debited twice.

Entry reservations conservatively include the maximum number of increment-sized
fixed-fee executions. Partial fills consume only actual basis collateral and explicit
fees. Remaining collateral stays held until completion/cancellation; IOC expiry
releases its remainder in the same settlement. The current eligible quote marks
existing exposure for the next entry-risk evaluation, so a newly known loss cannot
be hidden by a prior settlement valuation. Funding/risk denials preserve earlier
fills and release only the remaining hold.

Hand-calculated fixture: capital 1000, BUY 2 at ask 102/bid 100, budget 1,
commission 0.1/unit and fixed fee 1/execution. Reservation is 206.2. After the first
fill: quantity 1, fees 1.1, collateral 102, remaining hold 103.1, available 793.8,
equity 996.9. After the second: quantity 2, fees 2.2, collateral 204, no hold,
available 793.8, equity 993.8. Cancellation/IOC after the first leaves quantity 1,
fees 1.1, collateral 102 and available 896.9. A later bid 40/ask 42 implies pre-fill
marked equity 936.9; minimum equity 950 denies the next fill without another fee.

Weighted entry cost basis is exact. A nonterminating weighted average is explicitly
rejected through the account non-fill path instead of silently rounding. Currency,
multiplier, no-margin/no-FX and prefunded invariants remain those of Phase 18B.

## F. Position reduction and protective exits

Reduce-only orders bind the owned entry transaction, strategy, instrument, opposite
side and quantity <= current position. Partial/full long and short exits realize
exact P&L, apply closing fees once, release reduced cost-basis collateral, mark the
remaining position and never reverse. Entry-only risk limits do not block reductions;
quote/ownership/quantity/funding safety checks still apply. A full close preserves a
zero-quantity audit position.

The supported protective child is a reduce-only STOP_MARKET stop_loss or LIMIT
take_profit. One child/exit may be active; an active sibling/OCO is explicitly
rejected. Thus aggregate committed exits cannot exceed the owned position. Protective
partial fills and subsequent children are tested for long and short positions and
shared budget recovery. No general bracket/OCO or automatic sibling adjustment is
claimed. In the durable long fixture, entry is 2 at 101, stop trigger at 95, exits
1 at 94 and 1 at 93: realized P&L -15, fees 3.4, no collateral, available 981.6.

## G. Atomic publication design

Kernel records are prepared first. Accounting prepares exact settlement/reservation
changes and validates the complete candidate. Shared simulated liquidity is included
in the new account root; unsuccessful candidates consume no liquidity. Reversible
financial indexes stage before one account publication. Session candidate owners
include the exit kernel and required audit acknowledgement before deterministic
wire preparation and the existing SQLite transaction. Input/outcome/effects,
checkpoint and metadata head commit together before the session root publishes.

Allocation/index failures expose the prior roots and roll back caches. Funding or
unsupported economics cancel a proposed remainder without its phantom fill/fee.
Shared PREPARING/COMMITTING/COMMITTED store state preserves recovery_required across
ambiguous commit outcomes, interrupts and committed-but-unpublished state. Reads and
new operations then fail closed until fresh-owner recovery. Session stop stages
pending cancellation and acknowledgement with the non-liquidating terminal record.

## H. Durable recovery compatibility

Physical SQLite schema and JournalEntry/storage-policy versions stay v1 because
columns/table layout are unchanged. The manifest explicitly binds
paper-18f-exits-v2 to AdvancedReplayConfig schema 2; advanced order/config and
settlement payloads are versioned v2. V1 manifest/config cannot silently select
v2 semantics. Optional advanced state is encoded only when present, preserving
exact old canonical wires and IDs. A frozen journal generated by executing the
actual baseline code is recovered under both modes and compared byte-for-byte
without rewriting its operation/checkpoint records.

V2 recovery verifies all records/indexes/digests/checkpoints and runs the original
operational owners. Checkpoint prefixes use complete operational replay, including
position ownership, triggers, partial quantities, fees, pending cancellation,
liquidity consumption and acknowledgements. Full/checkpoint results agree. V2
checkpoints currently serve as verified anchors, not CPU accelerators. Active
recovery still requires new recorded pause/resume; historical retries cannot clear
the operator gate.

Fault tests cover submission, trigger, partial/final fill, cancellation request and
acknowledgement at precommit, postcommit and publication windows. Existing Phase 18E
process-death, SQLite VM interruption, corrupted-journal/checkpoint and operator-gate
regressions remain in the full suite.

## I. Exact tests and checks

Environment: Windows-10-10.0.26300-SP0, embedded CPython 3.11.9 64-bit,
pytest 9.1.1, Pydantic 2.13.5 and SQLite 3.45.1. The existing embedded interpreter
and installed dependencies were used; the virtual environment's original base
interpreter path is missing. No installation was performed.

| Required group | Result |
| --- | --- |
| New Phase 18F tests | 145 passed in 37.36 s |
| All paper tests, including 69 new cases | 634 passed in 21.19 s |
| Backtesting/risk | 468 passed in 2.22 s |
| Strategy/features/ML | 279 passed in 2.25 s |
| Validation/analytics | 186 passed in 1.97 s |
| Orchestration/MCP | 753 passed in 140.12 s |
| Persistence/recovery, including 76 new cases | 278 passed in 81.59 s |
| Full pytest suite on final production source | 3,385 passed in 253.52 s |
| git diff --check and new-file whitespace check | Passed |

Counts overlap between groups. The full count is the original 3,240 plus 145 new
cases. No skip or xfail was introduced or reported. Earlier complete runs passed
3,383 and 3,384 cases before the two final safety regressions were added. The final
production source includes both the shared-liquidity policy guard and current-quote
partial-entry risk marking. The final benchmark-only refinement separates trigger,
partial/final fill and terminal-delivery timing; its CLI run passed afterward.

Evidence: tmp/phase18f-new-release.log, phase18f-paper-release.log,
phase18f-full-release.log and phase18f-release-verification.json; unchanged regression
groups and persistence results are in tmp/phase18f-{group}.log and
phase18f-persistence-final.log. The intermediate final-source account regression run
passed 189 cases; the final risk-focused paper run passed all 69 advanced paper cases.

## J. Performance measurements

Command: python benchmarks/phase18f_advanced.py --repeats 10 --observations 32
--persisted-events 90 --recovery-repeats 3.

One serialized process on the Windows environment above; CPU identity was unreported.
Fixture/event construction and consumer snapshots are outside processing intervals.
No other tests or profile ran concurrently with the benchmark. SQLite uses a local
workspace temporary file, DELETE/EXTRA/EXCLUSIVE, checkpoint interval 2 and retention 2.
The filesystem is warm on a shared host, so results establish observations rather
than a latency SLA. There are no performance assertions.

| In-memory workload | Timed inputs | Events/s | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| market | 10 | 369.2 | 2.580 | 3.454 |
| resting_limit | 320 | 600.0 | 1.651 | 2.377 |
| stop_activation | 320 | 308.7 | 2.961 | 6.040 |
| partial_sequence | 320 | 64.0 | 15.245 | 26.760 |
| protective_exit | 30 | 111.5 | 9.691 | 12.805 |

The market row measures one v2 standalone kernel execution per fresh order.
Resting limits remain nonmarketable. Partial sequences are standalone kernels with
32 one-unit fills per order; the row does not claim account-settlement throughput.
The stop chain contains ten triggers, ten later fills and 300 terminal deliveries;
its aggregate median is not pure trigger latency. Separate trigger median/p95 are
1.369 / 2.054 ms. Its subsequent full-fill median is
3.324 ms. Protective sessions include ten triggers and twenty accounted reduction fills:
trigger, partial-fill and final-fill medians are 5.164, 9.691 and 11.620 ms.
Phase classification occurs outside the timed processing interval.

Durable processing measured 94 operations (exit submission, trigger, two reductions
and 90 heartbeats) after the four unmeasured approved-entry setup inputs. Throughput
was 27.86 events/s, median 38.667 ms, p95 47.586 ms and total 3.374 s.
The database held 98 operations and occupied 4,005,888 bytes. Fees were 3.4 and
realized P&L -15. Every measured recovery matched the independently processed
reference state, including the financial/audit projection.

Three fresh-owner samples per recovery mode were measured in alternating mode order,
including file open, admission and complete integrity/operational verification:

| Recovery mode | Median s | Raw samples s |
| --- | ---: | --- |
| Full journal | 2.497 | 2.450, 2.497, 2.643 |
| Checkpoint | 2.412 | 2.412, 2.370, 2.469 |

V2 checkpoint recovery still replays its verified prefix. Its small observed timing
difference is not evidence of asymptotic acceleration. The earlier uninstrumented
benchmark observed 20.86 persisted events/s versus 27.86 in the final run, illustrating
host/timing variability rather than a financial or storage-policy change. Final raw
measurements are in tmp/phase18f-benchmark.json; the first run is retained as
tmp/phase18f-benchmark-first.json.

Before/after CPU profiles used the same five advanced workloads with 24 observations,
two in-memory repetitions, 20 durable heartbeat inputs and one recovery per mode.
Profiles include fixture construction and consumer snapshots; their seconds are
CPU attribution, not latency measurements. Canonical serialization dominated.

The only optimization reuses one immutable configuration digest within each snapshot
validation, retaining every event binding/integrity check. A regression counts that
one derivation and verifies that configuration corruption still fails.

| Profile metric | Before | After |
| --- | ---: | ---: |
| Total profiled seconds | 18.809 | 16.276 |
| canonical_json calls | 18,819 | 12,418 |
| stable_id calls | 16,915 | 10,514 |
| canonical_json cumulative seconds | 16.240 | 13.668 |

Call-count reduction directly establishes eliminated redundant work. The 13.5%
observed profile-time reduction is not a throughput guarantee on this shared host.
Evidence: tmp/phase18f-profile-before.txt and tmp/phase18f-profile-after.txt.

## K. Known limitations and remaining blockers

**The Phase 18F no-full-history-copy hot-path requirement is not met.** Existing
immutable kernel snapshots retain input/event tuples and serialize retained order
history. Session candidates also clone account journals/indexes. Repeating advanced
observations increases work with retained order history. V2 capacity defaults to
128 inputs, configurable 3..256, with two extra cancellation-completion records;
this bounds cost but does not remove copying. A compact-state/journal-reference
format and transactional incremental retry indexes are needed. Introducing that
cross-component format/recovery rewrite without proof would risk the Phase 18E
integrity and publication guarantees, so this stage preserves the existing design
and reports the blocker instead of claiming completion.

Other explicit unsupported cases: two-child OCO/brackets and sibling resizing;
durable resting advanced strategy entries under the locked-next-open v1 approval;
order-level fixed fees; DAY/GTD/FOK/IOC stops; nonterminating weighted average entry
bases; orders resting beyond the input bound; multi-strategy/account allocation,
netting/hedging, margin/FX, live brokers, network execution, APIs and dashboards.
Advanced entries are trusted local account operations with explicit reservation;
durable v2 sessions only add exits after the approved v1 entry. New reviewed
execution/admission policy is necessary before claiming durable advanced entries.

## L. Git diff/status summary

HEAD remains 504606b3c47dab589b56ebc4a6707492048bbf3f on master. There are 15 tracked
modified files and seven new files listed in section B, all uncommitted. The tracked
diff contains 681 additions and 90 deletions; Git's unstaged diff statistic excludes
the seven untracked files. README, architecture and roadmap explicitly label the
bounded stage and retain the Phase 18F readiness blocker. git diff --check passes;
new files were also checked for trailing whitespace. No existing test file,
dependency manifest or unrelated tracked file was changed. No commit or push was made.

## M. Readiness assessment

**Bounded supported stage implemented and verified; Phase 18F is not complete.**
The supported financial, matching, cancellation and protective-exit/recovery paths
have focused regressions and broad compatibility checks. The hot-path/history
representation remains a mandatory readiness blocker. The roadmap deliberately
keeps Phase 18F incomplete. This is not a claim of brokerage-grade execution/OCO,
unbounded performance or readiness for Phase 19 portfolio allocation. Work stops
after implementation, testing and this report, without commit or push.

## N. Retained-history copying remediation (2026-10-08)

This follow-up supersedes the history-copying blocker in sections K and M.
The original stage measurements above are retained as the comparison baseline.
The report and dirty implementation were inspected before edits. Existing work
was preserved; this remediation adds no OCO, advanced strategy entries or Phase 19
capability. HEAD remains 504606b3c47dab589b56ebc4a6707492048bbf3f.

### Root cause and operation audit

Let I be retained order inputs, E retained execution events, F financial events,
L consumed quote identities, and B the current input/generated-record bytes.
The previous hot path repeatedly represented execution authority as a complete
consumer KernelSnapshot. Immutable snapshots therefore required prefix copies even
when the new observation produced no execution. Session candidates duplicated the
financial caches for isolation; v2 partial fills made those caches grow.

| Previous operation | Work/allocation per new operation |
| --- | --- |
| Input/event tuple expansion in normal preparation and pending cancellation | O(I + E) retained references copied |
| Retry dictionary expansion | O(I) entries copied |
| Event-ID collision/causation sets and retained-market cause set | O(E), O(I + E), O(I) temporary entries |
| Activation/trigger lookup, filled quantity, per-quote consumption, pending-cancellation list | O(E) scans; trigger/request lists allocate O(E) in the worst case |
| KernelSnapshot construction/revalidation and canonical serialization | O(I + E) nested reconstruction and O(retained wire bytes) materialization |
| Account denial model_dump, replacement history and retry map | O(I + E) retained model data/references and O(I) retry entries |
| Session financial journal, two dictionaries and four index-set clones | O(F) retained entries, including on unrelated heartbeat events |
| Account consumed-liquidity dictionary expansion | O(L) retained quote entries |
| Financial islice starting at F | O(F) prefix traversal before bounded new records |
| SessionRecord.exit_order, Effects.exit_kernel and scheduled checkpoint exit snapshot | Repeated O(I + E) validation/encoding of the retained exit prefix |

Account order maps and cloned kernel bindings copy at most 32 entries. Opening
acknowledgements in an exclusive session are fixed to its one strategy entry.
Legacy durable strategy-entry snapshots contain at most three market/submission/
terminal-cancellation inputs; their serialization is bounded independently
of later session/exit history. These small copies remain. Explicit public session,
account and order snapshots intentionally traverse retained audit history. Recovery
also reads/verifies retained history; these are consumer/recovery operations, not
new-event processing.

### Bounded fix and changed files

Order preparation now keeps a private immutable OrderStateView. Input/event history
is an immutable linked sequence of newly appended chunks; no prior chunk is copied.
Counts, canonical byte lengths, cumulative fills/costs, activation, trigger, pending
cancellation and adapter causal references update from only the new records. Public
KernelSnapshot remains the same frozen, fully validated, complete-history contract,
materialized lazily and cached per immutable state. Snapshot identity and historical
prefixes survive retries/failures. Incremental byte accounting retains the 4 MiB
full-snapshot serialization guard without encoding its prefix during processing.

Retry, execution identity, market cause and liquidity indexes use private persistent
SHA-256 radix tries. Each update copies exactly 64 branches, each with at most 16
children; lookups traverse at most 64 branches. Work is bounded by new input/record
size and a fixed number of trie updates, independent of I/E/F/L. Published roots are
never mutated. Standalone legacy kernels use the same incremental history/indexes.
The existing advanced input and 32-kernel capacities are unchanged.

Session financial journals/indexes now share append-only storage, like the existing
runtime journals. Candidate writes record only newly introduced cache keys in an
undo log. Failure restores the prior root, removes the staged journal suffix and
undoes those keys. Public reads retain committed-count/version filtering. Liquidity
is immutable with the account/order publication root. Financial extraction slices
only the new bounded suffix, avoiding an islice walk through the prefix.

New exit audit records, durable effects and checkpoints contain an explicit
schema-version-2 KernelProgress head rather than the complete exit snapshot. It
records current state, prefix counts/digest and cumulative execution/cost/causal
state. Newly generated execution and financial records remain in each append-only
session operation. Full/checkpoint recovery uses the same operational owners to
rebuild history/indexes and compare every expected head/effect. Digests alone do not
confer execution authority. Physical storage and outer v1 contracts are unchanged.
Old v2 full-snapshot records are decoded/replayed in their original representation,
retaining their IDs/wires; a newly appended suffix uses heads. V1 wires are unchanged.

Files changed by this remediation, relative to the inspected dirty implementation:

- src/quantlab/paper/history.py (new): immutable history and bounded persistent indexes.
- src/quantlab/paper/order_state.py (new): incremental matching state and lazy consumer export.
- src/quantlab/paper/models.py; orders.py; accounts.py; sessions.py; session_models.py.
- src/quantlab/persistence/contracts.py; paper.py.
- tests/paper/test_history_hot_path.py; tests/persistence/test_history_hot_path.py (new).
- tests/paper/test_advanced.py: digest-count assertion now accounts for lazy export
  and explicit revalidation separately; corruption rejection remains tested.
- README.md; docs/architecture.md; docs/roadmap.md; docs/paper-advanced-orders.md;
  docs/phase18f-report.md: update only history architecture/status/report claims.

No pricing, strategy, risk or accounting formulas, dependency, benchmark script,
SQLite transaction policy or unrelated implementation was changed in this follow-up.

### Regression and compatibility evidence

42 new cases cover prefix lengths 0, 8, 64 and 200 for matching, and up to 100
accounted partial exits or pending cancellations for session/adapter paths.
History/index iteration and full-snapshot construction are forbidden during the
measured processing call. Branch-copy operation counts are checked directly:
64 branch copies per new index entry/update, each at most 16 children. A standalone
partial fill makes six index updates (384 bounded branch copies) at every tested
history length; a durable reduction also makes six. Resting and legacy terminal
deliveries make two. Exact retries make no updates. Tests check physical chunk
sharing, cumulative quantities, append-only event prefixes and frozen snapshot
identity. These are structural/operation-count checks, not timing assertions.

Durable tests process long partial histories with financial-journal iteration
forbidden, verify shared cache identities and bounded head wires, then recover under
both modes. They cover mixed old snapshot/new head journals, staged-suffix rollback
after record/publication interrupts, and ten hash-consistent forged-head scenarios
(counts, digest, cumulative quantity or costs). Forged heads are rejected by
operational comparison even after all content, journal and checkpoint hashes are
recomputed. Original partial-fill/reduce-only/protective/cancellation and real
SQLite fault-window tests also pass.

An ignored source archive reconstructed from HEAD plus the exact inspected dirty
diff generated an actual seven-operation pre-remediation v2 journal (approved entry,
protective submission, trigger, partial reduction). Updated full and checkpoint
recovery matched its original full snapshot and retained every operation/checkpoint
row byte-for-byte. The frozen original Phase 18E fixture remains covered by tests.
An additional check confirmed incremental canonical byte lengths exactly equal
full-snapshot wire lengths through submission, partial fills and cancellation.

| Final check | Result |
| --- | --- |
| Focused Phase 18F + copying regressions | 187 passed in 91.08 s (145 existing + 42 new) |
| Full pytest suite on final production source | 3,427 passed in 334.86 s |
| Original v2 journal, full and checkpoint recovery | Snapshot and stored wires identical |
| Incremental full-snapshot byte accounting | Exact agreement in exercised lifecycle |
| git diff --check + all new-file whitespace checks | Passed |

No skips or xfails were introduced. An earlier full run passed 3,424 cases before
the final three adapter regressions/reference caches; the final run above includes
all changes. Intermediate failures were an initialization mistake and test setup
assumptions about lazy validation/duration decoding; they were corrected before the
final focused/full runs. No existing integrity or financial assertion was removed.
Logs: tmp/phase18f-history-regressions-final.log, phase18f-history-full-final.log,
phase18f-history-legacy-verification.json and phase18f-history-bound-verification.json.

### Measured throughput and recovery comparison

The existing Phase 18F benchmark ran unchanged with exactly the section J command:
python benchmarks/phase18f_advanced.py --repeats 10 --observations 32
--persisted-events 90 --recovery-repeats 3. Tests, profiles and benchmarks did not
run concurrently with these measurements. The same embedded Python/Pydantic/SQLite,
serialized writer, warm shared Windows host, DELETE/EXTRA/EXCLUSIVE policy,
checkpoint interval 2 and retention 2 were used. Fixture construction and consumer
snapshots remain excluded. Different run times on a shared host are not a controlled
hardware experiment or latency guarantee.

| Workload | Original → current events/s | Median ms | p95 ms |
| --- | ---: | ---: | ---: |
| market | 369.2 → 467.2 | 2.580 → 2.131 | 3.454 → 2.522 |
| resting_limit | 600.0 → 1163.6 | 1.651 → 0.684 | 2.377 → 1.329 |
| stop_activation | 308.7 → 1027.6 | 2.961 → 0.790 | 6.040 → 1.671 |
| partial_sequence | 64.0 → 405.8 | 15.245 → 2.094 | 26.760 → 3.395 |
| protective_exit | 111.5 → 100.0 | 9.691 → 8.105 | 12.805 → 18.727 |
| Durable, 94 timed operations | 27.9 → 36.7 | 38.667 → 25.089 | 47.586 → 55.183 |

The measured standalone partial-sequence throughput is 6.35 times the prior report;
durable throughput is 31.8% higher in this run. These are observed measurements,
not attribution of every timing change to code. Structural tests independently
establish elimination of retained-prefix copies. Protective-exit aggregate throughput
is lower (111.5 → 100.0 events/s), despite a lower median. Its p95 and durable p95
are higher; there is no blanket latency-improvement claim. Protective trigger,
partial-fill and final-fill medians are 4.976 / 8.181 / 8.263 ms; the ten final fills
include a 46.543 ms maximum/p95 observation. The stop chain still combines trigger,
fill and 300 terminal deliveries; its lower aggregate median is not pure trigger
latency. Standalone partial throughput does not include account settlement.

| V2 recovery mode | Original median s | Current median s | Current raw samples s |
| --- | ---: | ---: | --- |
| Full journal | 2.497 | 1.546 | 1.404, 1.642, 1.546 |
| Checkpoint | 2.412 | 1.731 | 1.736, 1.731, 1.542 |

Each fresh recovery matched the independent reference snapshot. Both modes still
verify/replay the complete operational prefix; the checkpoint timing does not claim
asymptotic acceleration. The database has the same 98 operations, fees 3.4 and P&L
-15; size is 2,285,568 bytes versus 4,005,888, reflecting bounded head wires. Timed
durable processing totaled 2.560 s versus 3.374 s. Full raw measurements are retained
in tmp/phase18f-history-benchmark.json; the original comparison is unchanged in
tmp/phase18f-benchmark.json.

The unchanged Phase 18D replay benchmark also completed:
python benchmarks/phase18d_replay.py --counts 100 1000.

| Replay workload | 100 events/s | 1,000 events/s |
| --- | ---: | ---: |
| quotes | 758.9 | 753.7 |
| bars | 483.5 | 447.3 |
| raw-offset-bars | 360.3 | 360.2 |
| entry-and-bars | 320.7 | 329.7 |

These v1 workloads are compatibility/throughput observations, not comparisons with
the advanced-order rows in section J. Raw output: tmp/phase18f-history-replay-benchmark.json.

The unchanged Phase 18E persistence benchmark completed:
python benchmarks/phase18e_persistence.py --counts 100 1000
--checkpoint-interval 100 --recovery-repeats 2. Full/checkpoint mode order alternated;
every recovered state matched the independently processed reference. The checkpoint
interval/counts differ from the older 5,000-event Phase 18E report, so no speedup
comparison with those older measurements is claimed.

| V1 workload | Market events | Persisted events/s | Full median s | Checkpoint median s |
| --- | ---: | ---: | ---: | ---: |
| quotes | 100 | 74.8 | 0.542 | 0.343 |
| quotes | 1000 | 94.6 | 4.597 | 2.951 |
| entry-and-bars | 100 | 49.4 | 1.388 | 0.916 |
| entry-and-bars | 1000 | 55.2 | 11.336 | 8.755 |

Raw samples, phases, storage sizes and final-state digests are in
tmp/phase18f-history-persistence-benchmark.json. All three benchmark commands exited
successfully and were run sequentially after focused/full testing.

### Remaining risks and scope boundary

Consumer full snapshots still take O(retained history) time/memory, and retained
histories/indexes still consume O(N) memory. Fixed-depth persistent tries add an
allocation/memory constant compared with mutable dictionaries. Session shared-cache
staging relies on the existing exclusive serialized owner boundary and committed
read counts; this change does not introduce concurrent reader/writer support.

KernelProgress is a new versioned nested record format. Consumers of per-operation
exit audit records must use its head fields or request SessionSnapshot.exit_order
for complete history; older code cannot read new head records. Updated recovery
accepts older v2 snapshots without rewriting them. Verification of older full-
snapshot journals can still incur their original history-dependent decoding cost.
Both v2 recovery modes continue complete prefix operational replay; checkpoints are
verified anchors, not asymptotic recovery accelerators. SQLite synchronization and
strict bounded-record codec work remain on the durable processing path.

Existing input/retention/one-position capacities and other section K limitations
remain. This remediation resolves retained-history copying; it does not claim full
Phase 18F completion, OCO, advanced strategy entries or Phase 19 readiness. Work stops
after this bounded fix, validation and report. No commit or push was performed.

Final worktree verification: 15 tracked modified files and 11 new files remain
uncommitted (four new files are from this remediation). The complete tracked dirty
diff is 797 additions / 131 deletions; it includes the original Phase 18F work and
excludes untracked-file contents. Content comparison against the inspected source
archive found exactly the nine production files listed above changed by this
remediation; other original production work is intact. git diff --check and all
new-file whitespace checks passed. Final evidence is consolidated in
tmp/phase18f-history-final-verification.json. HEAD is unchanged; no commit or push.

## O. Two-child protective OCO implementation (2026-10-09)

This follow-up supersedes the unsupported-OCO statements in sections K–N. Those
sections retain their historical implementation results and measurements. The
actual dirty source, tests, protective/session/recovery interfaces and section N
were inspected before edits. An exact pre-OCO source archive was retained in
ignored tmp/phase18f-before-oco; existing work was preserved. This implementation
adds the requested OCO group without advanced strategy entries or Phase 19 work.
**Phase 18F remains incomplete.**

### A. Files changed in this follow-up

Created:

- src/quantlab/paper/oco_models.py: explicit commands, bounded heads/events and full consumer validation.
- src/quantlab/paper/oco.py: creation and two-child coordination under the account publication root.
- tests/paper/test_oco.py: 81 focused matching/account/session/atomicity/structural cases.
- tests/persistence/test_oco.py: 71 durable boundary, fault, corruption, compatibility and scaling cases.
- tests/persistence/fixtures/phase18f-v2-before-oco.json: frozen actual pre-OCO v2 journal/checkpoints/snapshot.
- benchmarks/phase18f_oco.py: stop/target/partial-chain latency, real persistence and operational recovery.

Modified production files relative to the inspected source: paper/account_models.py,
accounting.py, accounts.py, advanced.py, models.py, orders.py, order_state.py,
session_models.py, sessions.py, strategy_models.py, __init__.py, and
persistence/contracts.py, paper.py, store.py, all under src/quantlab.
README.md, docs/architecture.md, docs/roadmap.md, docs/paper-advanced-orders.md and
this report describe the new policies and remaining scope. Existing test and
benchmark files are byte-identical to the pre-OCO archive. No dependency, risk,
backtesting, strategy engine, MCP tool or orchestration implementation was changed.

### B. State and ownership model

OCOCommand schema 3 selects trusted local creation and whole-group cancellation.
Creation requires a settled live position, exact account/strategy/instrument/entry
transaction ownership, its entire current quantity, a fresh committed position
valuation, and stop/target prices strictly bracketing its executable bid/ask side.
It rejects independent active orders, reservations, overlapping groups and
unsupported ownership. Group/child identities derive from canonical creation
content. One group is allowed per entry transaction; cancellation does not authorize
replacement on that same position. Both GTC children are reduce-only, one
STOP_MARKET stop_loss and one LIMIT take_profit. They reserve neither position
quantity nor entry collateral independently.

The immutable account publication includes both child states and one bounded
OCOProgress head. That head carries original/live quantity, ownership, revision,
clock, pending cancellation and two child progress records. Per-input OCOEvent
records carry only current child deltas and at most one financial effect identity.
Original submissions are never resized or rewritten. Explicit v3 child config,
submission, fill, adjustment, progress and settlement types select these semantics.
Public OCOSnapshot retains both complete append-only histories and validates their
ownership, quantities, causality, costs and prefix digests. Independent child
processing/cancellation and arbitrary price/quantity amendments are rejected.

### C. Matching and partial-fill behavior

Recorded sequences increase; equal timestamps retain that recorded order. Each
quote visits stop then target. Existing trigger rules require a later input before
a stop fill; a triggered stop has market precedence over a simultaneously executable
target on a later quote. Existing executable bid/ask, stop-gap/slippage and limit
price constraints remain in force. There is no inferred OHLC ordering. Stale,
unavailable and future/causally invalid observations cannot authorize execution.

Before matching each child, the coordinator uses the current live position and
shared group allowance. Each active child covers the full remainder: a successful
fill consumes either that remainder or the available shared quote budget. Thus at
most one child fills per quote; its sibling receives zero execution allowance for
that quote. Successful publication consumes liquidity once; redelivering the same
quote cannot replenish it. Failure consumes no allowance.

A peer fill appends OCOQuantityAdjustment to the sibling, binding only the actual
current fill and updating cached cumulative withdrawals. Completion stages the
winning fill, position close and sibling cancellation together. Already committed
fills remain unchanged. The flat session audit projects the actual target fill
before its stop adjustment when target wins, preserving causal event order.
Cancellation requests leave both children executable; a racing partial fill remains
committed, while acknowledgement cancels both remaining allowances. Full closure
makes a later acknowledgement a terminal outcome rather than undoing execution.

### D. Financial and sibling-quantity invariants

For admitted quantity Q, actual stop fills S and target fills T:

- group remaining = Q - S - T, never negative;
- each active child remaining = Q - own fills - peer withdrawals;
- peer withdrawals equal the other child's actual filled quantity;
- both active child remainders equal the live owned position quantity;
- each financial reduction binds that position and the actual post-fill remainder.

OCOApplyFill extends the existing exact reduction accounting, including its cached
withdrawal total. It does not duplicate the financial engine. Actual fills alone
charge execution fees, realize P&L and release reduced basis collateral; peer
adjustments have no financial effect. Over-reduction, exposure growth, attribution
mismatch and reversal fail closed. After full closure, a child may be FILLED with
own fills below Q because its own fills plus peer withdrawals equal Q. After
explicit group cancellation, the head retains nominal unexecuted quantity for
audit, but neither cancelled child has execution authority; residual exposure is
not liquidated.

Hand fixtures start with capital 1000 and a long two-unit entry at 101, fee 1.2.
Stop exits of one unit at 94 and one at 93 produce P&L -15, total fees 3.4 and
available balance 981.6. Target exits at 110 and 111 produce P&L 19, total fees
3.4 and available balance 1015.6. A mixed target/stop chain realizes P&L 2.
Full-fill, short-side, spread/slippage and fixed-per-fill fee cases independently
check executable prices, fees, collateral and zero reversal.

### E. Atomicity, persistence and recovery

Both child preparations, peer adjustment/cancellation, group head, current financial
records and shared liquidity are prepared before the account root is published.
OCO preparation or economic failure aborts the entire new input; it preserves the
previous valid children and position and remains retryable/cancellable. Creation
rolls back only bindings admitted by that attempt and restores the prior account
root even if interruption follows its publication handoff. Session candidates use
the existing bounded undo/staged-journal transaction. Session stop is nonliquidating:
it batches request/acknowledgement, or directly completes an existing pending
acknowledgement, preserving cancellation completion at the input capacity boundary.

SQLite input, output, financial effects, group heads, checkpoint and journal head
commit under the existing transaction before session publication. Ambiguous commit,
after-commit interruption or publication failure requires recovery; the interrupted
owner cannot furnish readable execution authority or continue processing. Full and
checkpoint recovery construct fresh owners and regenerate all operational effects,
then compare order/group/financial heads, outcomes and checkpoints. A digest is not
accepted as execution authority. Hash-consistent forged heads remain rejected.

Physical SQLite schema and outer journal/storage versions stay v1; the explicit
advanced-session manifest/config remain paper-18f-exits-v2/schema 2. OCO is selected
by explicit nested v3 contracts, never inferred from old single-exit records.
Optional OCO fields are omitted when absent, preserving historical v1/v2 wires and
identities. The frozen actual pre-OCO v2 fixture and original Phase 18E v1 fixture
recover in both modes without rewriting their records. Checkpoints remain verified
anchors with complete operational prefix replay, not asymptotic recovery shortcuts.

### F. Regression and verification results

| Final-source coverage | Passed |
| --- | ---: |
| New OCO paper tests | 81 |
| New OCO persistence tests | 71 |
| New OCO total | 152 |
| Existing Phase 18F + history regressions | 187 |
| All paper/persistence | 1,106 (735 + 371) |
| Backtesting/risk | 468 (300 + 168) |
| Orchestration/MCP | 753 (113 + 640) |
| Full pytest suite | 3,579 in 461.86 s |

Focused suites and the requested existing groups were also run separately during
implementation. The final full suite includes all refinements and the default
128-input durable cancellation-completion case; every collected test passed,
with no skips or xfails. The observer only counted ordinary pytest outcomes; it
did not change test selection. git diff --check and new-file whitespace checks
passed. Existing integrity, financial and corruption assertions were preserved.

Recovery cases cover creation, trigger, partial stop/target, mixed fills, full
closure/sibling cancellation, pending cancellation, acknowledged cancellation and
session stop under both modes. Fault cases cover eight operations at pre-commit,
commit-armed, after-commit and publication boundaries. Other tests inject failures
during peer reconciliation, financial preparation, event/audit staging and account
handoff. Eight hash-consistent forged-head cases and four consumer-head corruption
cases reject altered quantities, costs, digest/count/revision authority. Exact
retries, conflicting identities, equal timestamps, shared/reused budgets, stale or
unavailable feeds, future observations, ownership negatives and cancellation races
are explicitly exercised.

Twenty structural active-path cases use retained prefixes 0, 8, 64 and 200 across
resting, trigger, partial fill, full completion and cancellation request. They
forbid retained History/RetainedMap iteration and full KernelSnapshot construction
inside processing. Exact retries add zero index updates. At every prefix, these
paths use respectively 5/6/12/16/5 fixed-depth trie updates; each copies exactly
64 branches with at most 16 entries per branch. The full-completion fixture has no
liquidity budget; a budgeted completion additionally updates account consumption.
Eight durable scaling cases use prefixes 0/8/64/100 and both recovery modes, forbid
financial-prefix iteration, and count exactly 12 updates for a partial fill.
Current effects/output remain bounded below 30/60 kB in those fixtures and contain
heads, not retained child histories. Saved public snapshots retain immutable
prefixes. These establish bounded processing work structurally rather than by
latency alone; consumer exports and recovery intentionally traverse history.

### G. Performance measurements and regression investigation

The unchanged advanced benchmark ran on the exact archived pre-OCO dirty source,
then on final source; the new OCO benchmark also ran on final source. Each used:

```text
python -m benchmarks.phase18f_advanced --repeats 10 --observations 32 --persisted-events 90 --recovery-repeats 3
python -m benchmarks.phase18f_oco --repeats 10 --observations 32 --persisted-events 90 --recovery-repeats 3
```

The archive was the inspected implementation including section N's history fix,
not a reconstructed HEAD baseline. Runs were sequential, with no concurrent test,
profile or other benchmark process. Embedded CPython 3.11.9 (64-bit), Pydantic
2.13.5 and SQLite 3.45.1 ran on the same warm shared Windows host. Construction and
consumer snapshot materialization are excluded; processing includes audit and,
where applicable, accounting and strict durable codec/transaction work. Durable
runs use DELETE/EXTRA/EXCLUSIVE, interval 2 and retention 2. These are observed
samples on an uncontrolled host, not performance guarantees or causal speedup proof.

| Existing workload | Section N events/s | Archived pre-OCO events/s | Final events/s | Final median ms | Final p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| market | 467.1 | 420.9 | 406.9 | 2.436 | 2.896 |
| resting_limit | 1163.6 | 1268.6 | 1076.2 | 0.751 | 1.293 |
| stop_activation | 1027.6 | 968.0 | 1008.2 | 0.808 | 1.457 |
| partial_sequence | 405.8 | 344.1 | 332.9 | 2.334 | 4.398 |
| protective_exit | 100.0 | 97.4 | 121.5 | 7.951 | 12.308 |
| Durable (94 inputs) | 36.7 | 33.4 | 37.8 | 26.507 | 38.803 |

Standalone partial processing measured 332.9 events/s versus section N's 405.8
and the fresh archive's 344.1; median/p95 are 2.334/4.398 ms versus N's 2.094/3.395.
Final single protective median/p95 are 7.951/12.308 ms versus N's 8.105/18.727 and
the fresh archive's 9.252/13.135. Final single durable median/p95 are
26.507/38.803 ms versus N's 25.089/55.183. These mixed results support no blanket
throughput or tail-latency improvement claim. Single protective trigger/partial/
final medians are 4.795/8.369/8.289 ms; partial max/p95 is 28.687 ms.
The stop_activation aggregate includes 300 terminal deliveries, and the standalone
partial workload excludes account settlement; neither is an accounted OCO fill rate.

| New OCO workload | Timed inputs | Events/s | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| oco_stop_exit | 30 | 32.0 | 28.866 | 48.518 |
| oco_target_exit | 20 | 27.2 | 35.029 | 64.081 |
| oco_partial_sequence | 320 | 30.1 | 31.071 | 43.046 |
| Durable (creation/trigger/two fills/90 heartbeats) | 94 | 30.0 | 30.928 | 56.881 |

| OCO phase | Samples | Median ms | p95 ms |
| --- | ---: | ---: | ---: |
| oco_stop_exit/trigger | 10 | 18.888 | 28.944 |
| oco_stop_exit/partial_fill | 10 | 37.543 | 49.480 |
| oco_stop_exit/final_fill | 10 | 38.330 | 48.518 |
| oco_target_exit/partial_fill | 10 | 30.459 | 41.777 |
| oco_target_exit/final_fill | 10 | 37.165 | 64.081 |
| oco_partial_sequence/partial_fill | 310 | 30.967 | 43.046 |
| oco_partial_sequence/final_fill | 10 | 39.377 | 42.006 |

The stop chain uses trigger and two fills; the target chain uses two fills.
The 32-unit partial chain runs ten independent groups with one-unit quote budgets
(310 partial and ten final reductions). Stop OCO throughput is materially lower
than final single protective throughput (32.0 versus 121.5 events/s); final-fill
median is 38.330 versus 8.289 ms. The larger bounded linked/audit payload and two
child transitions add substantial constant work. Durable OCO measured 30.0 versus
single 37.8 events/s, but both aggregates contain 90 post-close heartbeats and only
four timed setup/execution operations; they are not durable fill-only rates.
OCO durable creation/trigger/partial/final individually took
53.956/67.216/109.485/88.004 ms (one sample each). Heartbeats measured median
30.854 ms, p95 46.355 ms. OCO timed durable processing totaled 3.133 s versus
single 2.490 s. Every result matched the independent reference and expected fees
3.4/P&L -15. Both databases retain 98 operations; OCO is 3,317,760 bytes versus
single/archive 2,285,568, reflecting extra current child/group audit data.

| Recovery | Median s | Raw samples s |
| --- | ---: | --- |
| Archived single / full | 1.643 | 1.841, 1.579, 1.643 |
| Archived single / checkpoint | 1.639 | 1.667, 1.639, 1.540 |
| Final single / full | 1.788 | 1.788, 1.637, 1.823 |
| Final single / checkpoint | 1.908 | 1.949, 1.908, 1.677 |
| Final OCO / full | 2.202 | 2.108, 2.202, 2.473 |
| Final OCO / checkpoint | 2.133 | 2.133, 2.084, 2.334 |

Section N single full/checkpoint medians were 1.546/1.731 s. Final single recovery
is higher at 1.788/1.908 s; final OCO is 2.202/2.133 s. Every fresh full/checkpoint
owner matched the complete expected snapshot, including fills, withdrawals,
liquidity, financial state and acknowledgements. Mode order alternated. The small
sample count and complete prefix verification do not establish checkpoint speedups.

Material OCO regressions were investigated rather than hidden. A separate profile
of 74 ordinary OCO inputs made 24,729,588 calls in 7.350 profiled seconds.
canonical_json took 6.616 cumulative seconds (about 90%), with 5,950 calls;
stable_id took 4.583 cumulative seconds, with 4,234 calls. These cumulative figures
overlap and must not be summed. Progress construction was 0.169 s (2.3%), and
906 persistent-index updates took 0.076 s (1.0%). The dominant extra cost is repeated
strict canonical encoding/content validation of bounded group/child/audit records,
not traversal or copying of retained prefixes. Caching progress alone would target
a small fraction; rewriting serializers, identity formats or integrity checks was
not justified as part of this bounded implementation.

A separate normal-GC observational run of 50 stop chains found six major collection
events for OCO and none for either archived/current single exits. OCO's slowest
73.071 ms event included 31.984 ms of GC; other major-collection events included
roughly 20–23 ms. Total OCO GC time was 0.203 s out of 3.693 processing seconds.
Other slow events had under 1 ms of GC, so collections do not explain every tail.
The archived 150-event audit did not reproduce section N's 46.543 ms final-fill
outlier; its cause cannot be retroactively attributed to GC. The earlier ordinary
OCO run measured stop/partial/durable 40.6/37.0/33.4 events/s versus final
32.0/30.1/30.0. The two subsequent fixes affect exceptional creation rollback and
pending-stop cancellation, outside those timed normal paths; run variation remains
material. No timing assertion or unsupported attribution was added.

The unchanged Phase 18D and 18E benchmarks also passed sequentially during this
follow-up. They preceded the final exceptional-handoff/pending-cancellation guards;
those guards do not execute in these v1 workloads. Commands were:
python -m benchmarks.phase18d_replay --counts 100 1000;
python -m benchmarks.phase18e_persistence --counts 100 1000 --checkpoint-interval 100 --recovery-repeats 2.

| V1 replay workload | Events/s at 100 | Events/s at 1,000 | Section N at 100 / 1,000 |
| --- | ---: | ---: | ---: |
| quotes | 879.9 | 874.5 | 758.9 / 753.7 |
| bars | 513.0 | 466.6 | 483.5 / 447.3 |
| raw-offset-bars | 453.9 | 407.1 | 360.3 / 360.2 |
| entry-and-bars | 390.8 | 392.2 | 320.7 / 329.7 |

| V1 durable workload | Events | Events/s | Full median s | Checkpoint median s |
| --- | ---: | ---: | ---: | ---: |
| quotes | 100 | 125.9 | 0.357 | 0.229 |
| quotes | 1,000 | 96.1 | 4.565 | 2.729 |
| entry-and-bars | 100 | 49.6 | 1.223 | 0.954 |
| entry-and-bars | 1,000 | 55.5 | 11.136 | 9.917 |

The last checkpoint median exceeds section N's 8.755 s; its raw samples were
8.901/10.934 s. Other observations vary in both directions relative to N.
No broad performance improvement is claimed. All benchmark commands exited zero;
all recovered states matched their independent reference.

Evidence is retained under ignored tmp/: phase18f-oco-final-suite.log,
phase18f-oco-final-counts.json, phase18f-oco-baseline-benchmark.json,
phase18f-oco-final-advanced-benchmark.json, phase18f-oco-final-benchmark.json,
phase18f-oco-profile.txt, phase18f-oco-tail-baseline.json,
phase18f-oco-tail-current.json, phase18f-oco-replay-benchmark.json and
phase18f-oco-persistence-benchmark.json. The profile/tail observations preceded the
last two exceptional-path guards; final normal-path benchmarks above were rerun
after the final complete suite. No profiling overhead enters the throughput tables.


### H. Compatibility and limitations

Legacy standalone Phase 18A kernels, v1 paper layers and existing v2 single exits
retain their contracts and semantics. New readers accept old full-snapshot and
bounded-head v2 records; old executables cannot decode v3 OCO payloads. The producer
still supplies trusted genuine quote/classification inputs, and existing exact
Decimal, prefunded account, one-position and attribution restrictions remain.

Admission requires full current quantity and a fresh committed valuation. Partial
position brackets, arbitrary amendments, independent child authority, replacement
groups on the same entry, DAY/GTD/FOK/IOC stops, exchange volume/queue modeling,
live brokers, multiple positions and concurrent writers are unsupported. Accounts
retain their 32-kernel bound; OCO consumes two slots. The existing default 128,
configurable 3..256 ordinary-input bound plus two cancellation-completion records
applies independently to both children. Completed audit histories remain retained.

Public full snapshots take O(N) time/memory, retained histories/indexes use O(N)
memory, and both recovery modes still validate the complete operational prefix.
Active coordination visits exactly two children plus the already bounded account
registry. Fixed-depth indexes have a substantial allocation constant. Shared cache
undo relies on the existing exclusive serialized-owner boundary. Canonical audit
serialization, strict current-record validation, SQLite synchronization and garbage
collection remain material latency costs; bounded history work is not a latency
SLA or a claim of low-cost protective execution.

### I. Remaining Phase 18F work

Two-child protective OCO and its retained-history requirement are now supported
within the stated bounds. Durable resting advanced strategy entries remain gated
on a new reviewed admission/execution policy; the existing locked-next-open v1
approval does not grant that authority. Order-level fixed fees, additional stop
TIF/session expiry policies, longer/unbounded lifetimes and broader position/account
models remain unsupported. The measured OCO serialization/allocation costs remain
performance risks requiring separate evidence-led work. Phase 18F is not marked
complete. No Phase 19, UI, broker, new MCP trading tool or LLM execution authority
was added.

### J. Git status, diff and stopping point

HEAD remains 504606b3c47dab589b56ebc4a6707492048bbf3f on master. The complete dirty
worktree contains 15 tracked modified files and 17 untracked files, including the
previous Phase 18F/history work and six new OCO files. The tracked diff is
1,061 additions / 133 deletions; it excludes untracked-file contents. Comparing
the pre-OCO archive confirms only the fourteen existing production files and five
documentation files listed in A changed in this follow-up; existing tests and
benchmarks are byte-identical. git diff --check passed and every untracked file
passed trailing-whitespace checks. Git's LF/CRLF conversion notices are line-ending
configuration notices, not whitespace errors.

Final evidence is consolidated in tmp/phase18f-oco-final-verification.json. Source
archives, logs, profiles and benchmark databases/results remain ignored local
artifacts. No reset, discard, commit or push occurred. Work stops after this bounded
OCO implementation, validation and reporting; Phase 18F remains incomplete.



## P. Durable advanced strategy entries

This section supersedes earlier statements that durable advanced strategy entries
are unsupported. All earlier Phase 18F, history and OCO work remains in the dirty
worktree. No reset, clean, discard, commit or push was performed. HEAD remains
504606b3c47dab589b56ebc4a6707492048bbf3f on master. The shell helper again could not
start; local Node child processes use the existing embedded CPython 3.11.9 and Git.
No dependency or external account connection was added.

### A. Exact files changed by this follow-up

Modified relative to the inspected incoming dirty tree:

- src/quantlab/paper/strategy_models.py
- src/quantlab/paper/admission.py
- src/quantlab/paper/runtime.py
- src/quantlab/paper/strategy_orders.py
- src/quantlab/paper/accounts.py
- src/quantlab/paper/session_models.py
- src/quantlab/paper/sessions.py
- src/quantlab/paper/__init__.py
- src/quantlab/persistence/contracts.py
- src/quantlab/persistence/paper.py
- src/quantlab/persistence/store.py
- README.md
- docs/architecture.md
- docs/roadmap.md
- docs/paper-advanced-orders.md
- docs/paper-strategy-runtime.md
- docs/paper-session-persistence.md
- docs/phase18f-report.md

Created:

- tests/paper/advanced_entry_helpers.py
- tests/paper/test_advanced_strategy_entries.py
- tests/persistence/test_advanced_strategy_entries.py
- benchmarks/phase18f_strategy_entries.py

All pre-existing tests, benchmarks, strategy specification/timing schemas, order
matching/pricing, accounting, OCO, retained-history and order-state implementations
are byte-identical to the incoming tree. Their account/session integration files
retain the existing work and receive only the entry integration described below.
Source fingerprints and an incoming source/test/benchmark/document archive are
retained under ignored tmp/phase18f-entry-initial-hashes.json and
tmp/phase18f-before-entries/. No missing incoming file was found.

### B. Architecture and causal generation

The narrow integration boundary is the existing admitted StrategyRuntime ->
StrategyOrderAdapter -> PaperAccount path, wrapped by PaperSession and its existing
durable preparation/publication seam. No alternate order, accounting or risk engine
is introduced. AdvancedStrategySessionConfig schema 2 selects fixed approved order
parameters and an explicit next-opening/later-quote policy. The unchanged causal
bar evaluator emits AdvancedEntryIntent only for its admitted on-time TRUE decision.
It retains strategy ID/version/digest, admission, policy, configuration and approval,
account/instrument, quantity, source bar, decision/causation, sequence and a
content-derived command identity. Causal dependencies/features remain in the exact
retained decision. Subsequent decisions cannot emit another entry.

The adapter derives AdvancedOrderSubmission only from that retained intent. An
actual on-time close quote, acceptance/risk/OrderActivation and reservation publish
in one existing account transaction. Nothing executes on the close submission.
Actual classified on-time adjacent locked OpeningDelivery provenance is required
before later quotes receive matching authority. The producer must preserve the
existing internal command sequence gap: submission is close delivery sequence + 1,
and opening must follow it. UTC timestamps may coincide only with increasing
recorded sequence. An opening cannot be reconstructed from OHLC or arbitrary ticks;
a missed opening cannot be submitted retrospectively after another completed bar.

The opening may fill a market/limit entry or trigger a stop. A stop requires a later
eligible delivery to fill, including a separately ordered delivery at the same UTC
time. After the opening gate commits, the reviewed policy permits later fresh
quotes across subsequent bar decisions. Paused, stale, interrupted, exhausted,
unavailable, backward or unproven inputs cannot execute. The opening gate is
published with execution and its retained acknowledgement in the account root;
its active index has one item per bounded owned kernel, at most 32.

### C. Versioned approval and timing authorization

Legacy StrategySpecification schema 1, TimingIntent, approvals, content digests,
EligibilityPolicy/Decision, EntryIntent and BAR_CLOSE -> NEXT_BAR_OPEN wires and
semantics are unchanged. Legacy ordinary quotes still cannot execute a strategy
entry. No legacy approval is reinterpreted as resting-order authorization.

AdvancedEntryPolicy schema 2 records fixed order type/prices, GTC/IOC, policy/version,
close-submit-next-open-gate-v2 activation, opening-then-ordered-fresh-quotes-v2,
simulated quote allowance, maximum quote age and bounded lifetime. A separate
AdvancedEntryApproval schema 2 binds the entire AdvancedStrategySessionConfig
excluding that approval, including the exact original approved strategy binding,
account/instrument/capital, quantity, risk/costs and policy. Changing any of these
requires a new approval; model_copy cannot bypass owner-side revalidation.

AdvancedEligibilityPolicy explicitly selects explicit-next-open-resting-v2.
AdvancedEligibilityDecision schema 2 binds that policy, execution approval and
configuration alongside unchanged independent retained research checks. Original
strategy approval must precede execution approval, then eligibility, then activation.
The original strategy document is not revised or retroactively changed. Existing
research reports describe their original strategy semantics; the separate advanced
eligibility is an explicit trusted execution-policy review, not a claim that an old
next-open backtest simulates resting entries. Review records/content hashes do not
provide reviewer authentication or signatures.

AdvancedEntryReplayConfig schema 3 and manifest paper-18f-entries-v3 explicitly opt
in. Session stale maximum age must equal the approved entry age. Old v1 sessions
and v2 exit sessions cannot silently select the new strategy configuration. Unknown
versions/config-engine pairings and unsupported parameters fail validation.

### D. Execution/accounting, idempotency and atomicity

The existing kernel owns matching, stop chronology, risk, deterministic partial
quantity, capped limit slippage and terminal state. Existing account reservation,
AdvancedApplyFill, fee, exact basis, collateral and position logic own economics.
Simulated allowance is explicit; no quote size, exchange volume or market liquidity
is fabricated. Reusing an identical quote cannot replenish its allowance.

Identical decisions retain one intent/command/order; exact input retries return
retained outputs and never reserve or charge again. Conflicting identities fail
before mutation, including collisions with strategy decision inputs at the trusted
standalone adapter. Failed admission creates no executable owner. Failed initial
funding leaves the submission absent; later risk/funding/economic denials retain
prior fills while replacing only the proposed fill with cancellation/release.
Unexpected failures undo only candidate journal/index suffixes. No phantom fill,
fee, reservation or opening gate is published.

EntryCancellationCommand schema 3 binds the exact intent, then routes request and
acknowledgement through existing cancellation semantics. Fills may win a request
race; acknowledgement cancels only the remainder. Stop completes a pending request
or batches request/ack atomically, including ordinary input capacity, without
liquidating a partial position. Existing one-account/one-position restrictions and
FILLED-entry requirements for protective exits/OCO remain. No agent/MCP tool,
financial authority or autonomous permission changed.

### E. Durable lifecycle and recovery

SessionRecord adds optional advanced entry intent and bounded entry_order progress.
Legacy records omit these fields, preserving canonical IDs/wires. Advanced Effects
and Checkpoint kernel fields hold KernelProgress: activation/trigger/pending cancel,
current market/order parameters, cumulative quantity/costs, counts and history digest.
Remaining quantity is exactly original minus cumulative fills. Durable inputs retain
the genuine opening, all subsequent quote ordering and cancellation commands.

Existing SQLite transactions commit input, output, current financial commands,
entry/session/provenance state, optional checkpoint and durable head before session
publication. PREPARING/COMMITTING/COMMITTED failure behavior is unchanged. Confirmed
precommit failure is retryable; ambiguous commit, postcommit interruption and
publication failure revoke readable execution authority and require a new owner.

Recovery rechecks admission and regenerates the recorded causal decisions, opening
gate, matching, trigger/fills, fees/reservations and cancellation with the same
engines. Full/checkpoint recovery compare exact operational effects and outcomes;
forged hash-consistent progress cannot grant authority. No AI inference is run.
Checkpoints remain verified anchors with complete operational prefix replay, not
an asymptotic recovery shortcut. Recovered ACTIVE owners preserve exact retries but
require newly recorded pause/resume before new market/entry/OCO processing.
Stopped owners preserve terminal state and have no operator gate.

Physical SQLite, journal/storage and outer checkpoint versions stay v1. Nested v2
entry contracts and explicit v3 replay/manifest/entry-cancel contracts select new
behavior. Current readers accept old v1/v2 records; older executables reject the
new configuration/payload rather than reinterpret it.

### F. Verification results

The final full source passed 3,828 tests in 589.14 seconds, with no skips, xfails,
failures or errors. All 3,579 incoming tests remain, plus 249 new entry tests.
JUnit independently confirms every testcase passed and no non-pass elements exist.

| Coverage | Passed |
| --- | ---: |
| New paper entry tests | 101 |
| New durable entry tests | 148 |
| New entry total | 249 |
| All paper | 836 |
| All persistence | 519 |
| All paper/persistence | 1,355 |
| Backtesting/risk | 468 (300 + 168) |
| Orchestration/MCP | 753 (113 + 640) |
| Full pytest | 3,828 |

Commands: python -m pytest -q --junitxml=tmp/phase18f-entry-full.xml;
focused paper/persistence entry suites ran during implementation. The final focused
245-test run passed in 113.65 seconds, followed by four added short-side cases
passing separately; all 249 are included in the full suite. The incoming strategy,
session, advanced-exit and OCO compatibility selection passed 279 tests separately.
Earlier focused failures exposed an unregistered durable entry-cancel command and
test assertions using an unsupported snapshot property; both were fixed before the
passing final runs, without weakening the existing assertions or omitting tests.

Coverage includes exact/draft/revised approval and digest negatives; configuration,
price, age, cost and liquidity authorization; v1 ordinary-quote timing; all four
advanced long/short types; unavailable offset features and future availability;
opening provenance/adjacency/freshness; same-timestamp trigger/fill ordering and
non-replenishing redelivery; partial/final fills, risk/funding denials and no phantom
fees; exact retry/conflicting identities; cancellation races; non-liquidating stop
and pending cancellation completion at capacity; precommit/commit-armed/postcommit/
publication failures across seven operations and both recovery modes; admission,
decision, resting, triggering, partial, final and cancellation restart; hash-consistent
forged heads; recovered-ACTIVE operator gates and subsequent OCO execution/recovery.
The full suite includes the exact frozen original v1 and pre-OCO v2 journals.

Structural tests prohibit History/RetainedMap iteration and KernelSnapshot
construction during new entry processing at retained lengths 0/8/64/200 across
resting, triggering, partial/final and cancellation. Durable cases cover 0/8/64/100
resting observations and actual partial financial fills under both recovery modes,
forbid financial journal iteration and preserve shared financial-index object
identities. Exact retries add zero index updates. Measured branch-copy counts remain
below 16 fixed-depth updates per input, with exactly 64 branches per update and at
most 16 entries per branch, independent of retained length. Current output/effects
remain below 40/30 kB in the structural fixtures and contain heads without retained
inputs/events. Consumer snapshots and recovery intentionally materialize history.

### G. Final benchmarks and retained-history findings

The archived pre-entry dirty tree is the baseline, rather than HEAD. The advanced
baseline was captured before entry implementation; the paired OCO baseline was run
against that archive during resumption. Existing advanced/OCO benchmark files are
unchanged. Main runs use 10 repetitions, 32 observations, 90 persisted heartbeats
and three recoveries per mode. Runs were serialized with no concurrent tests.
Construction and public snapshots are excluded from processing timings; audit,
accounting, durable codec and SQLite work are included. The warm shared Windows
host uses CPython 3.11.9 and SQLite 3.45.1. Advanced/entry durability uses DELETE/EXTRA,
checkpoint interval 2; OCO also specifies EXCLUSIVE and checkpoint retention 2.
Both recovery modes verify and operationally replay the complete prefix.

On resumption, the advanced baseline/final JSON and the complete strategy-entry
log already contained valid, finished results. The entry process had completed
after the earlier usage cutoff; none of these main runs was repeated. Outstanding
paired OCO runs, the entry profile and protective-exit tail audit were completed.
The observed OCO stop regression also warranted paired OCO tail audits and profiles.
Every benchmark/recovery assertion passed. A temporary audit-script launch failed
because of Windows native-argument quoting; the ignored script was corrected and
both audits completed. No project source or test changed during this resumption,
and the already passing full pytest suite was not repeated.

#### Existing advanced and OCO workloads: baseline -> final

All latency values below are milliseconds. Throughput is timed inputs per second,
not trading decisions, order completions or broker capacity.

| Workload | Timed inputs | Inputs/s before -> final | Change | Median before -> final | p95 before -> final |
| --- | ---: | ---: | ---: | ---: | ---: |
| Standalone advanced market | 10 | 385.4 -> 390.2 | +1.3% | 2.27 -> 2.30 | 4.48 -> 3.65 |
| Standalone resting limit | 320 | 920.7 -> 1,077.2 | +17.0% | 0.93 -> 0.74 | 1.73 -> 1.32 |
| Standalone stop activation/deliveries | 320 | 987.7 -> 913.5 | -7.5% | 0.81 -> 0.92 | 1.48 -> 2.05 |
| Standalone partial-fill sequence | 320 | 402.1 -> 396.0 | -1.5% | 2.11 -> 2.21 | 3.05 -> 2.97 |
| Session single protective exit | 30 | 126.3 -> 100.1 | -20.8% | 7.65 -> 8.50 | 9.29 -> 37.47 |
| Durable single protective exit/heartbeats | 94 | 42.4 -> 38.2 | -10.0% | 25.07 -> 25.73 | 30.63 -> 38.25 |
| Session OCO stop exit | 30 | 38.0 -> 28.8 | -24.0% | 27.36 -> 33.14 | 42.52 -> 54.79 |
| Session OCO target exit | 20 | 33.0 -> 29.7 | -10.0% | 26.74 -> 30.67 | 60.24 -> 65.17 |
| Session OCO partial-fill sequence | 320 | 34.3 -> 33.5 | -2.4% | 25.96 -> 27.11 | 47.67 -> 52.06 |
| Durable OCO/heartbeats | 94 | 30.3 -> 29.9 | -1.2% | 30.84 -> 32.72 | 56.73 -> 54.32 |

The stop aggregate contains 10 triggers, 10 fills and 300 terminal deliveries;
913.5 inputs/s therefore does not describe sustained stop execution. Protective
exit phase medians before -> final were trigger 4.49 -> 5.28, partial 8.12 -> 8.82,
final 7.98 -> 9.20 ms. Their phase p95s were 6.26 -> 6.56, 36.15 -> 44.15 and
8.90 -> 37.47 ms. OCO stop phase medians were 15.04 -> 22.98, 27.97 -> 41.81 and
29.41 -> 37.93 ms; phase p95s were 23.65 -> 28.15, 69.47 -> 77.93 and
35.39 -> 49.16 ms. Each of these phases has only 10 samples.

The durable single-exit processing total changed 2.215 -> 2.463 seconds; OCO
changed 3.106 -> 3.145 seconds. Database sizes stayed exactly 2,285,568 and
3,317,760 bytes respectively. Both preserve exact fees 3.4 and realized PnL -15.
The earlier section O final OCO run reported 32.0/27.2/30.1 inputs/s for
stop/target/partial and 30.0 durable inputs/s; its full/checkpoint medians were
2.202/2.133 seconds. These historical results provide context; the archived
pre-entry comparison above is the direct baseline for this follow-up.

#### New approved strategy-entry/session workload

No equivalent approved advanced-entry workload existed in the pre-entry tree.
These timings include start, causal decision, submission and genuine opening
delivery, plus the subsequent observations/fills. They are not directly comparable
to the standalone matching workload above.

| Entry workload | Timed inputs | Inputs/s | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| Market lifecycle | 50 | 90.9 | 9.74 | 28.03 |
| Resting limit lifecycle | 380 | 194.9 | 4.06 | 9.04 |
| Stop-market lifecycle | 70 | 164.5 | 4.57 | 9.31 |
| Stop-limit lifecycle | 80 | 167.5 | 4.41 | 10.99 |
| 32-fill partial lifecycle | 350 | 120.1 | 8.46 | 9.71 |
| Durable stop-market lifecycle + heartbeats | 97 | 56.5 | 17.86 | 23.77 |

| Entry processing phase | Samples | Median ms | p95 ms |
| --- | ---: | ---: | ---: |
| Market partial fill | 10 | 15.20 | 35.10 |
| Market final fill | 10 | 14.33 | 47.52 |
| Limit resting observation | 320 | 3.91 | 7.09 |
| Limit partial fill | 10 | 11.50 | 17.88 |
| Limit final fill | 10 | 9.81 | 57.92 |
| Stop-market trigger | 10 | 4.53 | 4.89 |
| Stop-market partial fill | 10 | 9.06 | 9.57 |
| Stop-market final fill | 10 | 8.84 | 9.26 |
| Stop-limit trigger | 10 | 4.51 | 6.70 |
| Stop-limit later nonmatch | 10 | 4.06 | 5.11 |
| Stop-limit partial fill | 10 | 9.68 | 15.93 |
| Stop-limit final fill | 10 | 9.65 | 12.19 |
| Partial sequence, intermediate fills | 310 | 8.49 | 9.77 |
| Partial sequence, final fills | 10 | 8.55 | 9.47 |

Durable entry processing took 1.717 seconds for seven lifecycle inputs and
90 heartbeats. Individual start/decision/submit/open/trigger/partial/final samples
were 21.38/17.86/20.53/18.67/18.04/30.24/23.77 ms; one sample per stage cannot
establish a stage latency distribution. Ten observed heartbeats had median/p95
17.79/19.03 ms; 80 stale heartbeats had 17.81/23.22 ms. Exact reference/recovery
comparisons passed, with fees 2.2 and remaining position quantity 2. The database
was 1,675,264 bytes, maximum output 13,001 bytes and effects 6,340 bytes.
The entry workload retains a position and bounded progress; existing exit workloads
close it and carry different payloads/economics. Their differing throughput and
database sizes do not establish a general persistence speedup.

#### Recovery

Medians in seconds, with three samples per mode. Checkpoints remain verified
anchors with full operational prefix replay, rather than a recovery complexity
improvement.

| Workload | Full before -> final | Checkpoint before -> final |
| --- | ---: | ---: |
| Existing single exit, 94 persisted inputs | 1.341 -> 1.442 (+7.6%) | 1.268 -> 1.368 (+7.9%) |
| Existing OCO, 94 persisted inputs | 2.008 -> 2.281 (+13.6%) | 1.901 -> 2.017 (+6.1%) |
| New advanced entry, 97 persisted inputs | unavailable -> 0.977 | unavailable -> 0.961 |

Final full/checkpoint sample seconds were:
single exit [1.451, 1.442, 1.365] / [1.338, 1.524, 1.368];
OCO [1.926, 2.281, 2.281] / [1.980, 2.017, 2.483];
entry [0.977, 0.930, 1.030] / [0.961, 0.966, 0.924].
All modes reconstructed exact snapshots; the entry run also verified the recovered
ACTIVE operator gate. Three shared-host samples do not isolate a causal recovery
regression, and the slower observed legacy medians remain a reported limitation.

#### Tail investigation and profiles

Normal GC remained enabled throughout. The prepared audit ran 100 independent
setup/exit chains, timing only trigger/partial/final processing: 300 inputs per
source and workload. Event construction and setup were outside timings. This is
an additional diagnostic workload, not a replacement for the main results.

| Audit phase | Median before -> final ms | p95 before -> final ms |
| --- | ---: | ---: |
| Single exit trigger | 5.17 -> 4.94 | 9.23 -> 7.67 |
| Single exit partial | 9.30 -> 8.61 | 15.04 -> 13.10 |
| Single exit final | 9.04 -> 8.83 | 14.94 -> 13.21 |
| OCO trigger | 14.92 -> 14.70 | 25.17 -> 18.21 |
| OCO partial | 27.64 -> 26.54 | 59.67 -> 36.71 |
| OCO final | 30.22 -> 29.03 | 56.95 -> 55.08 |

Single-exit audit throughput was 117.2 -> 123.2 inputs/s (+5.1%); OCO was
37.4 -> 39.9 (+6.7%). The large main-run throughput losses did not persist in
these larger samples. Single-exit timed processing saw 0 -> 1 major collections
and 0.067 -> 0.090 seconds of total GC; the slowest final event was 35.35 ms,
including a 26.07 ms major collection. OCO saw 14 -> 13 major collections and
0.625 -> 0.511 seconds total GC. Its slowest final event was 62.72 ms, including
32.30 ms of major GC; the baseline slowest partial event was 78.44 ms with
35.12 ms major GC. Some slow events had little GC time, so GC is not a complete
explanation. This evidence establishes variable tails and GC coincidence, not a
causal attribution of every earlier regression to GC or host scheduling.

Paired OCO profiles each processed 74 calls, with exactly 5,950 canonical_json
calls, 4,234 stable_id calls and 4,230/2,502 validation calls. Total function calls
were 24,729,588 -> 24,730,180, a 592-call increase (eight per processing call).
Profile elapsed time was 7.686 -> 6.996 seconds; canonical_json cumulative time
6.904 -> 6.323 seconds, about 90% of each run. The new entry profile made
39,568,367 calls in 12.795 seconds, with canonical_json cumulative 10.531 seconds
(about 82%) and stable_id 7.620 seconds. Cumulative times overlap and must not be
added. Profile timings include construction, snapshots, reference processing and
recovery, unlike the main processing timer; they identify existing serialization/
integrity costs rather than usable production throughput.

The main entry GC audit recorded 91/420/120/130/891 collections for
market/limit/stop-market/stop-limit/partial workloads, including 0/3/2/1/1 major
collections. Their maximum collection pauses were 10.12/49.29/23.51/21.84/24.52 ms.
Durable entry processing saw 238 collections, one major, 0.041 seconds total GC
and an 18.64 ms maximum pause. Allocation/serialization and collection tails remain
material even though the hot path avoids retained-history copying.

No repeatable significant processing regression requiring a source correction
was established by the audits/profiles. The main -10.0% single-exit durable result
and slower recovery medians remain unresolved observations, not dismissed as noise.
Financial correctness, Decimal rules, content hashes, validation, integrity checks,
GC behavior, commit durability and full replay were not weakened; no speculative
serializer or unrelated engine optimization was applied.

New processing appends immutable histories and updates fixed-depth retained indexes.
Session candidates share financial journals/indexes and roll back only their suffix.
Entry Effects/Checkpoint and SessionRecord do not encode retained entry history.
Public snapshots and recovery traverse retained state; storage/index memory and
full recovery still grow with retained inputs. The opening-gate map remains bounded
by the existing 32-kernel registry. Structural tests in section F establish bounded
new-input work at tested history lengths; these timing samples alone do not prove
asymptotic behavior.

Results are descriptive shared-host observations without confidence intervals,
host isolation or independent multi-run replication. Short lifecycle phases have
10 samples; durable stages have one; recovery has three. Reported p95s are empirical
order statistics, and 10-sample phase p95s are the observed maximum. No p99 or latency
SLA is established. Simulated allowance is not exchange liquidity. The implemented
bounds, full-history consumer snapshots and complete-prefix recovery remain limits.

Artifacts retained under ignored tmp/:
phase18f-entry-baseline-advanced.json, phase18f-entry-final-advanced.json,
phase18f-entry-final-benchmark.log and its validated .json copy,
phase18f-entry-baseline-oco.json, phase18f-entry-final-oco.json,
phase18f-entry-tail-baseline.json, phase18f-entry-tail-final.json,
phase18f-entry-oco-tail-baseline.json, phase18f-entry-oco-tail-final.json,
phase18f-entry-profile.txt, phase18f-entry-baseline-oco-profile.txt and
phase18f-entry-final-oco-profile.txt. JSON artifacts preserve unrounded samples,
workload parameters and economics.

Reproduction commands (using the existing embedded interpreter):
python -m benchmarks.phase18f_advanced --repeats 10 --observations 32
--persisted-events 90 --recovery-repeats 3;
python -m benchmarks.phase18f_oco with the same parameters;
python -m benchmarks.phase18f_strategy_entries with the same parameters.
Profiles use --observations 32 --profile <path>. Archived runs prepend the archive
src/root to sys.path before runpy.run_module. Tail audits use
tmp/phase18f-entry-tail-audit.py --source <archive-or-current> --chains 100 and
tmp/phase18f-entry-oco-tail-audit.py with the same options plus --oco.

### H. Compatibility, limitations and readiness

Supported: separately approved MARKET/LIMIT/STOP_MARKET/STOP_LIMIT entries with fixed
positive approved prices; GTC and market/limit IOC; declared full-fill or increment-
aligned simulated partial budgets; cancellation and non-liquidating stop; durable
admission/resting/trigger/partial/final/cancel recovery; existing single exits and
protective OCO after a completed entry. Existing v1 timing/journal and OCO financial
semantics remain preserved by regression and frozen-journal coverage.

Unsupported: dynamic order-price expressions, per-decision caller overrides,
DAY/GTD/FOK, IOC stops, order-level fixed fees, automatic brackets on unfinished
entries, exits/OCO while an entry remains partial or cancelled, repeated entries,
longer-than-approved/bounded lifetimes, multiple independent positions/accounts,
portfolio allocation, borrowing/FX/margin, live brokers and new MCP/LLM financial
permission. Existing exact Decimal rules still cancel unrepresentable nonterminating
weighted-average entry bases; no silent rounding is introduced. A future gap is
not guaranteed funded. V1 research evidence does not become an advanced execution
backtest, and trusted review/feed provenance is not cryptographic authentication.

The requested durable advanced strategy-entry integration is complete and ready
for the supported bounded paper-research scope. Correctness and durable recovery
are verified; performance readiness is qualified by the observed tails and legacy
persistence/recovery differences in section G. This is not a low-latency SLA or
live-trading readiness claim. Phase 19 and broader/live Phase 18 capabilities
remain outside this task.

### I. Final repository and execution status

The resumed task changed only this report and ignored tmp benchmark artifacts.
All incoming uncommitted implementation, tests and documentation were preserved.
The implementation and 3,828-test result were retained without rerunning the full
suite. The exact follow-up files remain listed in section A.

Final working tree: 20 modified tracked files and 21 untracked files (41 total),
including the prior Phase 18F/history/OCO work. Tracked-only git diff --stat:
20 files changed, 1,410 insertions and 165 deletions; this excludes all untracked
files, including this report and the new entry benchmark/tests. HEAD remains
504606b3c47dab589b56ebc4a6707492048bbf3f on master. git diff --check passed.
No commit, push, reset, clean or discard was performed.

The Windows sandbox helper failed before commands could start on resumption.
Approved escalated shell execution was used for the required local reads, benchmark
runs, report update and Git checks. No dependency installation, account connection
or financial authority change was required. All requested final benchmark/report
work is finished; no benchmark or verification is left pending.


## Q. Final Phase 18A–18F integration correctness audit

This audit supersedes the readiness conclusion and repository totals in section P.
Earlier test and benchmark measurements remain historical evidence. The audit
preserved the incoming uncommitted Phase 18F implementation and used its existing
benchmark artifacts; no benchmark was repeated, and no Phase 19 work was performed.

### A. Verdict

PASS for the documented bounded offline Phase 18A–18F scope after the correction
below and final regression. No blocking finding remains. This is correctness and
commit readiness for that scope, not completion of external/live services or a
latency guarantee.

### B. Findings ranked by severity

1. **Medium, fixed — session stop could fail with a pending single-exit cancellation
   at kernel capacity.** The single-exit terminal path unconditionally staged a new
   CancellationRequest. Once the ordinary kernel input bound was reached, an already
   pending request allowed only its acknowledgement, so stop raised PaperInputError
   instead of cancelling the remainder. The new test failed before the source fix
   with advanced capacity allows only cancellation completion at orders.py:172.
   There was no phantom financial effect; the failure prevented the promised
   non-liquidating terminal transition. An explicit acknowledgement remained possible
   when session capacity permitted it.

   The smallest correction is in paper/sessions.py: if the request is already
   pending at the input bound, stage its acknowledgement directly. Below capacity,
   preserve the existing request/ack sequence and previously valid v2 canonical
   stop outputs. No capacity, causation, funding, financial, integrity or durability
   check was removed. The same terminal branch serves stop/fail; regression tests
   exercise stop.

   Three new cases in tests/persistence/test_advanced.py cover an unfilled exit
   with 128 retained inputs, a partially filled exit with 129 inputs (the request
   uses reserved cancellation capacity), and a below-capacity compatibility case.
   They assert cancellation, unchanged account/fees/PnL/position, retained prior
   fills, exact retry results, durable/reference equivalence and full/checkpoint
   recovery of the stopped owner. No existing assertion was weakened.

2. **Low, fixed — stale capability/completion claims contradicted current Phase 18F
   documentation.** The runtime document still described local persistence,
   advanced matching and executable exits as future work; the persistence document
   called them unsupported; the roadmap retained a blanket incomplete statement.
   The three documents now distinguish the legacy phase owner from current
   opt-in bounded capabilities and retain all broader/live/portfolio exclusions.
   Historical Phase 18E test numbers are explicitly labelled historical. This was
   a documentation correction, verified against the actual contracts and owners.

3. **Informational, retained — performance qualifications.** GC pauses and observed
   legacy persistence/recovery slowdowns remain documented in section P.G. No
   correctness failure, weakened validation or supported-scope execution deadline
   was identified. These measurements do not establish a low-latency service SLA.

### C. Evidence for critical invariants

| Invariant | Code traced | Regression evidence reviewed |
| --- | --- | --- |
| One atomic order/account/session outcome | accounts._prepare_apply/_publish; sessions._candidate_owners/_process/_rollback_account; durable _commit_candidate | Financial/index/publication failures preserve the old root and undo only candidate suffixes; entry/OCO fault-window tests cover before-commit, commit-armed, after-commit and publication interruption |
| Exact quantities, fees, basis, collateral and PnL | accounting.transition_account; account_models.exact_context and snapshot/position reconciliation; advanced price/slippage policies | Hand-calculated long/short partial fills, funding gaps, fee/reservation release, nonterminating-basis rejection and Decimal-context independence |
| No over-reduction or reversal | Account settlement validates live direction and quantity; reduce-only admission checks position ownership; OCO settlement binds live remainder | Oversize/wrong-side/wrong-position negatives, mixed target/stop fills and final shared-quantity reconciliation |
| No duplicate financial effects | Canonical retained input/causation indexes; session retries return before clock or capacity advancement; operational new-owner replay | Exact input and financial retries, conflicting identities, redelivered quote budgets, stopped/recovered retries and all three new stop cases |
| Exact strategy and additional timing authorization | admit_strategy; AdvancedStrategySessionConfig.approved_configuration; runtime intent construction; adapter submit/open/process_quote | Draft/revised/digest/config/approval/eligibility negatives, immutable original v1 approval wires and no record/MCP/agent authority bypass |
| No future-data or opening inference | Typed ReplayEvent envelope; runtime availability/offset evaluation; on-time close and adjacent locked OpeningDelivery gate | Future/unavailable/late/stale/paused inputs, missing/forged/missed openings, offset dependencies and equal-time ordered trigger/later-fill tests |
| Deterministic two-child OCO | create_group/process_group; stop-first evaluation, cap-zero sibling preparation, exact withdrawal reconciliation; account publication validates both heads and live position | Stop/target/mixed partial and complete exits, cancellation races, independent-child rejection, fault injection and full/checkpoint recovery |
| Recovery cannot trust a forged head | Store canonical decode/index/chain/tail validation; fresh admission; both recovery paths replay operational semantics | Hash-consistent forged entry/exit/OCO heads fail recovery; frozen original v1 and pre-OCO v2 records retain exact wires |
| Ambiguous commit and operator gates fail closed | PREPARING/COMMITTING/COMMITTED state shared before commit; poisoned snapshot/record reads; recovered-ACTIVE new-input gate | Unknown/postcommit/publication failures require new-owner recovery; historical retries cannot clear the recorded pause/resume requirement |
| Ordinary work does not copy or scan growing history | History append, 64-level RetainedMap updates, bounded heads, candidate suffix staging; active scans limited to two OCO children and at most 32 account kernels | Structural tests prohibit retained-history iteration/full snapshots on processing and verify fixed branch counts and shared financial-index identities |

The economics reconciles balance = starting capital + realized PnL - fees,
available = balance - reservations - position collateral, and equity = balance +
unrealized PnL. Exact partial-entry continuation binds strategy, direction and
original entry transaction. Exit settlement compares the proposed reduction with
the live position; OCO remaining quantity is original minus both children fills,
and each child withdrawal equals its sibling fills.

Preparation, canonical encoding and immutable-root allocation precede publication.
The durable input, output, financial inputs/effects, optional checkpoint and head
commit in one transaction before the session root swap. Confirmed precommit
rollback permits retry; ambiguous or committed-unpublished state revokes readable
execution state. Recovery creates fresh owners rather than applying historical
fills to an existing settled account. ACTIVE restoration accepts exact retries
but requires a newly recorded pause/resume before new market execution.

The new stop correction adds only a constant-time history length comparison on
the terminal path. It does not change ordinary matching, authorization, fee/PnL
calculations, OCO coordination, journal format or recovery validation. Existing
successful below-capacity stop wires are preserved deliberately.

### D. Supported-scope limitations

Support remains one serialized trusted local Python session, one strategy/account/
instrument and one position, prefunded linear EQUITY research units, raw causal
MID/OHLC strategy features and recorded provenance. Original v1 strategy timing
remains next-open only. Advanced entries need separate exact v2 execution approval/
eligibility and explicit v3 session selection, actual close submission and genuine
adjacent opening evidence before later fresh quotes can execute.

Fixed market/limit/stop-market/stop-limit orders, GTC and market/limit IOC,
declared simulated partial budgets, cancellation, non-liquidating stop and durable
recovery are supported within input/retention bounds. Stops require a later
delivery after triggering. Reduce-only exits and the two-child protective OCO
require an entry that is FILLED; cancellation of a partial entry may leave a
position without an executable protective order. This is an explicit exclusion,
not automatic bracket protection.

Dynamic price expressions/caller overrides, IOC stops, DAY/GTD/FOK, order-level
fixed fees, repeated entries, automatic partial-entry brackets, multiple independent
positions, borrowing/FX/margin, portfolio allocation, external feed services, live
brokers and MCP/LLM financial authority remain excluded. Nonrepresentable exact
weighted-average bases are denied rather than rounded; future gaps are not
guaranteed funded. Producer/reviewer identities and hashes are trusted application
records, not signatures or authentication.

### E. Test and benchmark evidence reviewed

- Incoming final result: 3,828 passed in 589.14 seconds; no skips/xfails.
- Defect reproduction: one new case failed in 4.95 seconds before the source fix;
  76 cases were deselected by the focused selector.
- Affected regression: all three new cases passed in 18.63 seconds.
- Final post-correction full regression: 3831 passed in
  633.52 seconds; zero failures, errors, skips or xfails. JUnit was
  independently checked for testcase count and absence of non-pass elements.
  This one full rerun was required by the concrete shared-session defect; it was
  not a repetition of the previously completed suite without cause.
- Existing advanced, approved-entry and OCO JSON benchmark artifacts, paired normal-GC
  tail audits and entry/OCO profiles in section P.G were reviewed. All previously
  completed financial/reference/recovery assertions remain valid. Benchmark
  workloads do not invoke the corrected pending-exit session-stop path, so no
  repeat benchmark was warranted.
- Advanced/OCO durable baseline/final artifacts preserve fees 3.4, realized PnL -15
  and database sizes 2,285,568/3,317,760 bytes respectively. New entry durability
  preserves fees 2.2, position quantity 2 and a 1,675,264-byte database.
- Audits of 300 timed exit inputs measured single-exit 117.2 -> 123.2 and
  OCO 37.4 -> 39.9 inputs/s. Larger audits did not reproduce the smaller main-run
  20–24% exit-throughput losses. GC remains material and is not a complete
  explanation of every slow event. Legacy durable single-exit throughput -10.0%
  and recovery medians +6.1–13.6% remain unresolved observations.

Logs: tmp/phase18-final-audit-repro.log, tmp/phase18-final-audit-targeted.log,
tmp/phase18-final-audit-full.log and tmp/phase18-final-audit-full.xml.
No completed benchmark was rerun.

### F. Remaining risks

No blocking integration defect remains in the reviewed scope. Correctness tests
and deterministic replay do not establish throughput under controlled load or
hardware-failure durability. Shared-host tail/recovery samples are small and have
no confidence intervals or p99/SLA. Serialization/integrity validation remains
expensive; normal GC was not disabled to improve numbers.

Snapshots and recovery intentionally materialize or traverse retained prefixes;
journal/index storage grows with retained inputs. Both advanced recovery modes
verify complete operational prefixes, so checkpoints are verified anchors rather
than asymptotic replay accelerators. Operators must manage declared order/session
capacity and use explicit cancellation/stop; unsupported partial-entry protection
and unfunded future gaps remain visible capability limits.

Durability is local SQLite DELETE/EXTRA/EXCLUSIVE with one writer and supported
filesystem behavior. Whole-file replacement/deletion, hostile local code,
authentication, distributed coordination, broker side effects and media guarantees
are outside the integrity claim. Broader/live deployment requires separate work.

### G. Git status and commit readiness

The audit changed only:

- src/quantlab/paper/sessions.py — the bounded pending single-exit stop correction.
- tests/persistence/test_advanced.py — three added regression cases.
- docs/paper-strategy-runtime.md — legacy/current capability distinctions.
- docs/paper-session-persistence.md — current advanced support and historical counts.
- docs/roadmap.md — bounded Phase 18A–18F completion statement.
- docs/phase18f-report.md — this audit and its evidence.

All incoming dirty files remain. Final status: 20 modified tracked and 21 untracked
files, with no staged changes. Tracked-only diff: 20 files, 1,435 insertions and
177 deletions; it excludes all untracked content, including the report and new
Phase 18F test/benchmark modules. HEAD/master remains
504606b3c47dab589b56ebc4a6707492048bbf3f. Final git diff --check passed.

Ready for a reviewed commit of the declared bounded Phase 18 scope. No commit,
push, reset, clean or discard was performed. The broken sandbox helper required
approved local escalated shell execution. No dependency, external account,
financial permission or Phase 19 capability was added. Work stops after reporting.
