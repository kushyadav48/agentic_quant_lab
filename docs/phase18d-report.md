# Phase 18D implementation report

Implemented 2026-10-08 against master at b49c664. The baseline working tree was
clean. No commit or push was requested or performed.

## A. Files created/modified

Created:

- `src/quantlab/paper/session_models.py`: strict frozen session, command, event,
  configuration, clock/feed and content-addressed audit contracts.
- `src/quantlab/paper/feed.py`: pure clock, recorded feed and freshness transitions.
- `src/quantlab/paper/sessions.py`: exclusive lifecycle/replay owner and shared
  candidate publication over the existing account/runtime/order adapter.
- `src/quantlab/replay.py`: validated stored-dataset adapter and isolated replay verification.
- `tests/paper/test_sessions.py`: Phase 18D regression coverage.
- `benchmarks/phase18d_replay.py`: reproducible four-workload offline benchmark.
- `docs/paper-market-replay.md` and this report.

Modified:

- `src/quantlab/paper/__init__.py`: explicit additive public exports.
- `README.md`, `docs/architecture.md`, `docs/roadmap.md`: accurate 18D status/contracts.

Local ignored verification evidence is in `tmp/phase18d-verification.json`,
`tmp/phase18d-*.log`, `tmp/phase18d-benchmark.json` and
`tmp/phase18d_verify.py`. No dependency, existing test, MCP tool, order engine,
account engine, strategy engine or risk engine was modified.

## B–F. Architecture, causality, integration and guarantees

`PaperSession` creates fresh exclusive account/runtime/adapter owners and repeats
Phase 18C admission. CREATED/ACTIVE/PAUSED/STOPPING/STOPPED/FAILED transitions are
explicit, strictly validated, idempotent and bound to recorded command reasons.
Pause suppresses strategy/order processing while retaining delivered inputs. Stop
cancels an accepted entry using the existing account-owned cancellation/release
transaction; open positions remain under a documented non-liquidating policy. One
terminal record beyond ordinary capacity guarantees shutdown remains possible when
a bounded journal is full. Exact typed lifecycle commands are retained in audit.

Versioned immutable event envelopes retain canonical observations, source/dataset
version attribution, market occurrence, availability, delivery, effective UTC time
and sequence. New sequences increase and times never rewind; equal times remain
ordered by sequence. Exact duplicates return prior results; identity conflicts fail.
Older delivered occurrences never replace newer market occurrences. Completed-bar
chronology and Phase 18C on-time restrictions remain enforced. No future observation,
bar OHLC or caller-supplied feature is exposed to the runtime.

Feed interruption, resumption, heartbeat and explicit exhaustion are recorded.
The explicit nonnegative stale threshold compares logical processing time with
recorded occurrence time; threshold equality is fresh. Missing/stale/interrupted/
exhausted data blocks new evaluation/submission/execution without altering financial
state. Resumption requires fresh data. There are no wall-clock trading decisions.

The historical adapter rechecks existing immutable dataset identity/quality and
preserves row provenance. It models delivery at availability because storage lacks
arrival evidence. It neither calls providers nor infers execution openings from
ticks/bar OHLC. Declared synthetic provenance supports the genuine adjacent on-time
locked-opening fixture, retaining the existing exact approval/eligibility, MID raw
features, quantity restrictions, entry-only risk/pricing/account settlement.

Private candidate owners reuse Phase 18A–18C transactions. Account/order/financial/
opening provenance remains atomic in the candidate. Required audit serialization,
record/root allocation and identity/journal indexing are staged before the one
session publication. Failure removes incremental runtime/journal/index suffixes and
exposes the old root; exact retry remains safe. Snapshot reads during staging retain
the old account/order/opening/feed/runtime/outcome state. Financial state cannot be
committed without its required session provenance. Reentrant mutation is rejected.

Recorded outcomes bind the configuration, original market envelope, actual decisions/
features/dependencies, order/risk/fill/cancellation records, financial changes,
resulting account/feed and lifecycle reasons into a deterministic hash chain.
Identical input/configuration verification uses two fresh financial owners and binds
final admission, intent, order and opening projections as well as the audit chain. Future
suffixes do not change prior immutable snapshots. There is no per-event full-history
copy/serialization, network activity, LLM inference, worker or sleep.

The atomicity guarantee is serialized and in memory; it does not cover process crashes,
concurrent callers or durable recovery. See [the full contract](paper-market-replay.md).


| Requirement area | Implemented boundary |
| --- | --- |
| Lifecycle and identity | Strict commands/state transitions, exact retries, conflicts, pause and safe non-liquidating stop/fail |
| Clock and causal order | Recorded UTC envelope, monotonic effective/delivery time, increasing sequence, no suffix lookahead |
| Historical replay | Existing stored-dataset integrity and provenance; observation delivery without invented openings |
| Feed and freshness | Explicit heartbeat/interruption/resumption/exhaustion; nonnegative versioned thresholds and fresh-data recovery |
| Strategy/account integration | Existing exact admission, causal runtime, entry adapter, risk, pricing and owned settlement |
| Atomicity and replay | Unexposed candidate owners, one session publication, reversible indexes, independent full outcome verification |
| Audit and performance | Typed original inputs/actual engine outputs, hash chain, incremental history and four reproducible workloads |
| Scope and next phases | In-memory offline guarantee; persistence, advanced orders, portfolios and external services deferred |

