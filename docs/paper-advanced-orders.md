# Phase 18F supported advanced paper-order stage

This stage adds explicit v2 contracts through the existing PaperOrderKernel,
PaperAccount, PaperSession and SQLitePaperStore. It is a bounded implementation,
not completion of every Phase 18F requirement. The retained-history copying
remediation is described in section N of the implementation report.

## Versions and authority

OrderSubmission and KernelConfig keep their v1 market/GTC schema, canonical wire,
identities and behavior. AdvancedOrderSubmission and AdvancedKernelConfig are
explicit opt-in v2 contracts. Kernel configurations require schema_version=2;
v1 dictionaries cannot silently select the advanced policy. Unsupported versions,
extra fields, nonpositive/nonfinite prices and quantities, floats, unsupported TIF,
IOC stops and inconsistent order parameters fail validation.

There remains one synchronous, serialized, locally owned paper authority. No new
MCP tool, broker integration, network call, LLM fill/risk authority, worker, cloud
service or dependency is introduced. Account currency must equal the EQUITY quote
currency, multiplier must be one, and base currency must be absent. No borrowing,
FX, multi-strategy allocation, netting, hedging or account import is supported.

## Lifecycle, matching and activation

The lifecycle is SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED, with repeated
partial transitions supported. Rejection terminates submission. Cancellation or
explicit IOC expiry terminates an accepted/partially filled order. Terminal orders
cannot reopen. Each successful acceptance records an OrderActivation with its own
identity, distinct from the command, risk, trigger and fill identities.

| Type | Deterministic observed-quote policy |
| --- | --- |
| MARKET | First subsequent eligible executable delivery; v1 semantics unchanged. |
| LIMIT | BUY requires ask <= limit; SELL requires bid >= limit. Adverse configured slippage is capped at the limit. |
| STOP_MARKET | BUY triggers at ask >= stop; SELL at bid <= stop. A later eligible delivery, never the trigger delivery, may fill. |
| STOP_LIMIT | Same stop activation, followed by limit matching on later deliveries. A price gap can leave the activated order resting. |

The quote side remains the reference price. Fill records retain configured cost
assumptions and the actual capped slippage adjustment; accounting receives the
corresponding effective cost policy. Observed spread is never charged again.
Execution does not infer prices or trigger order from OHLC. A subsequent sequence
at the same UTC timestamp is causal under this delivery policy. Older observations
than the command are retained but cannot fill. Stop fills also reference the
retained trigger and occur strictly after its delivery sequence.

V2 requires fresh data at activation and matching under its recorded maximum_age.
Stale observations do not consume an IOC opportunity or liquidity. Backward
observation/delivery chronology fails validation. The producer remains responsible
for genuine observations and classifications.

GTC rests until execution/cancellation or input capacity. IOC attempts execution
once on the first subsequent eligible observation, then expires any remainder,
including a nonmarketable limit. DAY, GTD, FOK and IOC stop orders are rejected.
There is no inferred session calendar or wall-clock expiry.

## Cancellation sequencing

V1 cancellation continues to acknowledge immediately. V2 CancellationRequest
records PendingCancellation and leaves the order executable. An explicit
CancellationAcknowledgement must refer to the pending request identity. If its
sequence wins, only the remaining order is cancelled. If a fill wins first, that
fill remains committed; a final-fill winner receives a terminal-order cancellation
outcome. Repeated identical commands replay their retained results. Distinct
requests while pending refer to the original pending request. Conflicting identity
content fails without publication. Session stop stages request and acknowledgement
with the session's existing non-liquidating terminal operation.

## Simulated liquidity and partial quantities

MarketQuote contains no size. With liquidity_per_observation=None, the v2 policy
retains the declared full-fill assumption. With an explicit positive, increment-
aligned budget, each canonical recorded quote has a simulated quantity allowance.
This is a user-selected offline liquidity model, not exchange volume or queue
position. Standalone sessions are isolated. Account-owned orders share the same
quote budget, including sequential exit children and restart recovery. A reused
quote under another delivery identity cannot replenish the budget; changing its
budget policy after consumption is rejected. Successful financial publication
records consumption; failed candidates consume nothing.

