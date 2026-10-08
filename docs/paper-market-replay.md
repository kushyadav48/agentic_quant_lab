# Deterministic market replay and sessions (Phase 18D)

Phase 18D is a bounded, serialized, offline Python layer. It owns a fresh
Phase 18B account, Phase 18C admitted runtime and strategy order adapter. It
adds no financial engine, network connector, MCP tool, worker or frontend.

## Construction and lifecycle

Construct `PaperSession(ReplayConfig(...), strategy=..., policy=...,
eligibility=..., evidence=...)`. The strategy configuration binds the stable
session/account identities and exact strategy version/content digest. Construction
creates an isolated account and repeats the existing formal approval and independent
eligibility checks. Snapshots, research records and entry intents confer no authority.
Configuration and records are strict frozen Pydantic contracts; replay configuration,
stale policy and envelopes are explicitly versioned.

The transitions are:

| Command | Required state | Committed state | Effect |
| --- | --- | --- | --- |
| start | CREATED | ACTIVE | Enable subsequent recorded deliveries |
| pause | ACTIVE | PAUSED | Retain feed deliveries, suppress evaluation/orders |
| resume | PAUSED | ACTIVE | Enable subsequent deliveries; no catch-up execution |
| stop | CREATED, ACTIVE, PAUSED | STOPPED via STOPPING | Cancel accepted order and release reservation |
| fail | ACTIVE, PAUSED | FAILED via STOPPING | Explicit operator failure and the same cancellation policy |

STOPPING is a recorded transition inside a single command transaction, never an
externally exposed half-completed cancellation. STOPPED/FAILED accept exact retries
only. Validation/staging errors leave the previous state intact; they do not implicitly
commit FAILED, allowing an exact safe retry. Commands require recorded UTC processing
time, sequence, stable command ID and an explicit reason reference. Repeating the
identical command returns the retained object; reusing an identity with changed or
invalid content raises `PaperIdentityConflict`. Invalid transitions fail without
consuming the identity or clock sequence.

Stop is non-liquidating. Open positions, collateral, valuation and fees remain as
committed, with no synthetic exits. An accepted order is cancelled through the
existing account-owned `CancellationRequest` path, which atomically releases its
reservation. If any reservation remains, termination is rejected. Pausing retains
pending reservations; missed openings are never manufactured on resume. An exhausted
feed with a pending order retains the reservation until explicit stop/fail.

## Recorded envelope and clock

`ReplayEvent` includes schema version, stable identity, positive ordering sequence,
source/dataset provenance, occurrence time, availability time, recorded delivery
time, effective processing time and the original canonical observation. Serialization
uses the established sorted finite JSON/Decimal representation and stable hashes.
Envelope identity, sequence, timestamps and source/dataset IDs must match its payload.
All timestamps are timezone-aware, normalized to UTC. The v1 boundary requires:

`occurrence <= available_at <= delivered_at <= effective timestamp`.

`ClockState` starts at the strategy configuration timestamp. Every new session
command or event must increase sequence and must not decrease effective time. Equal
times are accepted with distinct increasing sequences. No sorting, clock lookup,
sleep, network timing or retrospective insertion occurs. Feed delivery timestamps
also cannot decrease. Exact retries precede chronology/capacity/termination checks.
Session commands and feed events share a collision-detecting identity index.
Every lifecycle outcome retains its exact typed original command.

Market occurrences may arrive late or out of occurrence order, while recorded
delivery order remains authoritative. An old quote never rewinds the latest market
occurrence. A stale observation is retained as a rejected-for-evaluation outcome.
A completed-bar series must additionally satisfy the existing runtime's chronological
bar constraints; duplicate/overlapping/older bars cannot rewrite its history and
fail the whole coordinated event. Equal-payload quotes with distinct event IDs remain
separate deliveries; equal-payload repeated completed bars are invalid series inputs.

