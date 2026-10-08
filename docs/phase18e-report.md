# Phase 18E corrected implementation and verification report

## A. Baseline, scope and files

Baseline: `3561ebb97f5910898cb412a5ea96566f17413e0a` on master. Phase 18E remains
uncommitted. The independent audit identified three HIGH defects, one MEDIUM recovery
CPU concern and one LOW benchmark-methodology concern. All three HIGH defects are
corrected, with failing regressions recorded before the production changes.

Relative to the baseline, four tracked files are modified: README.md,
docs/architecture.md, docs/roadmap.md and src/quantlab/paper/sessions.py. Ten new files
are present: src/quantlab/persistence/{__init__,contracts,store,paper}.py,
tests/persistence/{__init__,test_paper,test_recovery_defects}.py,
benchmarks/phase18e_persistence.py, docs/paper-session-persistence.md and this report.

This correction round changes the persistence implementation, adds the defect tests,
improves the benchmark, and updates relevant documentation. The existing Phase 18A–18D
financial engines and the existing test files are unchanged by this correction round.
No dependency, live trading, broker, MCP execution authority, autonomous agent,
distributed writer coordination or Phase 18F matching feature was added. No commit or
push was performed.

## B. Root causes and corrections

### HIGH 1: post-commit return/handoff safety

The old owner set a pending flag only after SQLitePaperStore._append returned. An
interruption at the store return or before that assignment could commit the opening
fill while leaving readable memory at the previous reservation/account version.

The store now owns an explicit IDLE / PREPARING / COMMITTING / COMMITTED state machine.
COMMITTING is set before invoking COMMIT. COMMITTED remains set across store return,
owner handoff and memory publication. DurablePaperSession.recovery_required and its
read guards consult this shared state, independently of the exception-handler poison
flag. Only successful complete publication acknowledges the commit and clears it.

BaseException handlers include KeyboardInterrupt. A confirmed rollback before the
commit boundary permits retry; interrupted/failed rollback requires recovery. A
commit attempt remains ambiguous even if a subsequent rollback returns successfully.
The store return itself is within the guarded transaction path. Manual checkpoint
transactions use the same uncertainty boundary.

Locations: src/quantlab/persistence/store.py (_TransactionState, _append,
_rollback_transaction, _publication_complete, _save_checkpoint) and
src/quantlab/persistence/paper.py (recovery_required, _readable, _process, checkpoint).
The existing sessions.py rollback/publication seam remains compatible.

### HIGH 2: recovered-ACTIVE operator gate

The old code cleared the gate using the state in a returned historical outcome.
Retrying an old PAUSE receipt could therefore clear the gate while current state was
ACTIVE after recovery.

The gate now clears only when a new operation increases the current committed count,
is an explicitly permitted pause/stop/fail command, and publishes a non-ACTIVE current
state. Historical START/PAUSE/RESUME, inactive feed, bar, quote and opening retries
return their original results without altering the current gate. Identity conflicts
preserve it. A new pause followed by a new resume is required for ACTIVE processing;
PAUSED/STOPPED/FAILED restrictions remain intact.

Location: src/quantlab/persistence/paper.py, DurablePaperSession._process.

### HIGH 3: checkpoint semantic verification and opening acknowledgement

The old restoration checked many fields against the same retained outcomes but did
not establish complete ACTIVE/fresh event semantics. An opening whose stored reason
was changed to observed, with regenerated hashes, could be accepted and omit its ACK.
Full replay rejected that history.

Checkpoint prefix restoration now uses the existing StrategyRuntime, account and
strategy-order adapter engines. It reevaluates bar decisions and independently
compares lifecycle/feed state, transitions, reasons, command references, decisions,
orders, financial events, account snapshots and complete Effects. Close/open causality,
risk, pricing, settlement, cancellation and release remain owned by the existing engines.
The account generates opening acknowledgement identity/provenance atomically with
execution; no acknowledgement is fabricated from a stored reason string.

Every stored SessionRecord field is checked through typed identity/provenance checks
in store.verify plus generated operational comparisons during restoration. Checkpoint
roots/counts/references must agree. Invalid history refuses recovery and returns no
owner; it is not repaired. Verified prefix SessionRecords are reused rather than
regenerated, and only suffix inputs run through ordinary session publication.

