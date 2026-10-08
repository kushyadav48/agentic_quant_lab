# Phase 18B: deterministic paper account accounting

Phase 18B adds a separate Python account owner and atomic in-memory coordination
for account-owned Phase 18A entry kernels. Standalone Phase 18A behavior is preserved. It is a bounded offline research account, not a cash-settled brokerage
account, stock custody account or a margin/borrow simulator.

## Supported policy

The versioned policy is `prefunded-linear-equity-pnl-v1`. Each account has one
fixed EQUITY instrument, an explicit denomination equal to its quote currency,
contract multiplier 1, positive initial capital and a producer-supplied UTC
initial timestamp. Instrument metadata must not declare a base currency.
Currency labels are descriptive codes, not a currency registry. Forex, crypto,
other multipliers, conversion and physical settlement are rejected.

Either a long or a short linear research position may be opened. Both directions
lock 100% of original execution notional from owned balance; fees require
additional funds. No borrowed buying power or proceeds from a short sale are
credited. This prefunding rule is a research reservation policy, not an invented
brokerage margin convention. Short losses remain unbounded mathematically.

One position may be active at a time, with one strategy owner. Scaling in,
netting, reversals and simultaneous strategy positions are rejected. A closed
position remains as a zero-quantity immutable record until another position
opens. Its lifetime gross realized P&L and fees are preserved in earlier
snapshots and retained inputs; account totals carry across subsequent positions.
No average-price rounding or proportional entry-fee allocation is needed.

## Contracts and equations

`AccountConfig`, `AccountSnapshot`, `AccountPosition`, `FundReservation` and
`AccountEvent` are strict frozen Pydantic contracts. All account monetary values
are finite Decimal values. The snapshot exposes starting capital, balance,
available funds, order reservations, position collateral, gross realized and
unrealized P&L, fees, equity, peak equity, ownership, versions and logical sequence.

For initial capital C, cumulative gross realized P&L R, explicit fees F,
gross liquidation unrealized P&L U, pending reservations O and active cost basis K:

- balance = C + R - F
- equity = balance + U
- available funds = balance - O - K, and must be nonnegative
- reserved funds = sum of active reservation amounts
- position collateral = K = entry execution price * remaining quantity
- net realized account P&L = R - F, including fees on currently open positions

Reservations and collateral are encumbrances, not cash debits. Balance changes
on explicit fees and realized gross P&L. Unrealized gains never increase buying
power, and marks never release collateral. Unrealized losses affect equity but
are not settled into balance. The account does not enforce maintenance margin or
continuously cap notional/equity after market moves. A mark may therefore record
negative equity; a settlement that would produce negative available funds fails
atomically. Insolvent short closure/credit/default/liquidation policy is outside
v1, rather than a fabricated execution or balance adjustment.

For a reduction of q at execution price x and original entry basis e:

- long gross realized P&L = (x - e) * q
- short gross realized P&L = (e - x) * q

Remaining quantity is old quantity minus q and remaining cost basis is e times
that quantity. Gross unrealized P&L uses the same equations with remaining
quantity and the executable liquidation mark: bid for long, ask for short.
Closing sets remaining quantity, cost basis, collateral and unrealized P&L to
zero. Quantity must respect the instrument's exact quantity increment.
A reduction cannot exceed holdings or change the strategy owner/direction.

Commission per unit times filled quantity plus the fixed fee is debited exactly
once. Observed spread and adverse slippage are already in execution prices;
their descriptive cost breakdown is never debited a second time. No future
exit fee is estimated in unrealized P&L.

Accounting uses an isolated precision-4096 Decimal context trapping Inexact.
It preserves exact finite monetary results, independent of ambient/DefaultContext
settings. Results outside exact arithmetic/canonical bounds fail atomically.
Shared Phase 18A pricing remains precision 34. The account additionally checks
its exact price adjustment and individual monetary costs, rejecting an 18A fill
whose economics require rounding under the stricter account policy. Historical
pricing, backtesting and risk contracts are unchanged.

## Ownership and pure transitions

`initialize_account(config)` returns the immutable initial snapshot.
`transition_account(snapshot, input)` is a pure economic transition returning a
new snapshot and content-bound event. Inputs are `ReserveFunds`, `ReleaseFunds`,
`ApplyFill` and `MarkAccount`. These are **trusted local Python contracts**:
they do not authenticate an execution, approve a strategy or grant order
permission. In particular, exit `ApplyFill` inputs must come from an independently
trusted execution producer when integrated later.

