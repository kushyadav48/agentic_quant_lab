# Phase 20 — Trade journal and research history

Implemented against clean `master` at
`8d622dce01f602a27e76870c4216bf63763af93f`. The authoritative roadmap scope is
linked strategies, approvals, runs, simulated fills, risk decisions and human
notes, including backfill without rewriting financial history.

## Implementation and files

`src/quantlab/journal/` adds frozen contracts, retained research-result validation,
fixed boundary errors and `SQLiteJournal`. `tests/journal/` adds deterministic
behavioral/financial/recovery/compatibility tests. `benchmarks/phase20_journal.py`
measures actual imports and paged reads. README, architecture and roadmap record
the scope; this report supplies integration and verification evidence. No existing
engine or dependency is changed. No commit or push is performed.

Changed files: `README.md`, `docs/architecture.md`, `docs/roadmap.md`,
`docs/phase20-report.md`, `src/quantlab/journal/__init__.py`, `errors.py`,
`models.py`, `research.py`, `store.py`, `tests/journal/__init__.py`,
`tests/journal/test_journal.py`, and `benchmarks/phase20_journal.py`.

## Data model and source ownership

| Contract | Source and responsibility |
| --- | --- |
| `JournalSession` | Original replay config, approved specification and admission; exact session/account/instrument/strategy version/digest attribution |
| `TradeHistoryRecord` | Unmodified `SessionRecord`, registered session and realized/fee deltas derived from consecutive authoritative accounts |
| `ResearchHistoryRecord` | Origin namespace and retained evidence or operation snapshot; source-bound run identity, revision, time and status |
| `ResearchLink` | Explicit research/session/admission association; no eligibility or execution authority |
| `NoteRevision` | Human author/time/text, stable note target and append-only revision chain |
| `JournalEvent` | Schema-v1 ordinal, preceding identity and content-addressed import/note envelope |
| `HistoryQuery` / `HistoryCursor` | Supported identity/time filters and bounded query-scoped keyset pagination |
| `SessionSummary` | Latest paper account, record/fill counts, net realized P&L, position lifecycle and closed outcome |

Trade history represents **committed session deltas**, including decisions,
pending/rejected/cancelled orders and control records. Each delta is not necessarily
a completed trade. It preserves actual order/fill identity, price, quantity, costs,
risk, times, reservations and position context. Advanced/OCO heads, group events
and peer withdrawals remain accessible through the original source. No missing
price is reconstructed and there is no independently mutable financial ledger.

The journal reuses Phase 18 canonical identity serialization and the existing
strict persistence decoder: exact Decimal strings, canonical durations, bounded
encoding, duplicate-key rejection and strict JSON. Supported source schemas retain
their original v1/v2/v3 contracts. New journal/database schema is v1; incompatible
versions or modified table/index schemas fail closed. Historical paper journal
and checkpoint payloads are unchanged.

## Financial and causal invariants

- Registration requires exact formal approval and admitted configuration. An
  account cannot be assigned to another registered session.
- Imports bind original config/input digests, predecessor, session chronology,
  compatible market source/instrument, and decision admission/strategy/account.
- Financial events extend the original account event/version chain and reconcile
  to the retained account head. Nonsettlements cannot change realized P&L/fees.
  Every fill has exactly one settlement with original order, cause, time and fees.
- Realized deltas and resulting position quantity/basis reconcile with actual
  entry/exit fills and prior basis. OCO withdrawals bind the exact retained peer
  fill. These are consistency checks, not execution or a replacement ledger.
- Session, kernel and account sequences have different namespaces; numbers are
  preserved without treating them as a single clock.
- Net realized P&L is gross realized P&L minus fees, with immediate entry-fee
  recognition. Unrealized gains are never classified as closed profit. Closed
  outcomes require executed quantity with none remaining; cancelled/unfilled
  orders are `no_execution` and incomplete positions remain `open`.
- Exact retries append/settle nothing. Import/query cannot submit orders, change
  risk or portfolio ownership, resume recovery, approve research or grant trading
  authority. Existing Phase 18/19 financial semantics stay unchanged.

## Research provenance

Run identities hash a stable caller origin namespace, source kind and original
source identity. Namespaces distinguish independent application/server runs;
callers preserve them across retries/recovery. Operation revisions are existing
audit lengths; evidence revisions are 1. Terminal-only backfill invents no earlier
snapshots. Supplied historical prefixes can be imported later only if their audits
agree. Conflicting revisions/prefixes fail.

All nine existing tracked kinds are supported: backtest, holdout, walk-forward,
parameter robustness, ML dataset, training, OOS prediction, prediction features,
and performance. Original request/key digests, strategy/workflow bindings,
transitions, failure classes and immutable result JSON/digest remain intact.
Completed results are strictly checked against existing typed adapter contracts
and their canonical codec. Evidence retains definition-versioned reports,
request/result digests, dataset versions, verification status/time/author and
provenance reference. Existing reports retain configuration, costs, risk and
evaluation/robustness results where present.

