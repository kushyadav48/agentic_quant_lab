# Durable local paper sessions (Phase 18E)

Phase 18E adds opt-in persistence for the supported serialized, single-process,
one-strategy, one-instrument, single-entry paper session. Financial behavior remains
owned by the Phase 18A kernel, Phase 18B account, Phase 18C runtime/adapter and
Phase 18D session. Importing either package performs no storage or external I/O.
No execution transport, database service, background task or dependency is added.

## Usage and lifecycle

Import from `quantlab.persistence`:

```python
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy

storage_policy = StoragePolicy(checkpoint_interval=1000, retained_checkpoints=2)
with SQLitePaperStore(local_path) as store:
    owner = DurablePaperSession(store, replay_config,
        storage_policy=storage_policy, strategy=approved_strategy,
        policy=eligibility_policy, eligibility=eligibility_decision,
        evidence=retained_evidence_store)
    owner.process(recorded_start_command)
    owner.process(recorded_delivery)
    owner.checkpoint()  # Optional accelerator after a committed input.

# A newly opened connection and NEW financial/session owner are required.
with SQLitePaperStore(local_path) as store:
    recovered = DurablePaperSession.recover(store, replay_config,
        storage_policy=storage_policy, strategy=approved_strategy,
        policy=eligibility_policy, eligibility=eligibility_decision,
        evidence=retained_evidence_store)
    recovered_snapshot = recovered.snapshot
```

One file contains one session. A store permits one owner and retains one connection;
a second writer is refused rather than coordinated. Call `close()` or use a context
manager. A closed store refuses further processing, including retries. Construction
refuses an existing session: recovery is explicit. The parent directory must exist;
memory databases, SQLite URIs and UNC/network paths are rejected. Local mounted
filesystems must satisfy SQLite locking/sync requirements; remote mappings cannot
be detected solely from a pathname. Do not concurrently synchronize an active
store through cloud tools or copy a database without its active rollback journal.
Backup/migration, live network feeds and multi-process use are outside this phase.

Recovery preserves CREATED, PAUSED, STOPPED and FAILED exactly. A recovered ACTIVE
session retains its durable snapshot but sets `operator_required=True`. It accepts
exact identity retries and explicit pause/stop/fail commands. Historical retry
results never alter the current gate, including old PAUSED commands or inactive feed
events. Only a newly committed permitted operator lifecycle transition can clear it.
Before any new market
input, the operator must record normal increasing-sequence `pause` and `resume`
commands. This records the acknowledgement without inventing a clock, price, opening
or financial event. A stopped session never activates on restart. Pause retains
pending funds; stop/fail cancel and release without liquidation. Interrupted/exhausted
feed state and stale policy are retained, and missed openings cannot be filled
retrospectively. The Phase 18D terminal-command capacity exception still permits safe
cancellation after normal input retention is exhausted.

## SQLite durability and schema

Settings are explicit and checked: `journal_mode=DELETE`, `synchronous=EXTRA (3)`,
`locking_mode=EXCLUSIVE`, `foreign_keys=ON`, `trusted_schema=OFF`, zero lock wait,
and `isolation_level=None`. Each write owns `BEGIN IMMEDIATE`, SQL, commit and
rollback. Initialization uses an exclusive transaction. Application ID `0x514C5045`
and `user_version=1` identify this format. Unknown versions, unexpected schema
objects and modified table definitions fail closed; no automatic migration exists.