`PaperAccount(config)` owns the authoritative snapshot, immutable canonical
input records, event journal, retry index and lifetime reservation/execution
identities. `apply_trusted(input)` supplies atomic commit and idempotency to the
pure transition. `snapshot` and `events` are read-only views;
`get_input_record(input_id)` returns retained canonical input text with full fill,
quote, costs and causation provenance. No caller-supplied replacement snapshot,
balance, risk approval or serialized kernel snapshot can mutate the owner.

This is an in-process authority boundary, not authentication against malicious
Python code holding the object. The trusted application must retain account
handles; no LLM, MCP, graph, API, broker, network callback or dashboard receives
one. The core has no I/O, network, worker or wall-clock dependency. Use one
serialized caller per account; concurrent/distributed access is not supported.

## Phase 18A adapter and reservations

`create_order_kernel(session_id=..., strategy_id=..., risk=..., costs=...)`
constructs and retains a Phase 18A handle using owned flat equity and peak.
Risk and costs are trusted construction policies. Strategy approval and
validation eligibility remain the application's responsibility for Phase 18C.

The account adapter reads actual retained state from its owned kernel handle:

1. `reserve_order(kernel, event_id=..., sequence=..., timestamp=...)` requires
   ACCEPTED and computes an estimate from the latest quote, adverse slippage,
   complete quantity and explicit fees. It binds account, strategy, order,
   deterministic reservation ID and the kernel's acceptance event.
2. `kernel.process(input)` (or `account.process_order(kernel, input)`) prepares
   risk, matching, prices and proposed order records without publishing them.
   On a proposed entry fill, it prepares exact settlement from the owned reservation
   and rechecks actual fees/notional against every other hold. A successful call
   publishes the fill, its financial application and the consumed hold together.
   Explicit cancellation and pre-fill risk cancellation similarly release any
   existing hold in the same publication as the terminal order state.
3. `settle_order(...)` and `release_order(...)` retain their signatures as
   compatibility acknowledgments. They return the existing committed accounting
   event and never apply economics again. Their supplied sequences are checked
   strict acknowledgment metadata, not new financial sequence assignments.
   `settle_order` still requires the execution's exact effective timestamp.
   Automatic settlement/release uses the next account-local input sequence.

Accepted orders may exist before a hold is requested. An unreserved owned
entry cannot publish a fill; its first eligible execution quote cancels it with
`account_rejected`. Acceptance rejection never creates a hold or needs a release.
Reservation admission failure keeps the accepted, unreserved order unchanged.

Multiple accepted entry orders may reserve funds while the account is flat.
Their sum shares one account-level available-funds check; tested aggregate
reservation protection prevents double-spending. One position remains the
supported accounting capacity. A competing entry encountering an existing
position is cancelled without a fill and releases only its own hold.
This is not aggregate portfolio risk or an allocator.

A reservation is an estimate, not a guaranteed execution price. If actual
execution notional plus explicit fees and other holds exceed prefunded capacity,
the supported outcome is ACCEPTED -> CANCELLED with `account_unfunded`.
Other expected accounting validation failures use `account_rejected`.
The candidate FillRecord/FILLED transition is discarded before publication.
The original risk result is preserved; denial does not rerun risk or pricing.
The cancellation and hold release are one deterministic committed transaction:
balance, fees and position stay unchanged, the reservation disappears, and there
is no committed fill or fill input to acknowledge.

Invalid identities are explicit errors, not transformed into financial denials.
Unexpected preparation/staging/publication exceptions, or inability to validate
the required release (including exhausted financial retention capacity), leave
both components and all retry/history indexes unchanged. The order remains
ACCEPTED with its valid live hold; the failed input is not admitted, so it can be
retried after resolving the failure. There is no terminal order with an orphaned
reservation.

## Transaction boundary

Account-owned kernels are bound privately at construction. Their public
`process` always routes through the owner; standalone publication and separate
trusted applications targeting these owned orders are rejected. No public
commit of a prepared owned fill exists. Independent `PaperOrderKernel(config)`
instances remain order-only simulators and retain their original fills, identities,
risk gates and timing; they do not claim to represent an account execution.