`research_for_session` resolves admission references by both evidence identity and
result digest. Missing artifacts remain absent; unresolved references remain
visible on the admission. Explicit links require a prior completed/verified result
with exact strategy ID/version/digest and the existing admission identity. They
do not assert that an operation supplied eligibility evidence. Eligibility
policy/decision references and advanced execution approvals stay in original
admission/configuration records. Import cannot approve a strategy.

Operation snapshots lack raw requests/parameters; their request digests are
preserved instead of inventing configuration. Artifacts never retained cannot be
recovered. Stateless MCP results, LLM interpretation/review snapshots and new
research-tracking workflows are deferred. No LLM inference or quant service runs
during import, query, verification or replay.

## Storage, integrity and recovery

Local SQLite uses explicit `BEGIN IMMEDIATE`, foreign keys and `synchronous=FULL`.
Each success atomically appends an immutable artifact/note and its index rows.
Tables store canonical artifacts and lookup metadata; there are no separately
mutable financial amount columns. Handles are single-threaded; multi-query reports
and audits share a SQLite read snapshot. SQLite serializes writes.

Reopening checks SQLite integrity and schema/index definitions, deterministically
replays the ordered content-addressed log into a fresh in-memory journal, and
compares every derived row. This detects missing/reordered/duplicate, malformed,
incompatible, contradictory or altered retained records/projections. Hashes are
integrity checks, not signatures/authentication: they cannot prove engine origin.
Replacement or truncation to a valid earlier whole-log prefix requires an external
expected head/backup to detect. Corrupt stores are not silently repaired.

Before-commit failures roll back all rows and permit retry. Commit/post-commit
interruptions poison the handle with `JournalRecoveryRequired`: close, reopen to
verify the durable outcome, then retry the same identity. SQLite resolves commit
state; no mutable financial cache publishes ahead of durable data.

`events()` is explicit full export/backup. `SQLiteJournal.replay(path, events)`
requires an empty destination and verifies each ordinal/predecessor/output. Each
event commits separately; a failure can leave a valid committed prefix, not an
atomic bulk import. Recovery from valid retained exports uses a fresh destination
for inspection before replacing a damaged file.

There is **no cross-store transaction** with paper persistence, portfolio or
process-local research operations. Applications obtain committed artifacts, then
import them. Interrupted imports can resend the same artifact or backfill original
ordered paper records. Journal failure cannot undo a committed fill; journal
success cannot authorize another. Paper unknown-commit recovery and ACTIVE-session
operator pause/resume requirements remain unchanged.

## Queries and human notes

Trade queries filter session/account/instrument/order and exact strategy
ID/version/digest. Research queries filter run and exact strategy binding.
Unsupported combinations are explicit errors. Both provide half-open UTC
`[start,end)` ranges, ascending `(timestamp, owner identity, sequence/revision,
record identity)` ordering and 1–200 records/page. Cursors bind kind/filters,
allowing page-size changes but refusing other-query reuse. Keyset pagination
avoids offsets/history copying. It reads current data, not pinned history:
backfill before a cursor is visible in a fresh query. Unknown filters return empty
pages; missing required operation targets/sessions fail explicitly.

Summaries use the latest authoritative account and its own denomination. They do
not aggregate currencies, invent weighted prices or claim unrealized returns.
Record/fill counts use indexes but explicit count reporting costs O(session
records/events). No portfolio return series or ranking is added.

Notes require an existing target, contiguous revisions, stable target/predecessor
and nondecreasing author-supplied time. Text changes, including empty text to clear
a note, append immutable revisions. `notes(note_id)` explicitly retrieves revision
history; `annotations(kind,id,limit=200)` retrieves latest revisions by note ID.
Text is capped at 8,192 characters. Notes cannot alter facts or approval.

## Integration example

```python
from quantlab.journal import HistoryQuery, JournalSession, SQLiteJournal

# Trusted application with already admitted owner and retained approved spec.
with SQLiteJournal("journal.db") as journal:
    snapshot = paper_owner.snapshot
    session = JournalSession(config=paper_owner.config, strategy=approved_spec,
        admission=snapshot.runtime.admission)
    journal.register_session(session)
    # Explicit initial backfill; normal delivery sends only the new record.
    for source in snapshot.records:
        journal.ingest_trade(source)
    for ref in session.admission.evidence:
        evidence = evidence_store.get(ref.evidence_id)
        if evidence is not None:
            journal.ingest_research("retained-application", evidence)
    query = HistoryQuery(session_id=session.config.strategy.session_id, limit=20)
    page = journal.trade_history(query)
    if page.next_cursor is not None:
        next_page = journal.trade_history(query.model_copy(update={"after": page.next_cursor}))
```

## Verification and performance

Verification date: 2026-10-09. Native Windows Python 3.11.9, Pydantic 2.13.5,
pytest 9.1.1, MCP 2.3.0 and LangGraph 1.2.12, using the project `.venv`.
The system Python lacked MCP, so final verification uses the existing project
environment. No dependency installation was needed.

