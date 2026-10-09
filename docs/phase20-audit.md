# Phase 20 focused correctness audit

## A. Verdict

**PASS for the documented bounded Phase 20 scope, after five focused corrections.**
No unresolved blocking finding remains in the reviewed paths. This review concerns
the uncommitted Phase 20 increment over `8d622dc`; it adds no future-phase feature
and changes no shared paper, portfolio, MCP, accounting or execution contract.

## B. Severity-ranked findings

1. **High — failed rollback left the journal handle usable.** A precommit failure
   followed by a rollback exception could expose prepared, uncommitted trade rows
   through public queries. `_rollback` now poisons the handle and raises
   `JournalRecoveryRequired`; reads/writes require close and verified reopen.
   Both transaction failure and read-snapshot cleanup use this boundary.
2. **Medium — research output could contradict its declared strategy.** A
   completed operation with valid typed/canonical result JSON and recomputed
   digests could declare one strategy ID/version/digest while retaining another
   result. Holdout/walk-forward evidence could also mix identities across segments
   because history extracted only the first binding. Every supported report's
   execution/performance segments and robustness candidate specifications now
   agree, and operation result bindings match the original audit metadata.
3. **Medium — position identity/context could diverge from actual fills.**
   Aggregate account economics were checked, but a consistently rehashed position
   could replace its strategy, entry transaction/time or position-level fees/P&L.
   Those fields now reconcile to the original entry fill or preceding position
   and exact authoritative financial deltas. Basis/direction are also preserved
   on nonsettlement records. No independent ledger is introduced.
4. **Medium — a rejected replay link could publish a replacement suffix.** A
   replay envelope with an incompatible admission identity invoked the normal
   linking operation before checking its expected result. It raised an error
   after committing a different valid link, so the destination was not a prefix
   of the supplied export. Replay now checks the exact expected link before
   invoking any write; rejection leaves only the valid supplied prefix.
5. **Medium — selective paged queries traversed unrelated history.** Order-filtered
   trade reads and strategy-filtered research reads used correlated `EXISTS`
   predicates over the complete chronological outer index. Trade filters on
   strategy version/digest or instrument also traversed that outer index. An absent
   filter could scan every historical record despite a one-record limit. Matching-ID
   subqueries now select source records by primary key or the session index. Existing ordering,
   pagination, filtering, deduplication, source validation and database schema
   remain unchanged. This corrects a demonstrated algorithmic defect, not a
   benchmark-only optimization.

All five findings were reproduced with failing regression tests before correction.
No normal committed fixture showed wrong aggregate financial accounting, duplicated
OCO settlement, fabricated missing evidence or editable execution history.

## C. Corrections and test evidence

Audit changes are limited to `src/quantlab/journal/store.py`, `models.py`,
`research.py`, new `tests/journal/test_audit.py`, this report and the link in the
implementation report. Existing Phase 20 work remains uncommitted.

- Failing-first command: `.venv/Scripts/python.exe -m pytest tests/journal/test_audit.py -q --tb=short`.
  **13 failed in 3.38 s**, confirming the five defect categories.
- Follow-up filter check: the same audit command with `-k selective_history`.
  **3 failed, 2 passed, 11 deselected in 1.57 s**, reproducing the additional
  version/digest/instrument variants before their SQL correction.
- Affected-suite command: `.venv/Scripts/python.exe -m pytest tests/journal -q --tb=short`.
  **61 passed in 15.92 s**, including all original 45 cases and 16 new regressions.
  No failures, skips or xfails.
- New cases cover result ID/version/digest mismatch, mixed holdout evidence,
  position strategy/entry transaction/time/fees/P&L mismatch, replay rejection
  without a committed replacement, rollback-failure poisoning, and five selective
  SQL query plans. Existing tests cover all nine research codecs, partial long/
  short entries/exits, OCO, exact retries, recovery, frozen legacy journals,
  query pagination and immutable snapshots.
- The previous 1,490 targeted / 3,964 full-suite passes and published benchmark
  remain initial implementation evidence. They were not rerun or relabeled as
  post-audit results. Journal-local validation and SQL changes do not materially
  change shared behavior, so no full-suite rerun was justified.

## D. Durability and provenance assessment

Successful writes still atomically commit the immutable envelope and all derived
rows in one SQLite transaction with FULL synchronization. Exact retries are
idempotent, conflicts publish nothing, commit uncertainty and now failed rollback
require verified reopen, and replay rejection preserves the valid committed
prefix. Reopening still verifies SQLite, the exact schema, the complete event
chain and every rebuilt projection row. Database schema and source wire versions
are unchanged; historical paper journals/checkpoints are not rewritten.

Source approval/admission, namespace/run identity, original digests/revisions and
human-note chains remain immutable. Missing evidence stays missing. Stricter
checks reject contradictory artifacts rather than repair them or invent source
data. A previously stored contradictory artifact will fail closed on verification.

There is no cross-store atomic transaction or newly granted trading authority.
Engine origin is not authenticated by content hashes, and whole-log replacement
or truncation to a valid earlier prefix still needs an external expected head or
backup to detect. These documented boundaries remain applicable.

## E. Performance assessment and limits

No benchmark was repeated. The recorded 70.45 ms ten-record file read and 6.30 s
verified restart at 1,004 retained records are **pre-audit measurements**.

Code inspection shows repeated strict decoding/revalidation of session, source,
predecessor and returned wrappers. Similar memory/file read medians suggest
materialization/validation contributes substantially; this is an inference, not
a profiler measurement. Integrity checks were retained; no validation shortcut or
synthetic-latency optimization was introduced.

Verified restart deliberately replays every retained artifact into a fresh
in-memory SQLite journal and compares all projections. It is O(history and
artifact size), with O(history) replica memory. Startup time and memory therefore
remain material scaling limits; the published 6.30 s should not be read as a
constant-time restart promise.

`EXPLAIN QUERY PLAN` confirmed retry lookup uses SQLite's MULTI-INDEX OR with
exact input and record-ID searches, not growing-prefix copying or scanning.
Current-head and log-head lookups remain indexed and bounded. The five corrected
selective queries no longer scan the unrelated outer history. They can still
materialize/sort matching identity subsets; filters on non-leading index columns
may inspect session/binding metadata, and explicit summary counts remain linear in
session events. This is not a claim that every filtered page costs only O(page
size). Large source artifacts retain their serialization/revalidation costs.

## F. Git and commit readiness

No staging, commit or push was performed. The complete uncommitted Phase 20 change
set is ready for review and commit within the documented scope after the affected
tests. Final Git status is `master` at
`8d622dce01f602a27e76870c4216bf63763af93f`, with nothing staged: three modified
tracked files (`README.md`, `docs/architecture.md`, `docs/roadmap.md`) and eleven
untracked Phase 20 files (benchmark, implementation/audit reports, five journal
modules and three journal test files). `git diff --check` passed; Git reported
only its existing LF-to-CRLF conversion notice for `docs/architecture.md`.
The initial implementation's declined status refresh is historical and is
superseded by this successful audit status check.