Location: src/quantlab/persistence/paper.py, _restore_checkpoint. This deliberately
replaces the earlier policy of trusting hash-verified prefix decisions: checkpoints
now reevaluate prefix strategy semantics as well.

## C. Transaction and recovery guarantees

One accepted input commits its complete operation/outcome/effects, optional scheduled
checkpoint and metadata head in a single explicit SQLite transaction. DELETE rollback
journaling, EXTRA synchronization, exclusive locking, foreign keys, exact schema/app
version checks and strict canonical typed decoding remain enabled. Journal operations
are append-only through the supported API; only bounded accelerator rows are pruned.

While commit outcome is unknown or committed memory is unpublished, authoritative
snapshot/record access and processing require fresh-owner recovery. An interruption
after complete publication and acknowledgement may lose the response; a retained
identity retry returns that already committed result. Historical snapshots obtained
before an operation remain immutable historical views.

Recovery constructs fresh admitted owners rather than applying fills to an existing
settled account. Committed inputs reconstruct once, incomplete transactions do not
apply, and exact retries add no fees, fills, holds, releases or acknowledgements.
Every retained record and checkpoint is verified. Local exactly-once state application
is not broker or distributed exactly-once execution.

## D. New failure injection and regression tests

The initial three targeted tests failed on the original implementation; evidence is
in tmp/phase18e-corrections-before.log. The final new file contains 66 passing cases:

- KeyboardInterrupt before the commit boundary, after arming it, in commit wrappers
  before/after the real commit, immediately before/after the commit call, at store
  return, append handoff, owner return, and before/after memory publication.
- Real SQLite virtual-machine interruption during COMMIT, using a one-shot progress
  handler armed by the COMMIT trace callback. Both fill and release cases require
  recovery, reconstruct only the prior three operations, and reconcile safely.
- Interrupted rollback, shared-state read blocking before the owner exception handler,
  and manual checkpoint return interruption.
- Controlled subprocess os._exit at commit-armed, post-commit and store-return boundaries
  for both nonzero-fee/slippage fills and pending-order cancellation/release.
- Historical START/PAUSE/RESUME, paused feed, bar, quote and opening retries under both
  recovery modes, identity conflicts, operator acknowledgement, and terminal restrictions.
- Hash-consistent opening reason changes, missing opening effects, conflicting order
  outputs, cancellation/release omissions, invalid bar/quote reasons, contradictory
  strategy dependencies, command-reference mismatches and opening-causality changes.
- Codec strictness with/without duration adaptation and cached configuration digest
  reuse while record/index corruption is still refused.

Real SQLite files and newly opened owners are used. Assertions cover readable-state
blocking, journal cardinality, account equivalence, nonzero fees, one fill or release,
reservation cleanup, one opening ACK, retries and safe reconciliation. Existing tests
were not weakened. No wall-clock thresholds, skip or xfail was introduced.

## E. Checkpoint/full-replay equivalence evidence

Both modes reject each new semantically inconsistent hash-valid history. Corruption
fixtures rehash operation/output/checkpoint references to exercise semantics beyond
outer digest rejection. Failed recovery leaves the corrupted database bytes unchanged.

Valid prefix/suffix and full recovery continue to match complete reference snapshots,
including records, decisions/features, financial/account state and opening provenance.
Coverage includes existing causal feature offsets 0/1/3/100, long/short positions,
nonzero costs, lifecycle/capacity shutdown, and exact retries. Each repeated benchmark
recovery also matches an independently processed in-memory reference. Final benchmark
record identities match the previous implementation for both 5,000-event workloads.

## F. Exact regression results

Windows embedded CPython 3.11.9, pytest 9.1.1, Pydantic 2.13.5, SQLite 3.45.1;
existing MCP/LangGraph dependencies remain unchanged. The workspace virtual environment
still points to a missing base interpreter; the existing working embedded interpreter
was used. No package was installed.