Kernel sequences remain local to the existing order engine. A close quote generates
an entry submission at quote sequence + 1; a valid opening must have a greater kernel
sequence. Producers must leave room for that generated command (for example start=1,
bar close=2, close quote=3, generated submission=4, explicit opening=5). Stop derives
a cancellation sequence greater than the kernel's current sequence. These rules
are deterministic and preserve Phase 18A–18C input/output identities.

## Feed and stale policy

`accept_delivery` and `advance_clock` are pure boundaries coordinated by the session
owner. Session identity retention detects duplicates and rejects conflicts before
feed advancement. `FeedState` reports readiness/interruption/exhaustion, last accepted
event/delivery, latest market occurrence, delivery count and a stable freshness reason.

Heartbeat progresses logical time without inventing an observation. Interruption
blocks strategy/order processing and clears freshness. Resumption requires an
interruption and remains MISSING until a new observation. Observations delivered
while interrupted remain in audit but cannot restore freshness. Exhaustion is an
explicit producer event and is terminal for that feed; EOF itself changes no state.
A new replay session is needed for a new feed after exhaustion.

The versioned `StaleFeedPolicy.maximum_age` is an explicit nonnegative timedelta.
An observation is stale when effective time minus occurrence time is strictly greater
than the threshold. Equality is fresh, including a zero threshold. Each observation
is checked individually even if a more recent quote is retained. Heartbeat/commands
also evaluate the retained market's age. Reasons are `missing`, `stale`,
`interrupted`, `exhausted` and `fresh`. Only active sessions with fresh required
data evaluate or submit/match an entry. Fresh observations recover freshness after
staleness/resumption. Existing positions, fees and reservations are preserved on
stale rejection; no fills or valuation marks are invented.

## Replay and strategy integration

`PaperSession.replay(events, maximum_events=N)` processes at most the explicit bound
in the supplied order without consuming the future suffix. Each event is its own
transaction; earlier accepted inputs remain committed if a later event fails.
Ordinary input retention is bounded by `ReplayConfig.maximum_inputs` (up to 100,000),
with one additional terminal stop/fail record reserved so reaching capacity can never
strand a pending reservation. Total retained records are at most maximum_inputs + 1.
A terminal command can be sent through `process`/`stop` even after ordinary capacity
is reached; each replay batch still requires its explicit processing bound. Completed-bar
evaluation retains the existing runtime limit (up to 10,000). Immutable snapshots
are materialized only when requested. Appending a future suffix cannot alter saved
records, decisions or snapshots.

The active fresh processing path is:

1. Validate envelope, retained identity, provenance, clock and feed candidate.
2. Deliver a completed bar to `StrategyRuntime` for existing causal TRUE/FALSE/
   UNAVAILABLE comparisons, offsets, groups and supported raw features.
3. Convert an actual on-time close quote and retained entry intent through
   `StrategyOrderAdapter.submit`; existing risk and account-owned reservation apply.
4. Deliver a proven adjacent on-time locked opening through `process_open`; existing
   execution risk, Decimal pricing, settlement/cancellation and opening acknowledgement
   remain account-owned.
5. Prepare the immutable session outcome and retry/journal index, then publish once.

An ordinary quote never matches an accepted strategy order. The runtime still
requires BAR_CLOSE -> NEXT_BAR_OPEN and emits at most one entry. Late bars produce
UNAVAILABLE when within the stale threshold; older bars that violate series chronology
are rejected. Formal approval, independent eligibility, exact version/digest,
quantity increments, MID bars, EQUITY denomination/prefunding, trusted source
provenance, and entry-only restrictions remain unchanged. Exits/reversals and new
indicators/languages remain unsupported.

## Historical data limitations

`quantlab.replay.HistoricalReplay` is outside the paper core and accepts a validated
`StoredDataset` from the existing local dataset store. It rechecks the stored content
identity/metadata/quality before producing immutable events. It binds the original
row's dataset/source IDs to the immutable stored version digest and an explicit
`stored:sha256:...` source reference. `sources` provides the configuration bindings.
Stable event IDs hash that version, original row position and canonical observation.

