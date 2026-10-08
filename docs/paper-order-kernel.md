# Phase 18A: deterministic paper order kernel

Phase 18A is an offline, synchronous, one-order execution kernel. It is not a
complete paper-trading session. There is no strategy admission, automatic signal,
account ledger, persistence, recovery, live execution or agent/MCP/API integration.

## API and ownership

`PaperOrderKernel(KernelConfig(...))` owns its immutable construction-time
instrument, flat research equity, observed peak, risk policy and execution costs.
`process(input)` returns a tuple of immutable execution records; `snapshot`
returns retained inputs, order state, logical clock and execution journal.
Use each instance from one serialized caller. No asynchronous or concurrent
service, callback injection or background task exists.

The equity/peak inputs are trusted application construction state, not cash or a
claimed risk decision. Commands cannot supply account context or policy overrides.
Only multiplier 1 and quote-currency research units are supported. There is
exactly one submission per instance, including after rejection or cancellation.
After the single fill no further entry can reuse the unchanged flat state.
Read-only snapshots, exact retries and terminal cancellation outcomes remain
available. Later market deliveries can be retained without changing prior fills.

## Inputs, identities and logical time

Inputs are strict, frozen Pydantic models with aware UTC timestamps:

- `MarketDelivery`: producer-owned stable event ID, sequence, effective processing
  timestamp, actual delivery time and canonical `MarketQuote`.
- `OrderSubmission`: stable command ID, sequence, processing timestamp, retained
  market causation ID, instrument, BUY/SELL and positive quantity.
- `CancellationRequest`: stable command ID, sequence, processing timestamp,
  retained causation ID and the kernel's order ID.

Producer IDs are supplied explicitly; the kernel never generates random or
wall-clock identities. Input IDs share one namespace. Order and output event IDs
are SHA-256 identities over versioned namespaces and canonical content, including
the configuration digest. Output events retain their causation references and
both output journal sequence and triggering input sequence.

New inputs require strictly increasing sequences and nondecreasing processing
time. Sequence gaps are allowed. Quote observation and delivery times must also
be nondecreasing. Different observations at equal timestamps/prices remain
distinct when their IDs differ. Arrival-order corrections are rejected; there is
no late-data sorting, rewriting or recovery policy.

Processing requires `timestamp >= max(delivered_at, quote.available_at)`, and
delivery cannot precede observation. An unavailable/future payload is rejected,
not scheduled for later. An observation originating before submission can be
retained when delivered later, but cannot fill the order. A fill requires a
subsequent market input and `quote.timestamp >= submission.timestamp`.
Equal timestamps are valid when the recorded sequence is later.

A submission needs a retained market cause and a known executable quote.
Acceptance uses the latest delivered quote; it never fills from that quote.
Execution occurs only on a subsequent eligible delivery, at its effective
processing time. The record preserves observation, availability and delivery
times, so delayed delivery never backdates execution.

Canonical serialization expands defaults, sorts keys and preserves exact
Decimal values with normalized spelling, without ambient Decimal rounding.
Decimal expansion is bounded to 4,096 digits and a canonical encoding to 4 MiB.
Exact retries return the original output tuple, even after later inputs.
Identity reuse with different canonical content raises `PaperIdentityConflict`.

## State machine and risk

Supported order type is `market`; supported time in force is `gtc`.
Unsupported values fail strict validation rather than being ignored.

```text
SUBMITTED -> ACCEPTED -> FILLED
SUBMITTED -> REJECTED
ACCEPTED  -> CANCELLED
```

Submission processing records SUBMITTED, acceptance risk, then ACCEPTED/REJECTED.
Before execution it records a second risk outcome. A pre-fill rejection/error
cancels the accepted order; it does not retroactively reject acceptance.
Zero command latency means an accepted cancellation immediately records CANCELLED.
Cancellation after FILLED/REJECTED/CANCELLED records a terminal denial without
reopening or reversing the order.

Phase 11 `evaluate_entry_risk` owns the applicable flat-entry checks. Context is
built internally from the submission, current quote side and fixed owned
equity/peak. BUY maps to LONG; SELL maps to SHORT. Reference notional remains
unadjusted executable-side price times quantity. Existing exact threshold and
stable rejection-reason behavior is preserved; prospective costs do not change
the context. Malformed service results, changed context or service errors produce
a sanitized risk-error outcome and deny acceptance/execution.

Standalone kernels infer no portfolio, cash, margin, conversion, sizing,
liquidation or account update. Account-owned kernels delegate transaction
publication to their owner after the same risk/pricing preparation. Quantity increments are checked by exact integer ratios.

## Pricing and records

BUY uses observed ask; SELL uses observed bid. Synthetic spread must be zero.
Fixed adverse slippage, commission per unit and fee per fill reuse
`ExecutionCostConfig`, `CostBreakdown`, the extracted
`backtesting.execution.price_execution` arithmetic and existing `Fill`
reconciliation validators. The old next-open wrapper retains historical pricing
and its error boundary; `run_backtest()` and strategy timing contracts are unchanged.

The embedded `Fill` entry action is an economic record vocabulary reuse. It does
not turn a command into a strategy signal or reinterpret approved
BAR_CLOSE -> NEXT_BAR_OPEN intent.

For bid 100, ask 102, quantity 2, slippage 0.5, commission/unit 0.1 and fee 1:
BUY executes at 102.5; SELL at 99.5; explicit cost is 1.2. Observed spread is not
added again. Slippage is in execution price and its descriptive cost breakdown,
not a second debit. Fees are recorded; no cash ledger is updated.

Fill records preserve the full submission, source delivery, pricing assumptions,
execution economics and configuration digest. Prices are not tick-rounded.
Nonpositive prices, exponent overflow or unreconciled price effects fail without
committing a partial fill.

All input validation, risk/record construction, pricing and snapshot validation
complete before publishing state or caching the retry identity. Invalid input or
pricing failure leaves both unchanged. A recorded risk denial is a successful
state transition, not an exception.

## Verification and boundaries

`tests/paper/` covers strict contracts/JSON, transitions, equal-time ordering,
availability/delivery, causal prefixes, applicable risk thresholds and failures,
pricing, cancellation, identity conflicts, Decimal isolation, independent
instances and denied I/O/background execution.

Compatibility suites retain Phase 7/8 timing/cost behavior, Phase 11 risk, Phase
10 validation, features/analytics and Phase 16/17 agent/MCP boundaries.
No dependency was added. Journal records are in-memory integrity checks, not
authentication, persistence or a recovery implementation.

Phase 18B now adds separate account ownership/reservations and research position accounting; see [paper-accounting.md](paper-accounting.md). Standalone kernel behavior and entry-only scope above are unchanged. Account-created kernels route through atomic account coordination; `kernel.process()` publishes a fill only with its financial application, or commits an account denial/cancellation plus reservation release. Private preparation cannot publish an owned fill separately. See the transaction boundary in [paper-accounting.md](paper-accounting.md).

Remaining Phase 18 work: exact strategy admission
and validation eligibility, strategy runtime/timing policy, feed/session
integration, configurable latency, stale-feed behavior and durable recovery.

Verified in the existing WSL Python 3.11.17 environment: 115 paper tests,
468 backtesting/risk tests, 296 feature/validation/analytics tests,
753 orchestration/MCP tests and 2,588 full-suite tests passed. No packages
were installed.