Rollback journaling suits one exclusive writer and avoids WAL deployment/lifecycle
requirements. EXTRA adds directory synchronization after rollback-journal deletion
beyond FULL, subject to SQLite's VFS/platform support. See the primary
[SQLite synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous),
[atomic commit assumptions](https://www.sqlite.org/atomiccommit.html), and
[WAL operating constraints](https://www.sqlite.org/wal.html).
The configured transaction is atomic according to SQLite's guarantees, not an
absolute promise against defective hardware, lost files, broken locking, filesystem
failure or a device that falsely acknowledges sync. A process-exit test exercises
SQLite recovery; it is not a hardware power-loss certification.

| Table | Contents and role |
| --- | --- |
| metadata | Singleton immutable manifest/binding plus transactional count/head projection |
| operations | Append-only supported journal: ordinal primary key; unique input ID, logical sequence, transaction ID and digest; canonical versioned entry |
| checkpoints | Bounded disposable accelerators keyed by a foreign-key journal ordinal; canonical state and unique digest |

The manifest binds exact replay/account configuration, storage policy, engine format,
full strategy (including approval), eligibility policy/decision, retained evidence
identities and admitted runtime record. Recovery requires the same trusted artifacts
and rechecks approval and independent research evidence using Phase 18C. It never
fetches a replacement dataset or reruns research/agent inference. Full research
artifacts remain in the caller's retained evidence store; durable binding references
their content identities. Observations themselves are retained, so recovery does not
depend on a mutable dataset fetch.

## Journal and generated records

Each JournalEntry contains schema version, session/config identity, contiguous commit
ordinal, stable external input ID/type, logical sequence/effective UTC time,
deterministic transaction ID, canonical typed payload, generated session output/effects,
output digest, previous-entry digest and current content digest. Occurrence,
availability, delivery time, provenance and dataset version references remain in the
original ReplayEvent; command reason references and recorded clock advances remain
in SessionCommand or feed controls/heartbeats.

Commands and events share one collision domain. Transaction IDs hash the bound
manifest and stable input ID. Equal prices are not event identity: equal quotes with
different source event IDs are distinct deliveries. Exact retries return retained
results without SQL writes, clock advance, fees, fills, holds or releases; conflicting
or invalid reused IDs raise PaperIdentityConflict without mutation.

SessionRecord retains actual transitions/reasons, decisions/features/causation,
order/risk/fill/cancellation events, financial events, account/position/reservation
snapshot, feed state, original input and previous audit record. Bounded Effects
retain the existing single-order kernel state, generated entry intent and actual
canonical financial inputs required to verify settlement provenance. Opening
acknowledgements are rebuilt from original opening inputs and committed engine
results. These are generated outcomes and replay evidence, not another accounting
engine. Metadata count/head are a projection, not an independent financial ledger.

Serialization reuses sorted finite canonical JSON and exact Decimal strings. Each
encoded record is bounded to 4 MiB, and Decimal expansion to 4096 digits. Typed JSON
decoding rejects unknown fields/types/versions, duplicate keys, floating JSON numbers,
nonfinite constants, malformed/truncated values, invalid UTC timing and noncanonical
spelling. The existing canonical timedelta object is adapted explicitly for typed
configuration decoding. There is no pickle or disk-selected Python class loading.

## Durable transaction and publication

1. Phase 18D validates input and prepares unexposed candidate runtime, account,
   order, feed and lifecycle owners using the established engines.
2. It prepares SessionRecord and stages reversible indexes under the old public
   root. Runtime history appends are hidden by committed counts.
3. The internal `_commit_candidate` seam validates canonical input/output/effects,
   indexes, identities and any scheduled checkpoint before SQL.
4. One database transaction inserts the complete operation, optional checkpoint and
   new count/head. Unique constraints and the expected durable head are checked.
5. Only after commit does `_publish_candidate` expose the complete candidate root.
   No additional financial calculation is required after commit.

The store holds an explicit shared transaction state: IDLE, PREPARING, COMMITTING
and COMMITTED. COMMITTING is set **before** invoking SQLite COMMIT. COMMITTED
remains set across store return, owner handoff and the complete publication path.
The session checks this shared state for authoritative reads and processing, even
if an interruption prevents its exception handler from setting the poison flag.
Only completed memory publication permits the owner to acknowledge the commit and
return the store to IDLE. Manual checkpoint transactions use the same boundary.

Failures before the commit boundary permit retry only after a confirmed rollback.
A failed/interrupted rollback, or any exception after COMMITTING was set, requires
recovery; rollback succeeding after a commit attempt does not prove noncommit.
BaseException handling includes KeyboardInterrupt. Successful commits followed by
return/handoff/publication failures remain fail-closed. Snapshot/record reads and
new processing are blocked while durable state may be ahead of memory. A new
connection/owner must verify/recover the journal; the old root cannot override the
durable outcome. Previously obtained snapshots remain historical immutable views.
An interruption after the complete root has been published and acknowledged may
lose the caller's response; an exact retry returns that committed result.

The base PaperSession seam is a no-op: standalone Phases 18A–18D retain public
behavior and no-I/O architecture. Pointer publication selects coherent in-memory
state; SQLite commit and deterministic recovery provide cross-process durability.
There is no distributed exactly-once claim.

## Checkpoints and recovery

StoragePolicy bounds interval to 0–100000 and retained checkpoints to 1–8. Default is
1000 commits and two checkpoints; zero disables scheduling. Checkpoints may also be
requested explicitly. An identical checkpoint at an existing head returns its retained
equivalent without changing history. Only accelerator rows are pruned; authoritative
journal inputs/outcomes are never pruned or repaired.

A compact Checkpoint binds schema, manifest/session, ordinal, entry/head/audit identity,
lifecycle/clock/feed, account/positions/reservations, kernel, intent and runtime/financial
counts and clocks. Growing decisions, bar history, acknowledgements and idempotency
indexes reference the retained validated prefix. Creation does not serialize complete
session/runtime history. The one-order kernel and financial state remain bounded by
the supported single-entry capability.

Recovery validates schema/metadata binding, SQLite consistency, every record/index,
contiguous ordinals, increasing logical sequence/time, unique identities, complete
digest chains, causal input/output bindings and count/head tail anchor. It validates
every retained checkpoint, even when full replay is requested: an invalid accelerator
is never silently ignored. Without a checkpoint, or with `use_checkpoint=False`, all
committed inputs are replayed and every outcome/effect compared. Inconsistency refuses
activation.

With a checkpoint, recovery rebuilds private prefix runtime/account/order owners
through the existing deterministic engines. It reevaluates prefix bar decisions
and compares event reasons, lifecycle/feed state, transitions, command references,
decisions, order outputs, financial events, account snapshots and complete Effects.
The strategy-order adapter rechecks close/open timing, adjacency and causality;
the account atomically reconstructs opening acknowledgements with settlement.
Acknowledgements are not inferred from a stored reason string. Cancellation and
reservation release are generated and compared through the existing account owner.
Missing or contradictory evidence refuses recovery rather than repairing history.

Verified prefix SessionRecords are reused for history/index reconstruction, avoiding
prefix session-record generation and per-operation candidate-owner copies. Suffix
inputs use ordinary session processing with complete outcome/effect comparison.
Both paths evaluate the same supported operational semantics. Checkpoints no longer
skip prefix strategy evaluation: this stronger validation is intentional. Every
record is still verified, and restart remains O(n). Checkpoint timing counters
identify prefix verified inputs separately from ordinary suffix replay inputs.

Stable PersistenceError.reason diagnostics include journal_chain, journal_digest,
journal_tail, invalid_record, configuration_mismatch, checkpoint_state,
financial_provenance, checkpoint_operation_mismatch, commit_outcome_unknown,
rollback_outcome_unknown, committed_unpublished and replay_mismatch. A failed recovery
returns no owner and retains its reason in `store.last_recovery_failure`. It does not
fabricate records or edit financial history to record its own failure.

## Integrity threat model and future integration

The chain detects supported accidental modification, omission/reordering, index
conflict, broken links and tail truncation against retained metadata. Typed validation
and replay add semantic checks. Hashes are not authentication: an attacker able to
rewrite all local records, checkpoints and anchors can recompute hashes. Complete-file
deletion/replacement, trusted-artifact loss, media failure and hostile local code are
outside this guarantee.

Local exactly-once state application means that a stable input appears once in the
journal and its financial effect appears once in each reconstructed owner. Recovery
computes in a fresh owner; it does not reapply fills to an already settled account.
This says nothing about broker side effects or distributed processing.

Phase 18F preserves candidate preparation and persists complete advanced outcomes
in the same boundary through explicit versioned contracts. Limit/stop/partial fills
and executable position-linked exits/OCO are supported within the bounds in
[paper-advanced-orders.md](paper-advanced-orders.md). Broader state still requires
compatible engine/schema migration and a bounded checkpoint strategy. Allocation,
portfolios, brokerage, cloud persistence, concurrent writers, new MCP execution tools,
dashboards and runtime LLM calls remain unsupported.

The corrected 5,000-event repeated recovery medians are 23.02/14.40 seconds for
quotes and 55.86/46.34 seconds for entry/bars (full/checkpoint). Checkpoints now
verify prefix engine semantics, so they do not skip strategy evaluation. Integrity
verification remains the largest recovery phase. These shared-host measurements are
observations, not latency guarantees. The Phase 18E final suite passed 3,240 tests
without skips or xfails. Current Phase 18F/audit evidence is recorded in
[phase18f-report.md](phase18f-report.md). See [the Phase 18E verification report](phase18e-report.md) for
exact regression counts, failure-injection evidence, raw measurements, before/after
comparisons and the explicit performance follow-up.

## Phase 18F advanced strategy-entry journal extension

AdvancedEntryReplayConfig schema 3 uses manifest engine paper-18f-entries-v3.
Physical SQLite and outer journal/checkpoint versions stay v1; old payloads retain
their canonical identities and exact operational behavior. SessionRecord adds
optional advanced intent and entry progress, omitted for legacy sessions. Advanced
Effects/checkpoints retain a bounded KernelProgress entry head instead of copying
its growing history. The recorded opening and later observations regenerate the
account-owned gate, triggers, fills, cancellations, fees and residual reservation.
Both recovery modes verify complete operational prefixes, including decisions;
no AI inference or snapshot-only financial authority is accepted. Commit uncertainty
and recovered-ACTIVE pause/resume gates are unchanged. See
[paper-advanced-orders.md](paper-advanced-orders.md) and section P of
[phase18f-report.md](phase18f-report.md) for contracts and verification.