Storage does not retain network arrival order. This adapter explicitly models delivery
at the observation's recorded availability, retaining original row order; it makes
no claim to reproduce missing historical arrivals. Availability chronology that
cannot satisfy the session/feed constraints is rejected rather than sorted. The
adapter never calls a historical provider or performs network I/O. Previously loaded
canonical data from a provider may be stored and replayed locally.

Historical bars expose OHLC only at/after completed-bar availability. Their `open`
field never becomes an execution quote. Historical bid/ask ticks never become proven
bar openings, even if their timestamp coincides with a bar boundary. Historical
sources cannot construct an envelope classified as an opening. Such data supports
observation/causal evaluation; successful strategy execution requires opening evidence
that these datasets do not carry. There is no inference of calendar adjacency.

The supported execution demonstration is a trusted local synthetic producer with
explicit `synthetic-declared-openings` provenance, recorded close quote, adjacent
previous-close identity, genuine opening instant and locked opening bid/ask price.
This label declares fixture provenance; it is not a signature, authentication service
or a claim that arbitrary historical data is execution-ready.

## Atomic publication and audit

The session privately stages candidate account/runtime/adapter owners using the exact
existing engines. Account order, financial settlement/release and opening provenance
still publish atomically inside that unexposed candidate. The session prepares the
content-addressed outcome, serialization, root allocation and reversible journal/
identity indexing before one session root swap exposes all components together.
Financial caches are copied only for this bounded single-entry account. Runtime
journals/features use incremental staging with committed-count views; failure removes
only the added suffix/index entries. There is no full-history serialization/copy in
the per-event path. Reentrant mutation is rejected.

This guarantee is for one serialized in-memory caller. It covers synchronous
validation, allocation, serialization, financial/index staging and injected publication
exceptions, including an exception immediately after a swap. It does not promise
concurrent readers/writers, crash durability, process recovery or distributed atomicity.
Internal underscore owners are trusted implementation details and must not be mutated
or retained as independent execution authorities.

Each frozen `SessionRecord` binds configuration digest, input identity/digest, previous
record ID, logical time/sequence, lifecycle transitions/reason reference, original
typed command or complete market envelope, actual strategy decision/features/dependencies,
actual order/risk/fill/cancellation records, new financial events, resulting account and feed state.
This is the future debugger/journal projection boundary. Explanations are actual
engine outcomes and stable reason codes; no LLM explanation or decision generator
exists. Original commands are retained directly rather than reconstructed from
action reasons.

`quantlab.replay.verify_replay` consumes a bounded immutable recorded input tuple
(including start/lifecycle commands) in two fresh owners. It compares deterministic
final financial/lifecycle/feed/admission/intent/order/opening projections and the
content-addressed audit chain, which binds every prior input and outcome. The bounded
verification tuple may include the one reserved terminal command beyond ordinary
capacity. It returns an immutable verification record and outcome digest.
It is in-memory verification, not persistent event storage or a recovery import API.

## Performance and next boundary

The reproducible benchmark is `benchmarks/phase18d_replay.py`. It covers quote-only
feeds, FALSE completed bars, indexed raw-feature/offset rules and one actual synthetic
entry followed by later bars. Evidence setup, event creation and explicitly requested
snapshot materialization are excluded from processing time. Event counts, configuration,
Python/platform/CPU, total seconds and events/second are reported. No correctness test
uses a wall-clock performance threshold. See [Phase 18D report](phase18d-report.md)
for measured observations and exact regression results.

Phase 18E must add durable input/provenance publication, reconstruction and recovery
without bypassing admission or splitting session/financial/opening commits. No session
store, crash restore, continuous runner, advanced order/partial fill, portfolio allocation,
external streaming feed, brokerage, MCP trading endpoint, API or dashboard ships here.