Kernel preparation performs all original validation, chronology, risk and pricing
without mutating order state or its retry index. Account preparation performs
all original financial checks, immutable record construction and canonical
serialization. Account snapshots and all owned order snapshots/inputs/retry
results share one immutable publication root. Publishing that root is the sole
point where either component becomes visible as committed.

Financial journals and indexes are staged in place to avoid copying full
histories. Public history reads are bounded by the published event sequence;
canonical-input reads similarly refuse unpublished records. Staging errors undo
the new entries and restore the original root. No validation, producer call,
callback or I/O follows the publication swap. Tests inject failures after pricing,
account transition, serialization, cache staging, release validation and the
publication assignment itself; no partial state or retry identity survives.

This guarantee covers one serialized synchronous trusted caller, including
reentrant mutation rejection. It is not a thread-safe/distributed transaction,
durable commit or process-crash recovery guarantee. Hashes are content integrity,
not authentication against malicious Python code with private-object access.

Repeated identical owned order inputs return the original paper result without
reapplying settlement, cancellation, fees or release. Changed content under
the same identity fails without publishing either component. Financial events
bind the exact committed FillRecord or cancellation transition as causation and
retain account, strategy and order transaction attribution. Automatic financial
input IDs use versioned SHA-256 namespaces over account and execution/terminal
event identity. Equal-time observations remain ordered by their kernel sequence;
account financial sequences are a separate monotonically increasing namespace.

Phase 18A remains entry-only. Pure accounting reductions and closes are fully
tested but **not reachable as executable orders through PaperOrderKernel**.
The adapter never manufactures exit fills or extends its lifecycle.

## Logical ordering, atomicity and history

Account inputs require increasing account-local sequence and nondecreasing
effective timestamps. Market references preserve observation, availability,
delivery and processing times. Quotes must be available by the financial input;
fills bind exact processing time, observed execution side and recorded cost
policy. Position valuations require increasing observation-stream sequence and
nondecreasing observation, availability, delivery and processing times. Distinct
observations at equal timestamps are ordered by sequence. Kernel sequences and
account input sequences are separate namespaces. Delayed entry quotes retain
their original observation time and the execution's effective time.

Identical input identities return the original AccountEvent even after later
updates, without rolling back current state. Changed content under an existing
identity raises PaperIdentityConflict. Lifetime reservation IDs/orders and
settled order/causation identities cannot be rebound under new financial IDs.
Pure accounting input failures leave its snapshot, journal and retry indexes
unchanged. Coordinated expected non-fill outcomes atomically commit cancellation
and release as described above; other transaction failures leave both components
and their retained inputs, records and indexes unchanged. Pure transitions
alone do not retain retry history; use the owner for exactly-once application.

Each output hashes canonical content and includes account/strategy attribution,
transaction and causation references, input ID/digest, policy, input/output
sequence, before/after version, prior event hash and reconciled monetary totals.
Full immutable financial input records are retained separately. Prior snapshots
and events cannot change when future marks/fills arrive. Hashes establish content
integrity and deterministic provenance, not cryptographic producer authentication.

Admission is bounded to 32 active reservations, 32 lifetime owned kernels and
100,000 financial inputs per account. Capacity fails rather than evicting retry
identities. Existing canonical limits apply: 4,096-digit Decimal expansion and
4 MiB per encoding. Updates inspect only the current position and bounded
reservation set, append one event and update indexes in place. They never copy
or serialize full historical journals; history is materialized only when read.

## Future integration and excluded scope

Phase 18C must add exact strategy version/digest approval and validation eligibility,
current-state risk coordination, runtime timing and execution admission. Phase 18D
may replay recorded inputs into fresh owners; Phase 18E must specify durable
storage, recovery and authentication. Neither is implemented here. Transaction,
causation, strategy/account attribution and versioned policy references provide
later portfolio, allocator, journal and visual-debugger integration points.

Unsupported: live/real-money execution, physical securities/cash settlement,
FX conversion, interest/borrow/dividends/corporate actions, margin/leverage,
multi-position allocation, scaling in/netting/reversals, portfolio rebalancing,
limit/stop/partial-fill matching, executable exits, persistence/recovery,
MCP operations, FastAPI and UI. Accounting reductions are not partial-fill
matching policies.