Order snapshots expose filled_quantity, remaining_quantity and cumulative_costs.
Individual fills record original submission quantity, executed quantity, cumulative
quantity, remaining quantity, quote provenance, activation/trigger references and
liquidity policy. Quantities never exceed the original order and must respect the
instrument increment. There is one active account-owned advanced order or one linked
two-child OCO group at a time.

## Costs, holds and accounting

Commission is per executed unit. fixed_fee_per_fill is charged once per committed
execution. An order-level fixed fee is not implemented. Entry reservations use the
known quote plus slippage, or the limit price when present. For simulated partial
entries, fixed fees reserve the conservative maximum number of increment-sized
executions, original_quantity / quantity_increment. Partial settlement consumes
executed collateral and explicit fees and retains the unused reservation.
Completion/cancellation releases the remainder; IOC partial settlement releases
its remainder in the same financial transition as the fill.

Each continuation must bind the original transaction, strategy, direction and
reservation. Cost basis is summed exactly and its weighted average must be exactly
representable under the existing bounded exact Decimal policy. A nonterminating
average is rejected through the existing account non-fill cancellation path; it
is never silently rounded. This deliberately narrows varying-price partial entry
support. Advanced partial-entry execution risk marks existing exposure on the current
eligible quote and combines that P&L with the committed balance; its peak includes
that known pre-fill equity. It does not use a future observation or the proposed
fill. The risk record retains this context; account valuation changes remain
explicit fill/mark accounting transitions. Future gaps are not guaranteed funded: current risk/funding checks can
cancel an accepted remainder while preserving all earlier fills and fees.

Reduce-only orders require the existing position's entry_transaction_id, owning
strategy, instrument, opposite execution side and a quantity no larger than the
owned position. A closing fill realizes long (exit - entry) * quantity or short
(entry - exit) * quantity, applies its closing fee once, marks remaining exposure
on the executable liquidation side and releases reduced basis collateral. A full
closure retains a zero-quantity audit position. Reduction orders do not reserve
entry collateral or run entry-only risk limits, but still obey quote freshness,
causality, increments, position ownership and prefunded financial invariants.
Unsupported attribution, reversal or exposure growth is rejected.

## Strategy and protective boundaries

Phase 18C admission, exact approval/version/digest, eligibility, causal raw features
and BAR_CLOSE -> explicitly locked NEXT_BAR_OPEN entry timing remain unchanged.
AdvancedReplayConfig permits trusted AdvancedSessionCommand exit operations only
after that approved entry has filled. Submit commands derive the closing side from
the owned position and bind the current position and session configuration. Ordinary
fresh quote envelopes then match the exit. Paused, stale, interrupted or exhausted
feeds cannot trigger/fill. Recovered ACTIVE sessions still require new pause/resume.

A protective child is either a reduce-only STOP_MARKET stop_loss or LIMIT take_profit.
It links its submission and kernel to the owned entry transaction. A single exit or
one explicitly linked OCO group may be active. Independent overlapping children,
groups and automatic attachment to unfinished entries are rejected. Sequential
single exits may protect residual exposure after a previous exit terminates.

### Two-child protective OCO (v3)

Trusted Python OCOCommand schema 3 adds submit_oco, request_cancel_oco and
ack_cancel_oco under AdvancedReplayConfig. PaperAccount.create_protective_oco and
process_oco support the same group outside a session. Creation requires exact
account, strategy, instrument and settled-position attribution, the entire current
position quantity, and prices strictly bracketing the fresh executable valuation
side. Group and child IDs are derived from canonical creation content. One group
is admitted per entry transaction; even a cancelled group cannot be recreated for
that position. Other active orders or reservations prevent admission. Neither child
reserves position quantity or entry funds independently.

Each group has a GTC reduce-only STOP_MARKET stop_loss and LIMIT take_profit.
Their original quantities and prices are immutable. An OCOQuantityAdjustment
records every peer fill and withdraws that quantity from the sibling allowance.
For each child, outstanding = original - own fills - peer withdrawals; both
outstanding quantities equal the group's live position quantity while active.
Fees apply only to real fills. Withdrawals never change or cancel historical fills.
A closing fill and the sibling cancellation publish together. The winning child
can be FILLED with fewer own executions than its original quantity when its peer
previously reduced the position. Its fills plus withdrawals then equal admission.
External price/quantity amendments, independently processing/cancelling children,
partial-position brackets and replacement groups on the same position are rejected.