## G. Regression verification

Validation uses the existing local embedded Windows CPython 3.11.9 (64-bit),
pytest 9.1.1, Pydantic 2.13.5, MCP 2.3.0 and LangGraph 1.2.12. The standard workspace
venv launcher points to a missing base Python; the embedded interpreter loads the
same installed workspace packages/source and runs the actual full tests.

| Required group | Result |
| --- | --- |
| New Phase 18D | 95 passed |
| Existing paper (18A–18C) | 470 passed |
| Backtesting/risk | 468 passed |
| Strategy/features/ML | 279 passed |
| Validation/analytics | 186 passed |
| Orchestration/MCP | 753 passed |
| Full pytest suite | 3038 passed in 183.70s (0:03:03) |
| git diff --check | Passed |

No tests were skipped, xfailed, weakened or excluded from the full suite. The separate
existing-paper group excludes the new file only because it was already run as the
first required group; the full suite includes every test. Coverage includes realistic
negative feed/causal/admission/risk/financial cases, unfunded cancellation, pending
stop, independent replay/prefix preservation, Decimal context isolation, data-integrity
checks, no external effects and failure injection at validation, feature indexing,
financial preparation/index/publication, audit serialization/staging/root allocation
and before/after session publication. Exact retry/conflict behavior is checked after
failures and terminal operations.

## H. Performance observations

Command: `python benchmarks/phase18d_replay.py --counts 1000 5000`.
Environment: Windows 10 API platform string `Windows-10-10.0.26300-SP0`, embedded
CPython 3.11.9 MSC 64-bit. CPU/model and machine strings were unavailable in this
execution environment. The benchmark shared the host with targeted regression work;
these are measured observations, not an isolated hardware throughput guarantee.

Configuration: version 1 replay and stale policy; stale threshold 0 microseconds;
flat prefunded USD EQUITY account; existing Decimal risk/pricing; one declared
synthetic locked adjacent opening for the entry workload. Maximum runtime events are
1,000/5,000 respectively. Each workload contains the listed market events plus one
start command; start is outside the timed region. Dataset/evidence/event creation and
explicit consumer snapshot materialization are excluded. Raw-offset-bars uses the
existing raw close feature at offset 100; entry-and-bars settles one entry, then
processes subsequent bars under the existing single-entry restriction.

| Workload | Market events | Processing seconds | Events/second |
| --- | ---: | ---: | ---: |
| quotes | 1,000 | 2.856 | 350.1 |
| quotes | 5,000 | 8.613 | 580.5 |
| bars | 1,000 | 2.926 | 341.8 |
| bars | 5,000 | 14.240 | 351.1 |
| raw-offset-bars | 1,000 | 3.078 | 324.9 |
| raw-offset-bars | 5,000 | 18.353 | 272.4 |
| entry-and-bars | 1,000 | 3.528 | 283.4 |
| entry-and-bars | 5,000 | 15.401 | 324.7 |

Observed 5,000-event workload rates range from 272.4 to 580.5 events/second. The
implementation uses incremental bar/feature journals and a constant-size single-entry
financial candidate; benchmark timings are not correctness thresholds. Large snapshot
materialization/serialization is an explicit consumer cost, subject to existing bounded
canonical-serialization limits. Full raw measurements/configuration/digests are retained
locally in `tmp/phase18d-benchmark.json`.

## I–K. Limitations, git status, risks and readiness

Supported: bounded offline observation/evaluation; declared synthetic opening execution;
one admitted strategy, instrument and executable entry; explicit session controls;
serialized coordinated financial/provenance publication and in-memory replay verification.

Remaining limitations:

- Historical datasets do not prove openings or retain network arrival chronology.
- No automatic post-fill valuation marks: subsequent quotes are retained observations;
  the open position keeps its last committed valuation until a future supported owner
  operation. Stop never fabricates liquidation or valuation.
- Interrupted/paused/missing openings are not recovered by invented catch-up prices.
- Exhausted feeds are terminal; pending reservations require explicit stop/fail.
- Producers must supply globally increasing session sequences and respect generated
  kernel submission sequence room. No calendar adjacency inference exists.
- Source labels/research attestations are trusted local records, not authenticated
  network clients or cryptographic signatures. Private owner internals are not an API.
- State is bounded and in memory, with no durable store, crash recovery, advanced order,
  partial fill, exit/reversal, portfolio allocator, live feed/broker, API/dashboard or
  new MCP financial authority. Phase 18E and later phases remain planned.

Git status at completion: master remains at b49c664; four tracked files modified and
eight new files, all uncommitted. `git diff --check` passes; no existing tests or financial
engine implementation changed. The worktree contains only the requested source/test/
benchmark/documentation changes plus ignored local verification evidence. No push.

Readiness: the bounded Phase 18D offline contract is implemented and regression-verified.
The main future integration risk is preserving admission and unified session/financial/
opening publication when adding Phase 18E durable recovery. Live streaming and arbitrary
historical execution remain outside this readiness assessment. The deterministic typed
audit chain is available for a future trade journal/Visual Strategy Debugger without
an LLM explanation layer.