| Check | Exact result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/journal tests/paper tests/persistence tests/portfolio -q --tb=short` | 1,490 passed in 454.97 s (0:07:34) |
| `.venv/Scripts/python.exe -m pytest tests/journal -q --tb=short` after final execution attribution checks | 45 passed in 16.62 s |
| `.venv/Scripts/python.exe -m pytest -q --tb=short` (one full run after stable source implementation) | 3,964 passed in 607.68 s (0:10:07) |
| `git diff --check` | Exit 0; only Git's LF-to-CRLF normalization notice |

The full suite ran once after source changes stabilized. No failures, skips or
xfails occurred in the final successful targeted/full runs. Failing-first regressions demonstrate rejection
of rehashed inconsistent realized P&L, compatible historical research-prefix
backfill, incompatible result JSON with a valid digest, and execution events
rebound to an unrelated kernel owner.

Coverage includes long/short entry/exit partial fills, fees and hand-computed net
P&L, OCO peer attribution, incomplete/cancelled/rejected orders and risk decisions,
exact retries/conflicts, all nine research codecs and terminal failures,
ordering/filtering/pagination, immutable snapshots, note revision/restart behavior,
pre/post-commit failures, source/index/schema corruption, deterministic replay,
legacy v1/v2 checkpoint recovery, portfolio isolation and hostile Decimal context.

Ordinary writes inspect only a bounded artifact, registered session, indexed
predecessor/current revision and new deltas. Index maintenance is O(log retained
rows); serialization uses the existing 4 MiB limit, including the envelope. No
historical tuple is copied or reconstructed per event. Legacy artifacts with full
kernel snapshots retain their original per-artifact cost; modern bounded heads
stay bounded. Startup verification/export are explicit O(history) operations.
Verification's fresh in-memory replica uses O(history) storage/index memory and
compares database rows in chunks of 128.

The benchmark times journal operations separately from paper execution. It
compares 100/1,000 retained market records in memory and real-file SQLite FULL
durability, quarter medians, ten-record reads, source/database sizes and verified
restart time. Measurements describe this machine, not production latency promises.

Command: `.venv/Scripts/python.exe -m benchmarks.phase20_journal --counts 100 1000 --durable`.
Each workload includes four initial committed entry records in addition to the
measured observations. Reads return the latest ten records, with 20 repeated reads
per workload. Source execution is excluded from insertion timings.

| Storage | New records | Insert median ms | First/last quarter medians ms | Ten-record read median ms | Insert total s | Verified restart s | Database bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SQLite memory | 100 | 5.1574 | 5.8371 / 4.8429 | 74.1573 | 0.5455 | n/a | n/a |
| SQLite memory | 1,000 | 5.0471 | 4.8365 / 5.1376 | 76.5832 | 5.3424 | n/a | n/a |
| SQLite file FULL | 100 | 9.5425 | 9.5632 / 9.6957 | 80.8252 | 0.9740 | 0.6617 | 1,093,632 |
| SQLite file FULL | 1,000 | 9.7076 | 9.6481 / 9.7788 | 70.4512 | 10.3192 | 6.3013 | 9,367,552 |

Source records grew from 3,494 to 3,510/3,518 bytes with sequence digits, rather
than serializing prior history. Maximum insertion times were 8.5970/68.0108 ms
in memory and 16.1788/198.2049 ms on disk. Median insertion/read costs stayed
similar at the two history sizes; durable storage and explicit full verification
grew with retained history as expected. This measures the supported workload,
not a proof about arbitrary large source results or all deployment hardware.

## Limits and deferred work

Only already-admitted supported sessions and retained tracked research/evidence
artifacts are imported. No arbitrary standalone ledger/kernel import, external
broker, new round-trip allocation model or automatic capture hook is added.
Admission checks validate contracts; they do not rerun eligibility computation or
authenticate reviewers. Notes have no destructive delete or target-move operation.
Source encoding near 4 MiB may exceed the shared bound when wrapped and fails
before publication. Durable storage grows with retained history; no pruning,
checkpoint compaction, migration, distributed writer or authenticated API is added.
FastAPI (21), dashboard (22), live brokerage, advanced allocation and autonomous
execution authority remain deferred.

## Git and commit readiness

The last verified branch was `master` at the starting commit above. The intended
change set contains the twelve files listed in this report; no commit, push or
staging action was performed. Implementation, tests, benchmark and documentation
are ready for review. A final repository-inspection command including Git status
was declined, so the final working-tree status was not refreshed. The required
`git diff --check` was separately authorized and passed; it is rerun after the
final documentation result update. No claim of a newly verified clean tree or
exact final staged/untracked status is made.

This paragraph records the initial implementation snapshot. The subsequent
[focused correctness audit](phase20-audit.md) supersedes its status/readiness
assessment and records five minimal corrections with 58 affected tests passing.
The earlier full-suite and benchmark figures above remain pre-audit evidence;
they were not rerun solely to repeat passing results.