Quotes follow increasing recorded sequence and nondecreasing effective time.
The stop child is evaluated first on each recorded quote; once triggered, it remains
a market order and takes precedence over a simultaneously marketable target on a
later quote. Stop triggering itself never fills. Each child covers the entire live
remainder: an execution consumes either all position quantity or all available
quote budget, so at most one child fills per quote. The other receives no execution
allowance and reconciles the actual fill. A repeated quote does not replenish
liquidity. No OHLC path or exchange queue ordering is inferred.

Group cancellation requests keep both children executable until the recorded
acknowledgement. A partial fill during that window remains committed; acknowledgement
cancels both remainders. A full-close winner makes later acknowledgement terminal.
Session stop completes an existing pending acknowledgement, or batches a new
non-liquidating request and acknowledgement for both legs. Completion remains
available through the two reserved cancellation inputs after ordinary capacity.
Any OCO preparation or financial error aborts the whole new input and remains
retryable/cancellable; it does not publish a one-leg denial or consume liquidity.

### Durable admitted advanced entries

AdvancedEntryPolicy schema 2 supports fixed approved MARKET/LIMIT/STOP_MARKET/
STOP_LIMIT parameters; GTC and market/limit IOC follow the existing matching policy.
It records version, next-opening gate, later-observation permission, simulated
liquidity, freshness and ordinary-input capacity. Dynamic price formulas, unapproved
parameter overrides, DAY/GTD/FOK and IOC stops are rejected.

AdvancedStrategySessionConfig schema 2 adds that policy and AdvancedEntryApproval.
This additional review binds the full configuration excluding the approval itself:
strategy ID/version/content digest, session, account/instrument, timestamp, quantity,
risk, costs, raw-feature timeframe and the entry policy. The original
StrategySpecification must still be exactly approved. Its schema, timing and digest
are unchanged; no existing approval authorizes the new behavior. A separate
AdvancedEligibilityPolicy selects explicit-next-open-resting-v2, and
AdvancedEligibilityDecision binds the same configuration and execution approval
alongside the unchanged independent retained research checks. Approval must precede
eligibility and both must precede activation. These are trusted application review
records, not signatures. Existing research evidence evaluates the original strategy;
the added eligibility is an explicit execution-policy review, not a claim that the
old backtest simulated resting orders.

AdvancedEntryReplayConfig schema 3 explicitly selects this path. Its feed age must
match the approved entry age. StrategyRuntime emits one AdvancedEntryIntent only
from an admitted, on-time, causally available TRUE decision. The intent retains the
exact decision/admission, strategy binding, account/instrument, quantity, policy,
configuration/approval and deterministic submission ID. The adapter accepts no
caller-supplied intent, risk outcome, order parameters or fill.

An actual on-time close quote stages submission, acceptance/OrderActivation and
reservation atomically. No execution occurs on that quote. Activation policy
close-submit-next-open-gate-v2 permits matching only after an actual on-time adjacent
locked OpeningDelivery with trusted declared-opening provenance. Its recorded
sequence must exceed the close submission sequence (close delivery + 1); producers
must leave that existing internal-command sequence gap. A missed opening cannot be
recreated from later quotes or complete OHLC. The opening may match a market/limit
or trigger a stop; a stop fill requires a later delivery. Once the opening gate
commits, explicitly approved ordered fresh quotes may match across subsequent bars.
Equal UTC timestamps require increasing recorded sequence. Backward observations,
unavailable/stale feeds, pause/interruption/exhaustion and conflicting identities
cannot produce execution. Quote redelivery cannot replenish simulated liquidity.

One account-owned kernel reuses the existing risk, reservation, matching, partial
settlement, fee and position engines. EntryCancellationCommand schema 3 binds the
exact emitted intent and requests/acknowledges remaining-entry cancellation. A racing
fill remains committed. Stop completes cancellation atomically, including an
existing pending request at capacity, while retaining any partial position. Protective
exits/OCO are admitted only after the entry is FILLED; they cannot overlap a resting
or partially filled entry. Automatic brackets and multiple entries remain unsupported.

