# Strategy admission and causal paper runtime (Phase 18C)

Phase 18C connects one formally approved exact strategy to independently verified,
previously produced research evidence and the existing single-entry PaperAccount
execution owner. It is a bounded synchronous offline Python boundary. It adds no
feed service, transport, persistence, MCP trading tools, broker integration or LLM
execution calls. Phase 18 as a whole remains incomplete.

## Admission and distinct authorities

StrategyRuntime construction calls admit_strategy; an AdmissionRecord alone cannot
activate a runtime. The strategy is revalidated using Phase 5's exact ID, version,
content digest and approval contract and Phase 7's existing supported-intent check.
A revised or invalidated specification is rejected. Phase 16 acceptance for approval
and Phase 17 research-operation completion grant neither formal approval nor paper
eligibility. No strategy approval is created here.

The caller must supply an explicit EligibilityPolicy with policy ID/version,
rationale reference, required OOS mode, minimum evaluation observation count,
minimum robustness candidate count, minimum OOS return and minimum OOS drawdown.
There is no default financial threshold. An explicitly recorded None disables that
metric requirement; it does not replace a missing policy. Drawdowns use Phase 9's
negative fractional sign: for example a declared -0.10 bound rejects a drawdown
below -0.10. Requirements apply to every supplied OOS segment. Observation counts
sum independent evaluations, including repeated observations in overlapping folds;
this is not a count of unique market observations.

The separate EligibilityDecision binds the policy digest, exact strategy, exact
evidence/result references, dataset versions, reviewer, decision logical timestamp
and reason reference. An eligible flag is necessary but insufficient. Duplicate
result references cannot inflate evidence counts. These records are immutable and
content addressed. Denied admissions retain stable reason codes and activate no
runtime. A malformed session configuration raises PaperInputError because it cannot
supply valid audit metadata.

## Previously verified research evidence

ResearchEvidenceStore is an application-retained, read-only artifact set. Each
ResearchEvidence binds an existing BacktestResult, HoldoutResult, WalkForwardReport
or RobustnessReport to a canonical result digest, request digest, exact dataset
version digests, provenance reference, verification status, verifier and verification
time. The verifier reference must differ from the eligibility reviewer. Verification
must follow the report's recorded final observation and precede eligibility review;
review and formal approval must precede activation. An unverified/rejected artifact,
a missing reference or a mismatched association cannot pass admission.

Admission requires a backtest, a chronological holdout or walk-forward OOS report,
and an approved robustness baseline, with the explicit policy determining evidence
quantity and financial requirements. It checks exact strategy identity, instrument,
timeframe, price basis, quantity, costs and risk configuration; segment membership,
chronological train/OOS separation, fold plans, analytics/result identity and
accounting associations; and exact-version baseline and approved parameter-variant
structure using the existing Phase 10 structural comparison. Each report retains
its research initial capital; live risk uses the actual account equity captured by
the owned kernel. Research capital is not substituted for live account equity.

Activation performs structural and digest verification, not backtesting, feature
history recomputation, model inference or analytics regeneration. Original Phase 10
reports do not embed dataset versions or independently authenticated verification.
The new envelopes explicitly bridge that missing provenance. The trusted application
must verify and retain those artifacts before constructing the store; nothing is
inferred from a successful run. Verifier/reviewer references are audit attestations,
not cryptographic signatures or authentication. Dataset numeric provenance and the
truth of a recorded verification are the producer's responsibility. No agent or
MCP endpoint can write this store or declare eligibility.

## Supported scope and compatibility

- One account, one exclusive runtime adapter, one immutable approved strategy and
  one entry intent for the entire runtime, including denied or cancelled entries.
- AccountConfig's prefunded linear EQUITY research P&L units, multiplier 1, quote
  denomination, no base currency; exact positive quantity increment alignment.
- One exact supplied instrument, the existing Timeframe labels and MID bars.
  Explicit endpoints remain authoritative; no exchange calendar is inferred.