| Required group | Final result |
| --- | --- |
| New defect regressions | 66 passed in 29.05 s |
| All Phase 18E persistence | 202 passed in 56.99 s |
| Existing paper 18A–18D | 565 passed in 28.62 s |
| Backtesting/risk | 468 passed in 1.97 s |
| Strategy/features/ML | 279 passed in 1.89 s |
| Validation/analytics | 186 passed in 1.94 s |
| Orchestration/MCP | 753 passed in 191.90 s |
| Full suite on final source | 3,240 passed in 298.84 s |
| git diff --check | Passed |

No skip or xfail occurred. An earlier correction full run passed 3,238 tests in
312.14 s before the two SQLite-VM interruption cases were added. The final full run
includes all 66 new regressions in addition to the original 3,174 tests. Evidence:
tmp/phase18e-corrections-regressions.log and tmp/phase18e-corrections-final.log.

## G. Recovery performance and benchmark quality

Command: python benchmarks/phase18e_persistence.py --counts 5000 --recovery-repeats 3.
The updated benchmark uses three fresh-owner measurements per recovery mode, alternating
full/checkpoint then checkpoint/full order, and reports medians, ranges and raw samples.
Integrity verification, checkpoint reference validation, prefix restoration and ordinary
replay processing are timed separately. Other recovery time includes admission,
checkpoint decoding and outcome/effect comparison. Marginal phase medians need not
sum to the total median. Consumer snapshots remain outside timing.

Persistence still has one measured processing run per workload, matching the earlier
methodology; its throughput is not a repeated-run median. Evidence/event generation,
explicit checkpoint creation and consumer snapshots remain excluded. Scheduled
checkpoints, strict encoding/validation and per-operation durable commits remain included.

Environment: Windows-10-10.0.26300-SP0, CPython 3.11.9 64-bit, SQLite 3.45.1;
CPU/machine identifiers unreported. One serialized writer, local workspace tmp files,
DELETE/EXTRA/EXCLUSIVE, checkpoint interval 1000 and retention two. Tests, profiling
and the final benchmark ran sequentially. This shared host and warm filesystem cache
are not an isolated hardware benchmark. Timing instrumentation adds small overhead.

| 5,000 market events | Prior persisted events/s | Corrected events/s | Prior full recovery s | Corrected full median s | Prior checkpoint s | Corrected checkpoint median s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Quotes | 111.9 | 96.6 | 21.07 | 23.02 | 13.69 | 14.40 |
| Entry/bars | 58.7 | 48.4 | 60.38 | 55.86 | 37.36 | 46.34 |

Full recovery ranges: quotes 22.33–25.30 s; entry/bars 54.76–78.04 s. Checkpoint
ranges: quotes 14.19–14.66 s; entry/bars 44.44–49.64 s. All six recoveries per
workload matched the reference snapshot. Database sizes remain 31,780,864 bytes
(quotes) and 85,512,192 bytes (entry/bars), with the same final record identities:

- Quotes: 8078fa3982846c7f4c34575463dc7e5ed1c1ca27f3a05b14e94fe33ca872cbf9
- Entry/bars: 1078af4ea5535480c09fbfd86e7eef45602e22a673f118f809386780887e7bc0

Both default benchmark fee totals remain zero; nonzero costs are covered separately.

| Checkpoint recovery phase medians | Quotes s | Entry/bars s |
| --- | ---: | ---: |
| Integrity verification | 13.11 | 33.62 |
| Prefix engine restoration/verification | 1.06 | 11.09 |
| Ordinary suffix processing | 0.0014 | 0.0030 |

The latest checkpoint represents ordinal 5000; one suffix input is ordinarily replayed.
Prefix inputs are verified through the original engines once, not also replayed through
ordinary session processing. Checkpoints avoid prefix SessionRecord regeneration and
candidate-owner copies, not required prefix semantic checks.

The entry checkpoint median is 24.0% slower than the earlier, insufficiently verified
checkpoint path. Prefix decision/operational validation is now part of its guarantee;
this is an intentional correctness cost. Full entry recovery is 7.5% faster in these
observations, while quote full/checkpoint medians are 9.3%/5.2% slower. These are separate
runs, not a controlled A/B attribution of individual optimizations.