SessionRecord includes the advanced intent and bounded entry_order progress;
Effects/checkpoints retain the corresponding bounded kernel head. The original
opening input, acknowledgement and later observations remain in the durable journal.
The account's internal gate index has at most one entry per owned kernel (32 maximum).
Recovery regenerates admission, decisions, matching, financial effects and this gate
from recorded inputs, compares exact heads/outcomes and never runs AI inference.
Full and checkpoint replay agree; recovered ACTIVE owners retain the operator
pause/resume gate and exact retry behavior. Unknown commits revoke readable authority.

## Atomicity, disk format and recovery

The existing kernel prepares events, the account prepares exact financial effects,
reservation changes and shared liquidity, and the session stages its audit record
and private candidate owners before the existing SQLite transaction. The durable
input, outcome, Effects, optional checkpoint and head commit together. Only then
does the session publish its complete candidate. Index/allocation failures roll
back candidate state. Account funding/economic denials remove only the proposed
fill, record cancellation and release the remaining hold atomically.

The SQLite physical schema, JournalEntry and storage-policy version remain v1.
The unchanged table layout stores versioned canonical payloads. The manifest
explicitly binds paper-18f-exits-v2 to AdvancedReplayConfig schema 2 and
paper-18f-entries-v3 to AdvancedEntryReplayConfig schema 3. V1 manifests
remain paper-18e-v1 and cannot be paired with v2 config. Advanced order commands,
config and settlement inputs are schema 2. OCO commands, child configuration,
submission, group/progress and settlement select explicit schema-3 contracts.
Optional OCO heads and bounded current OCO event deltas are omitted when absent;
historical v1/v2 wires and their original semantics remain unchanged. The physical
layout and advanced-session manifest stay unchanged: OCO is selected by its explicit
v3 command, never inferred for a historical single exit. Optional exit_kernel/exit_order state
is encoded only when present, preserving exact original v1 canonical records.
Older executables reject the new engine/payload; current code exactly replays v1.
A frozen journal exported from baseline 504606b3c47dab589b56ebc4a6707492048bbf3f
is tested in both recovery modes without rewriting its records.

Recovery constructs fresh admitted owners, verifies every record/index/digest and
checkpoint, and regenerates execution/accounting effects. V2 checkpoint prefixes
use full operational replay, so checkpoints are verified anchors rather than a
CPU accelerator for advanced state. OCO heads are likewise checked against
regenerated creation, matching, sibling reconciliation and accounting. Full and
checkpoint recovery must agree on
partial quantities, fees, triggers, cancellation, closed positions, liquidity,
financial state and idempotency. The shared PREPARING/COMMITTING/COMMITTED store
state preserves recovery_required on ambiguous commits or publication interruption;
no further readable authority or new processing is permitted until new-owner recovery.

## Bounds and remaining work

V2 kernels default to 128 inputs, configurable from 3 to 256. After capacity, only
a pending-order cancellation request and its acknowledgement may add two records;
no new market input can execute. Capacity is not a calendar expiry. Accounts retain
the existing 32-kernel bound. Orders requiring longer resting lifetimes are rejected
at the processing bound. Consumers should cancel safely before exhausting capacity.

Order processing now appends immutable history chunks and maintains bounded state
and fixed-depth retry/liquidity indexes. Session candidates share financial journals
and indexes, undoing only their staged suffix on failure. Full KernelSnapshot input
and event tuples are built and validated lazily for consumers.

New SessionRecord.exit_order, durable effects and checkpoints contain a versioned
KernelProgress head: counts, a chained prefix digest, cumulative quantities/costs
and current activation/trigger/cancellation state. New processing does not encode
the retained exit prefix. SessionSnapshot.exit_order continues to expose complete
history. Existing v2 snapshot records recover with their original wires and IDs;
subsequent writes use heads. V1 wires remain unchanged. Both recovery modes replay
and compare the operational prefix; a head is a reference, not execution authority.
OCO heads carry both bounded child progress records; full SessionSnapshot.oco
exposes both append-only histories. Active coordination visits exactly two children
and the existing bounded account kernel registry, with fixed-depth index updates.
Benchmark methodology, measurements, tests and remaining risks are in sections N/O/P of
[phase18f-report.md](phase18f-report.md).
Phase 19 portfolio allocation and the broker/API/dashboard boundaries remain deferred.