## Initial Phase 18B verification

Verification before consistency hardening on 2026-10-08 (superseded by the final results below):

- Embedded Windows Python 3.11.9:
  `python -m pytest tests/paper/test_accounts.py -q`: **138 passed in 1.05s**.
- Same runtime:
  `python -m pytest tests/paper tests/backtesting tests/risk tests/features tests/validation tests/analytics -q`:
  **1,017 passed in 7.89s**, including 115 unchanged Phase 18A paper tests.
- Existing WSL Python 3.11.17 environment:
  `python -m pytest -q`: **2,726 passed in 205.22s**, covering all repository suites
  including orchestration and MCP. Pydantic 2.13.5, MCP 2.3.0 and LangGraph 1.2.12.
- Strict account/input JSON roundtrips, explicit matching EUR denomination and
  aggregate reservation settlement/retry after a failed price gap also passed
  focused manual assertions. `git diff --check` passed.

The Windows full-suite attempt stopped during collection because Application
Control blocked the existing rpds DLL. The complete WSL run passed without
changing dependencies or weakening tests.

A local Windows Python check applied 1,000 marks in 1.280s (1.280 ms/update),
retaining 1,002 financial events and a 1,749-byte current snapshot. This is an
environment-specific observation, not a latency guarantee. A structural test
also verifies in-place journal/index updates, no historical event serialization,
and bounded current-snapshot size across 128 successive marks. Timing has no
role in financial decisions.


## Final accounting consistency hardening verification

The original defect was the separate publication in `PaperOrderKernel.process`
followed by financial validation in `PaperAccount.settle_order`. The account
could reject the actual price-gap settlement after the kernel had committed its
FillRecord/FILLED transition. That execution model is no longer supported for
account-owned kernels.

The correction separates kernel preparation from standalone publication and
makes account-owned processing publish one shared immutable account/order root.
Expected account rejection atomically publishes cancellation and reservation
release instead of a fill; unexpected errors publish nothing. Terminal adapter
calls are acknowledgments of the automatic accounting application.

Final results on 2026-10-08:

| Command | Runtime | Result |
| --- | --- | --- |
| `python -m pytest tests/paper --ignore=tests/paper/test_accounts.py --ignore=tests/paper/test_account_transactions.py -q` | Windows Python 3.11.9 | 115 passed in 1.25s |
| `python -m pytest tests/paper/test_accounts.py tests/paper/test_account_transactions.py -q` | Windows Python 3.11.9 | 183 passed in 1.57s |
| `python -m pytest tests/paper -q` | Windows Python 3.11.9 | 298 passed in 2.84s |
| `python -m pytest tests/backtesting tests/risk -q` | Windows Python 3.11.9 | 468 passed in 2.32s |
| `python -m pytest tests/orchestration tests/mcp -q` | WSL Python 3.11.17 | 753 passed in 170.61s |
| `python -m pytest -q` | WSL Python 3.11.17 | 2,771 passed in 197.14s |
| `git diff --check` | Git | Passed |

The 183 accounting tests include the existing 138 and 45 new transaction tests.
New coverage includes long/short unaffordable gaps, proposed-price settlement
validation, no phantom fills, cancellation/release consistency, exact retries,
identity conflicts, successful/failed deterministic replay, unchanged standalone
semantics, competing reservations/positions, equal-time ordering and reentrant
mutation denial. Failure injection covers transition calculation, financial event
serialization, denial preparation, release validation, index staging, registration
and the publication assignment. Injected transaction exceptions preserve both
snapshots and retained input/history/index state. Expected non-fill outcomes
publish only cancellation/release; successful retries apply once.

Only obsolete Phase 18B expectations for the split-commit gap and caller-assigned
post-fill settlement sequence were revised. Their replacements assert atomic
cancellation/release and automatic account-local sequencing. Existing standalone
Phase 18A, backtesting, risk, orchestration and MCP tests were not weakened.

Phase 18B is ready to commit within its documented bounded, serialized, in-memory
scope. Concurrent/distributed transactions, durable crash recovery, executable
exits, full strategy runtime admission, portfolio allocation, FX and margin remain
unsupported. No commit or push was performed; HEAD remains master at 67117c7.