Raw persistence throughput declined 13.6%/17.6%. The unchanged in-memory reference also
slowed from 7.02 to 8.29 s for quotes and 13.71 to 18.49 s for entry/bars (18.2%/34.8%),
and one full entry recovery took 78.04 s against a 55.86 s median. This demonstrates
substantial run variability. No extra per-event SQL transaction or synchronization was
introduced; exact causal attribution of throughput differences is not established.

Raw old/new benchmark evidence: tmp/phase18e-benchmark.json and
tmp/phase18e-corrections-benchmark.json.

## H. Profiling, safe optimizations and explicit follow-up

A corrected 400-event entry checkpoint profile recorded 17,304 canonical_json calls
and 14,057 stable_id calls. Integrity verification occupied 13.33 of 17.29 profiled
recovery seconds (about 77%); restoration occupied 3.89 s. Canonical serialization
appeared in 14.89 s of cumulative time (about 86%). These overlapping times must not
be added. cProfile distorts latency; these timings are CPU attribution, not benchmark
latency. The earlier audit counted 11,706/8,864 calls using the former prefix policy.
The corrected path performs additional prefix strategy and operational checks, so net
call counts increase despite eliminating specific redundant work. The new real-file
profile also retains a midpoint checkpoint. Evidence: tmp/phase18e-corrections-profile.log.

Small safe optimizations implemented:

1. Derive replay configuration digest once from the validated immutable manifest at
   binding, instead of rehashing it per appended/verified entry. A regression counts
   one derivation and still verifies that index corruption is rejected.
2. Reuse the strictly checked original JSON wire for Pydantic decoding when no timedelta
   adaptation is required, eliminating a redundant JSON dump. Duplicate-key, floating/
   nonfinite number, schema/type and canonical spelling checks remain in place.
3. Remove the second whole-runtime snapshot validation at checkpoint restoration; normal
   snapshot construction still performs the existing typed binding checks.

No O(n²) recovery was found in the supported bounded single-entry scope. Ordinary
processing does not serialize growing full session history. All-record verification
is O(n), prefix/suffix engine work is linear with indexed feature dependencies, and
retained-checkpoint prefix scans are O(k*n) with k bounded to eight. SQLite synchronous
commit cost is expected durability overhead; repeated canonicalization/nested model
validation is an additional CPU bottleneck, clearly present even in RAM audit probes.

Explicit follow-up before expanding Phase 18F state: profile and design reuse of
strictly validated canonical representations or versioned bounded-state references
for repeated kernel/intent/account structures. Any such change needs independent
integrity/financial-equivalence regressions and format compatibility, rather than an
unchecked cache or skipped validators. Duplicate quick_check and bounded checkpoint
prefix scans remain; standalone verify still checks database consistency. No broad
codec/financial-engine redesign was attempted in this correction round. Do not improve
throughput by relaxing durability, batching away operation atomicity or changing
financial arithmetic/causality.

## I. Risks, limitations and repository status

Logical interruption and process-death tests are not physical power-loss certification.
Faulty media, false sync acknowledgements, broken filesystem locking, lost/replaced
files or trusted-artifact loss remain outside the guarantee. The SQLite-VM test
interrupts COMMIT execution but does not emulate a device failure midway through fsync.
Hashes detect supported corruption and semantic replay rejects inconsistent outcomes;
they do not authenticate a complete hostile rewrite of all inputs/records/anchors.
Remote filesystems, concurrent writers, broker effects and distributed durability are
unsupported. No latency SLA is asserted; the remaining CPU follow-up is explicit.

HEAD is unchanged at the baseline. Four tracked modified files and ten new Phase 18E
files remain uncommitted; ignored tmp evidence is additional local verification output.
Git diff --check passed, and new source/test/benchmark files were checked for trailing
whitespace. No commit, push or package installation was performed.

## J. Readiness

All three HIGH defects are corrected and covered by final passing regressions.
The MEDIUM CPU concern remains an explicit measured follow-up; the LOW benchmark
methodology concern is corrected. Financial behavior, durable configuration and
Phase 18A–18D interfaces remain compatible.

READY TO COMMIT — PHASE 18E