- Long BUY, short SELL or BOTH declarations; simultaneous TRUE long and short
  signals are recorded as conflicting_signals and emit no order intent.
- Existing market/constant/parameter operands, offsets, comparisons, crossing
  rules, ALL/ANY groups and raw OHLC feature aliases. BID/ASK operands require a
  different evaluation input contract and are rejected.
- BAR_CLOSE -> NEXT_BAR_OPEN only. Execution additionally requires an actual,
  on-time locked opening quote with bid = ask = opening_price. This narrow policy
  supports existing quote pricing without synthesizing a quote from a bar.
- Existing risk and pricing configurations; synthetic spread must be zero.
  Slippage, commissions and fees continue through Phase 18A pricing and 18B exact
  reservation/settlement reconciliation.

Executable exits, stop/take-profit rules, reversal, pyramiding, session filters,
external sizing, other asset/accounting configurations, unlocked opening quotes,
gapped opening schedules and multiple competing strategies are rejected. Pure
Phase 18B accounting reductions/closures remain accounting operations; this phase
does not create corresponding executable exit orders.

## Runtime state and feature availability

The runtime transitions from active to entry_intent_emitted once and keeps evaluating
later deliveries for audit while suppressing additional intents. The owned order
retains Phase 18A's SUBMITTED -> ACCEPTED -> FILLED, rejection and cancellation
states. A terminal order cannot reopen.

BarCloseDelivery carries only a canonical explicitly delivered complete MarketBar,
event identity, delivery time, increasing sequence and logical processing timestamp.
The complete bar cannot be delivered before end_time or used before available_at.
Bar series identity, overlap and chronology reuse Phase 4 validation. Earlier or
conflicting deliveries cannot rewrite history. Equal logical timestamps are ordered
by increasing sequence; delayed bars can share a processing time with a later close.

RuleEvaluator implements the existing TRUE/FALSE/UNAVAILABLE semantics. Offsets
count delivered observed bars. Missing offset/crossing history stays UNAVAILABLE.
A late bar is retained as late_bar with UNAVAILABLE outcomes; it cannot emit a
retrospective signal. On-time raw features are computed from that single delivered
bar using the existing feature pipeline, retaining input_start, timestamp,
available_at, implementation, parameters and source dependencies. Dependency input
IDs retain the exact bar indices requested by rule offsets and crossings plus the
current decision bar, and complete
input observations remain in immutable snapshots. Numeric feature values cannot be
supplied or overridden externally.

SMA, EMA, RSI, returns, volatility, ML and other batch-only feature delivery are
rejected at admission under causal-raw-only-v1. No retrospective batch result is
presented as a live feature. Existing ML model/prediction cutoff and digest contracts
remain unchanged; a causal, provenance-preserving prediction delivery adapter is a
future prerequisite for using them here. No inference shortcut is provided.

## Decision-to-order causality and account atomicity

StrategyOrderAdapter consumes only its StrategyRuntime's retained EntryIntent. Its
submit method accepts an actual on-time close MarketDelivery for risk pricing,
not an externally supplied decision, intent, risk approval, quantity or direction.
The deterministic command ID hashes the complete attributed intent: strategy
ID/version/digest, admission/policy, account/session, decision, causation, side and
quantity. The kernel causation reference remains the real retained quote required
by Phase 18A. audit_snapshot retains the exact admission, decision, intent, owned
kernel snapshot and original opening observations together.

PaperAccount's private entry batch stages a close quote and submission using the
existing order kernel, constructs a reservation using the shared Phase 18B policy,
then publishes owned order state and the reservation through its existing single
publication boundary. Insufficient prefunding or an unexpected preparation/staging
failure leaves both prior snapshots and indexes unchanged. Risk rejection records
an order denial and reserves no funds. Standalone kernels retain Phase 18A behavior.

OpeningDelivery contains an opening price, an actual quote, explicit new-bar bounds,
timeframe and previous_close_id. It has no high/low/close fields. V1 requires
bar_start = signal source_bar_end, matching timeframe/instrument, on-time
availability/delivery and an actual locked quote equal to opening_price. Ordinary
quotes and subsequently delivered complete bars cannot act as opening events.
The runtime's source close must still be its latest completed bar when submitting
or processing a new opening; a missed opening cannot be executed retrospectively.
Distinct order-local sequences preserve close delivery -> submission -> opening
even when their logical timestamps coincide. Runtime, order and account sequences
are separate domains connected by explicit causation references.

The adapter forwards only that validated opening to PaperAccount's private strategy
transaction, which reuses the account-owned kernel's preparation, pre-fill risk,
observed-side pricing and coordinated settlement. The opening delivery's kernel ID
hashes its complete classification, interval and provenance; reusing the external
input ID with changed content fails explicitly. A funding gap prepares cancellation
and reservation release without committing a fill.

Before publication, the owner constructs an immutable opening acknowledgement with
the original OpeningDelivery, its canonical payload, the derived kernel delivery
and the exact operation result. It validates canonical serialization and allocates
the opening retry index and complete candidate publication before staging financial
indexes. One account publication pointer then selects account state, order inputs,
execution records and opening acknowledgements together. The adapter reads both its
openings and retry results from that publication; it performs no acknowledgement
allocation or indexing after commit.

Acknowledgement preparation, serialization, index allocation or financial staging
failure leaves prior account/order snapshots, journals, reservations, positions,
fees and retry indexes unchanged. Publication errors restore the prior publication
and reverse staged financial indexes. The original opening remains safely retryable.
The same transaction applies to charged fills and unfunded cancellation/releases.
The adapter also denies external position/equity/peak/reservation changes that
invalidate its exclusive flat entry state.

All exact retries return retained results and create neither another decision,
reservation nor fill. Retry checks precede new-event clock/capacity/terminal checks.
Opening retries return the exact account-retained result even after termination or
later runtime input. Conflicting payloads fail without mutation. Runtime and account
staging roll back on failure. Previously returned snapshots,
decisions and intents cannot be changed by future events.

## Bounds, determinism and authority

There are no network requests, clocks, sleeps, workers or inference in this path.
Financial results use supplied logical times and Decimal policies only. SHA-256
identities use the existing bounded canonical serializer. Its narrow timedelta
encoding adds exact days/seconds/microseconds for research reports; existing paper
record encodings are unchanged.

The runtime retains at most the configured 1–10,000 deliveries (default 5,000).
A fixed-length read-only view exposes the retained bar journal to RuleEvaluator
without copying it. Rule operands resolve exact offset/crossing dependencies by
index, and raw feature observations use an owner-private indexed store. Processing
work depends on the approved rule/feature width, not elapsed session history.
Raw feature computation receives one bar per event.
Complete journals are materialized/revalidated only when snapshots are requested;
normal processing does not repeatedly serialize or copy the full journal. A
250-event work-bound test and a 150-event offset-100 test verify this behavior without using
wall-clock timing to choose financial outcomes.

These are trusted, serialized local Python owners, not a concurrent or authenticated
remote execution service. Private-object mutation, arbitrary Python reflection,
verifier impersonation and process interruption recovery are outside the security
claim. MCP, orchestration and LLM modules do not import or expose paper operations.
Formal approval, recorded evidence verification and trusted application activation
remain separate prerequisites. No new dependency is required.

## Phase 18D integration boundary

An offline trusted caller can deliver all observations explicitly and complete one
causal entry today. Phase 18D must supply the actual on-time completed-bar and
opening events, real adjacency/calendar rules and session lifecycle. Phase 18C
neither manufactures those observations nor claims that a feed is connected.
Without a genuine qualifying opening event the entry remains unfilled; no later
quote or retrospective OHLC is substituted. Widening support beyond locked,
adjacent on-time openings requires an explicit causal price-source contract and
matching tests. Phase 18E persistence/recovery and Phase 18F advanced matching,
continuous sessions, multi-strategy allocation and executable exits remain future
work.
