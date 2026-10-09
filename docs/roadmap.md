# Roadmap

**Status: early development.** Phases 1 through 17 are implemented: the documentation/package foundation, strict market-data domain contracts, and the Dukascopy historical tick-to-quote adapter with offline tests, followed by dataset quality reports, causal UTC resampling, and immutable local SQLite storage. Successful live downloading remains unverified after an HTTP 429 probe. Phase 5 adds immutable strategy contracts, semantic validation and exact-version approval with offline tests. Phase 6 adds causal Decimal features and structural strategy-reference compatibility. Phase 7 adds approved-strategy deterministic bar replay with zero-cost next-open fills and Decimal research equity. Phase 8 adds deterministic spread/slippage/commissions/fees and gross/net accounting. Phase 9 adds separate deterministic net trade/account-return, volatility, drawdown, holding-duration and Sharpe analytics. Phase 10 adds independent chronological holdouts, rolling/expanding walk-forward folds and explicitly approved parameter-variant sensitivity. Phase 11 adds mandatory deterministic fixed-quantity entry limits, causal drawdown gating and audited decisions. Phase 12 adds leakage-safe offline supervised datasets, deterministic ridge artifacts and OOS predictions integrated as canonical ML features. Phase 13 adds provider-neutral async LLM contracts, structured-output validation, bounded retries/usage checks and deterministic fake-provider tests. Phase 14 adds strict natural-language interpretation into unapproved Phase 5 proposals or clarification questions, with deterministic validation and complete invocation provenance. Phase 15 adds chart + text interpretation with ordered opaque image inputs, visual evidence, conflict clarification and the same unapproved proposal conversion. Phase 16 adds bounded LangGraph routing, terminal clarification and explicit human interrupt/resume without Phase 5 approval or quant execution. Phase 17 implementation is COMPLETE and externally runtime-verified; Phases 18A–18E add the offline order kernel, prefunded research account, bounded causal strategy admission/runtime, deterministic market sessions and durable local persistence/recovery; the supported bounded Phase 18F scope includes advanced orders, protective OCO and separately approved durable strategy entries, with retained-history copying remediated; bounded Phase 19 portfolio coordination and Phase 20 trade/research history are implemented; bounded Phase 21 local FastAPI is implemented; Phases 22–25 remain planned. The phase order follows dependencies, and later phases must not be treated as available features.

Deliver each phase as a small, reviewable increment with relevant behavioral tests, documented assumptions, and an updated architecture/status statement. Add dependencies and modules only when the phase needs them. Phase 23 consolidates end-to-end coverage; it does not postpone unit/integration testing until the end.

## 1. Foundation and architecture

Purpose: establish repository conventions and a shared technical direction before functionality.

Deliverables: README, architecture source of truth, roadmap, minimal src-layout Python package, pytest development extra, ignore rules, and a credential-free environment template. Verify file structure and configuration. No trading code, Git initialization, or commit is part of this step.

## 2. Market-data domain models

Purpose: establish market-neutral contracts before choosing ingestion implementation.

Implemented: strict Pydantic Instrument, TradingCalendar reference, MarketBar, and MarketQuote models; AssetClass, PriceType, Timeframe, and VolumeType enums; Forex metadata checks; timezone-aware UTC observations; Decimal prices/quantities; and contract tests. Calendar schedule resolution, dataset-wide validation, and storage remain future work; Phase 3 supplies historical quote ingestion.

Deliverables: typed instrument, asset-class, timestamp, price/quantity, currency, calendar, and observation models, with Forex metadata and schema tests. Add Pydantic if selected for these contracts; document bid/ask/mid semantics and extensibility.

## 3. Forex historical-data ingestion

Purpose: obtain traceable historical observations through the first concrete market adapter.

Implemented: provider-neutral quote request/protocol; Dukascopy public hourly tick adapter for EUR/USD and USD/JPY; injectable urllib transport with finite timeouts and payload bounds; isolated LZMA-Alone/big-endian decoding; metadata-based Decimal scaling; UTC range filtering; payload-hash provenance; and distinct no-data, transport, corruption, and unsupported-configuration handling. Tiny generated binary fixtures and HTTP doubles keep all tests offline. Phase 2 contracts and runtime dependencies are unchanged. Storage and bar aggregation are supplied by Phase 4 below.

Deliverables: selected provider/format adapter, licensing notes, raw-source provenance, import configuration, error handling, and small legal fixtures. Verify ingestion with reproducible inputs; do not imply screenshots are historical datasets.

## 4. Data validation, resampling, and storage

Purpose: produce reliable, versioned research datasets.

Implemented: immutable typed quality summaries distinguishing exact duplicates/repeated timestamps, ordering and consistency errors, empty warnings, configurable consecutive-event gap thresholds, and caller-scheduled coverage gaps; opt-in stable normalization with explicit duplicate policies; strict causal UTC bid/ask/mid OHLC and bar coarsening with missing-data policies and no invented quote volume; complete deterministic dataset metadata/provenance; transactional standard-library SQLite storage for validated datasets with lossless typed reads and integrity checks; offline behavioral and pipeline tests. Pure Python canonical sequences are the initial representation; no dependency is introduced. Missing observations/bars are never fabricated. Pandas versus Polars, session/DST calendar aggregation, raw payload retention, columnar export, and PostgreSQL remain open or deferred. Phase 5 adds strategy contracts below; subsequent phases remain unimplemented.

Deliverables: duplicate/gap/OHLC/timestamp checks, explicit missing-data policy, causal resampling and bar-availability semantics, normalized storage adapter, dataset identifiers, and quality reports. Choose dataframe and file formats here. A local storage adapter can precede PostgreSQL.

## 5. Strategy specification

Purpose: define the shared declarative strategy contract for all strategy input routes.

Implemented: immutable schema-v1 StrategySpecification, identity/revision/content separation, bounded ALL/ANY rules, typed operands/features/parameters, long/short side constraints, explicit stop/target units, UTC session and safe timing intent, provenance, deterministic content digest, and exact-version approval lifecycle. Revisions drop approval. The manual approval example and contract policy live in [strategy-spec.md](strategy-spec.md). All 303 tests pass, including 72 Phase 5 behavioral cases. No dependencies or execution engine are added. Phase 6 adds feature computation below.

Deliverables: versioned StrategySpecification, bounded declarative rules, parameter/schema validation, provenance, immutable version identities, and user approval state. Define approval invalidation on edits and reject unsupported or ambiguous rules. Establish an approved manual strategy fixture for subsequent engine work.

## 6. Feature/indicator engine

Purpose: compute reusable, deterministic strategy inputs.

Implemented: immutable feature observations/requests/definitions; fixed read-only registry; raw OHLC, simple/log returns, SMA, SMA-seeded EMA, Wilder RSI and population volatility of simple returns; isolated 34-digit Decimal calculations; Phase 4 input validation; omitted warm-up observations; full dependency availability including recursive history; shared multi-feature computation; and structural strategy-reference checks. All 411 tests pass, including 107 Phase 6 cases. No dependencies, strategy execution or backtesting are added. Formulas and limitations are documented in [feature-engine.md](feature-engine.md). Phase 7 adds approved-strategy simulation below.

Deliverables: an initial small indicator set, feature registry/contracts, warm-up and missing-value policies, availability timestamps, and reference-value/causality checks. Add only the quantitative libraries required by implemented features.

## 7. Backtesting engine

Purpose: replay approved strategies against historical data with explicit timing.

Implemented and complete: immutable config/signals/fills/positions/trades/equity/results;
strict exact-version approval, bar and feature validation; declarative comparisons,
ALL/ANY tri-state evaluation and offset-aware crossings; causal BAR_CLOSE signals
and NEXT_BAR_OPEN fills; fixed quantity with existing quantity-increment checks;
one-position LONG/SHORT/BOTH lifecycles; deterministic conflict rejection; gross
Decimal P&L and research equity; final open-position retention and unfilled final
signals. Unsupported stop_loss, take_profit, session, sizing_reference and BID/ASK
operands are rejected. There is no brokerage cash/margin model, cost realism or
performance analytics. All 591 tests pass, including 167 Phase 7 cases. No
dependencies are added. See [backtesting-engine.md](backtesting-engine.md).

Deliverables: deterministic bar replay, signal/fill/position lifecycle, fixed research
sizing, immutable trade/equity artifacts and explicit causal timing. Phase 8 extends
spread/slippage/cost behavior below; Phase 9 supplies separate analytics below.
Cost-free fixtures establish mechanics, not realistic performance claims.

## 8. Spread, slippage, and cost modeling

Purpose: make execution assumptions explicit and useful for research.

Implemented: frozen strict zero-default ExecutionCostConfig; MID/BID/ASK-aware
synthetic spread with explicit TRADE incompatibility; adverse fixed slippage;
quantity-based commission and fixed per-fill fees; causal reference/execution
prices and immutable cost breakdowns; reference gross, execution gross and net
trade P&L; immediate entry-cost recognition and research equity accounting.
Next-open timing, delayed-bar decision gating, final unfilled signals, open final
positions, no same-open reversal, quantity increments, isolated Decimal arithmetic
and zero-cost economics remain intact. Exact hand-computed long/short trades and
causal replay tests cover all cost components without network access or new dependencies.
See [execution formulas, invariants and limitations](execution-cost-model.md).

This completed phase is deliberately limited to deterministic fixed-cost research
execution. Latency simulation, order types, partial fills, liquidity/impact,
tick rounding, financing, FX conversion and intrabar stop/target semantics remain
deferred. Phase 9 supplies separate analytics below; subsequent roadmap phases remain planned.

## 9. Performance analytics

Purpose: derive authoritative performance results from simulated account/trade records.

Implemented: separate quantlab.analytics package consuming BacktestResult; immutable
strict definition-versioned reports; authoritative capital-relative account returns;
net closed-trade classification/ratios and elapsed holding times; initial-baseline
equity returns; population volatility and rigorous per-period/explicitly annualized
Sharpe; signed drawdown series, independent amount/percentage maxima, and observed
recovered/unrecovered episodes. Undefined metrics use None. Boundary revalidation,
account/equity consistency checks, isolated 34-digit Decimal contexts, offline
hand-computed and real-engine tests preserve deterministic behavior and execution.
No dependency is added. See [performance-analytics.md](performance-analytics.md).

Exposure models, calendar/currency inference, CAGR, Sortino/Calmar, strategy ranking
and robustness/risk work are deferred. Historical metrics do not establish strategy
quality or persistent profitability. Phase 10 validation is implemented separately; later phases remain planned.

## 10. Out-of-sample, walk-forward, and robustness validation

**Implemented.** quantlab.validation provides explicit chronological half-open
bar-index holdouts, complete rolling/expanding walk-forward folds, independent
start-flat runs and deterministic approved parameter-default variant sensitivity.
Each window reuses Phase 7/8 backtesting and Phase 9 analytics with unchanged costs,
approvals and undefined metrics. Supplied causal features retain indicator history;
dependency availability is checked before slicing, and rule offsets/crossings are
segment-local. Fold/candidate summaries describe Decimal returns, signed drawdowns,
profit/loss counts and closed-trade totals without ranking or portfolio compounding.

Tests cover exact boundaries, hand-computed performance, next-open isolation,
no position carry/forced close, future independence, feature warm-up/availability,
costs, approval integrity, malformed copies, JSON, input immutability, hostile
Decimal contexts and offline replay. See [policies and limitations](research-validation.md).
No fitting, optimization, risk engine or later phase is added. Regime/session
analysis, candidate/tuning persistence and label-horizon purge/embargo are deferred
until their data/fitting contracts exist. Historical stability is not proof of
future profitability.

## 11. Deterministic risk engine

**Implemented V1.** quantlab.risk provides a pure entry evaluator and frozen strict
config/context/decisions with stable ALLOW/REJECT reasons. BacktestConfig.risk
defaults to no restrictions. Mandatory next-open pre-fill gates enforce maximum
quantity, unsigned reference notional, equity-fraction exposure, minimum equity and
causal observed drawdown. No rejected fill/cost is created; signals remain recorded,
exits proceed and open positions are never liquidated by risk. Results retain policy
and ordered decisions. Phase 10 propagates the policy with independent runtime
peaks. Exact Decimal integer-ratio comparisons protect hard boundaries and caller
context independence. See [risk-engine.md](risk-engine.md).

Fixed quantity is preserved because existing analytics requires it; resizing is
deferred. Capital-at-risk is deferred because stops remain unsupported. Portfolio,
leverage/margin, daily/session loss, cumulative realized-loss, stale-feed and atomic
multi-order budget controls are outside V1. Phase 12 research is implemented separately below.

## 12. ML research pipeline

**Implemented V1.** quantlab.ml provides strict immutable ordered feature schemas,
exact-time causal datasets, explicit bar-count forward-return targets, training-window
label purging, an exact-rational ridge regression baseline, auditable Decimal learned
parameters, deterministic model/data digests and OOS-only timestamped predictions.
The training information cutoff includes future-label publication. Predictions become
canonical model-bound ML_SIGNAL features consumed through approved strategies and
normal backtesting, execution costs, analytics and mandatory Phase 11 risk gates.
Phase 10 ValidationWindow is reused without rewriting validation. Tests cover causal
independence, target sensitivity, immutable replay/JSON, hostile Decimal context,
network isolation, exact coefficients and execution/risk integration.

No dependency was added. Scaling, training metrics, classification, automated fold
orchestration, tuning, boosting libraries, model selection, tracking services and live
learning are deferred. Phase 13 provider infrastructure is implemented separately below. See
[ml-research.md](ml-research.md) for the implemented scope and limitations.

## 13. LLM provider abstraction

Purpose: isolate provider-specific APIs from domain and quant code.

Implemented: frozen provider-neutral request/response, message, prompt provenance,
identity, capability, usage and invocation-policy contracts; an async provider
protocol and thin client with capability checks, response validation, bounded
transient retries and per-response usage limits; canonical JSON Schema transport
and strict Pydantic structured-output validation; deterministic scripted fake and
offline tests. Future image references are typed declarations only. No vendor SDK,
real provider calls, strategy/chart interpretation or orchestration is included.
Provider output remains untrusted and cannot calculate authoritative financial
metrics. See [llm-provider-abstraction.md](llm-provider-abstraction.md).

## 14. Natural-language strategy interpretation

Purpose: translate text research ideas into reviewable strategy drafts.

**Implemented.** `quantlab.interpretation` adds frozen bounded input, deterministic
prompt `quantlab.natural-language-strategy` version `1`, Phase 13 structured output,
READY/NEEDS_CLARIFICATION contracts, source-quote review evidence, deterministic
Phase 5 semantic validation and bounded feature vocabulary including existing
ML_SIGNAL declarations. READY creates only an unapproved DRAFT using existing
StrategySpecification and canonical digest contracts. Clarification creates no
proposal; no material assumptions are silently supplied. Full invocation artifacts
retain provider/model, input and prompt provenance. No quant engine, real vendor
adapter, automatic approval or network call is added. Offline FakeProvider tests
cover malformed output, unsupported intent, approval isolation, retries, deep
revalidation, catalogue parity and deterministic replay. See
[natural-language-strategy-interpretation.md](natural-language-strategy-interpretation.md).

Semantic fidelity still requires human review. Persistence, automatic clarification
loops and real model evaluation remain deferred. Phase 15 extends these contracts below; Phase 16 adds bounded human review below; Phase 17 adds bounded tools below; remaining Phase 18 work and Phases 19+ remain planned.

## 15. Chart image + text multimodal interpretation

Purpose: support single-image and multi-image research submissions.

**Implemented.** Strict immutable inputs accept 1–16 ordered opaque Phase 13 image
references and optional text. Prompt `quantlab.multimodal-strategy`, version `1`,
requires image and structured capabilities. Visual evidence and exact text quotes
bind proposed material fields to input; explicit text/image and image/image
conflicts require clarification and cannot yield READY. The existing Phase 14
draft/status semantics, Phase 5 conversion and supported vocabulary are reused.
Results retain input/order, exact request, raw response, provider/model identity
and an unapproved DRAFT or questions, with deterministic replay validation.
Screenshots are untrusted research inputs, never authoritative market data.
Offline FakeProvider tests cover malformed output, binding, conflicts, injection,
approval isolation, no market-data construction or quant-engine authority, no
network/vendor/OCR dependencies and state isolation. See
[multimodal-strategy-interpretation.md](multimodal-strategy-interpretation.md).

Real adapters (including Qwen3-VL/Ollama), upload/storage/privacy services, OCR,
chart-to-OHLC extraction and real-model quality evaluation remain deferred. No
automatic approval, optimization or chart trading is implemented.

## 16. LangGraph research/orchestration agents

Purpose: coordinate bounded research workflows around existing deterministic services.

**Implemented.** `quantlab.orchestration` adds the official LangGraph runtime,
strict immutable requests/snapshots, deterministic input-type branches calling
Phase 14/15 unchanged, terminal clarification and a real human-review interrupt.
Typed accept/reject/revise decisions bind caller-owned thread, strategy ID,
version and digest. Acceptance ends at `ACCEPTED_FOR_APPROVAL`, retaining the
original DRAFT and requiring separate Phase 5 validation/approval. No quant
engine or MCP tool executes, and there is no autonomous loop or automatic rewrite.

Fresh in-memory checkpointers, replay checks, duplicate-start/resume protection
and serialized handle calls support bounded offline workflows. Phase 13 still
owns provider calls, retries, limits and errors. Tracing flags are refused;
credentials are never read. Tests execute actual pause/resume cycles and deny
I/O, approval and quant authority. Verification: 106 Phase 16 tests, 658 Phase
13–16 tests, and 1,826 full-suite tests passed under Python 3.11.9 with LangGraph
1.2.12. See [agent-orchestration.md](agent-orchestration.md).

Durable persistence, authentication, distributed cancellation/recovery, real
provider adapters remain deferred. Phase 17 adds the bounded application handoff
below; remaining Phase 18 work and Phases 19+ remain planned.

## 17. MCP quant tools

Purpose: expose existing quant application services through typed agent tools.

**COMPLETE (implementation and external runtime verification passed).** The final
local stdio MCP 2.x surface has eighteen explicitly registered tools. The original
seven retain their behavior; seven additional Phase 10/12 adapters reuse public
chronological validation and causal deterministic ML services. Four operation
adapters supply process-local lifecycle, status, cancellation, idempotency and
immutable audit/provenance. The trusted application handoff connects retained
Phase 16 acceptance to a separately approved exact-version specification and a
fixed backtest -> performance sequence, without exposing human review as a tool.

The exact allowlist is:

1. `validate_strategy_content`
2. `validate_market_data`
3. `resample_market_data`
4. `compute_features`
5. `evaluate_entry_risk`
6. `analyze_performance`
7. `run_backtest`
8. `run_holdout`
9. `run_walk_forward`
10. `run_parameter_robustness`
11. `build_ml_dataset`
12. `train_ml_model`
13. `predict_ml_oos`
14. `ml_predictions_to_features`
15. `submit_research_operation`
16. `execute_research_operation`
17. `get_research_operation`
18. `cancel_research_operation`

The four operation tools admit only backtest, holdout, walk-forward, explicit
parameter robustness, ML dataset construction, ridge training, OOS prediction,
prediction-feature conversion and performance analysis. Submission returns a
queued identifier without executing work. Explicit execution transitions queued
-> running -> completed/failed; queued -> cancelled is the only cancellation
edge. Running synchronous services cannot be interrupted. Terminal identities,
results and audit chains are retained and cannot reopen. No worker, job callback,
background loop, database or distributed queue exists.

Idempotency binds a caller key to the kind, canonical validated inputs and any
trusted workflow context. Identical requests return the same current operation
and retained terminal result; different inputs/kinds/context conflict. Canonical
JSON uses sorted keys, expanded defaults, normalized exact Decimal spelling and
SHA-256. Audit records include kind, request/key digests, strategy ID/version/
content digest where relevant, legal transitions, UTC timestamps and terminal
result digest or fixed failure class. Audit contains no raw request, key, prompt,
image, exception text, credentials or filesystem paths. Results are immutable
canonical JSON text containing validated adapter output.

Each server owns an isolated namespace capped at 32 total admitted identities;
terminal identities are never evicted to make room or silently reuse keys. New
research requests have a 1 MiB serialized admission bound, at most 5,000 bars/
dataset rows/predictions, 100,000 supplied features, 32 ML columns, 16 robustness
candidates and 64 walk-forward folds where applicable. Decimal expansion is
limited to 4,096 digits for bounded canonicalization/rational inputs. Retained
operation results have a 4 MiB limit; exceeding that limit is an unexpected
terminal failure, never partial success. These are adapter resource limits,
not changes to mathematical contracts. Existing seven stateless tools are
unchanged. Stateless computations do not create operation metadata; callers
choose the tracked surface when they need lifecycle/idempotency/audit.

Phase 16 remains independently bounded and keeps accepted proposals DRAFT.
`get_workflow_snapshot` only reads validated retained state. The application-only
`run_reviewed_backtest` requires retained ACCEPTED_FOR_APPROVAL and a separately
Phase 5-approved specification matching thread, strategy ID, version and digest.
It executes at most two services, stops after a failed backtest, hashes thread
context into audit and preserves clarification/rejection/revision paths. No
MCP transport, provider invocation, human resume or approval transition is added
to the graph. Canonical records are contract checks, not authentication; this
local process and its graph/checkpointer/application handles remain trusted.

All wire inputs remain strict finite JSON, canonical strict JSON-mode contracts,
with no coercion/repair or executable dependency injection. Expected service
input errors are fixed sanitized issues; unexpected failures and invalid outputs
propagate to the SDK's sanitized tool error. Quantitative Python still owns
approval, leakage/cutoffs, rules, risk, costs, fills, P&L and research reports.
No paper/account/portfolio/ledger state, arbitrary filesystem/database/network,
code execution, ranking, live trading or autonomous agent loop is exposed.

The committed baseline was externally verified with Python 3.11/MCP 2.3.0:
390 MCP tests and 2,216 full-suite tests. Those counts are not verification of
this final pass. Windows runtime attempts are blocked by a missing venv Python;
stdlib AST syntax and architecture checks pass. Final expanded MCP + orchestration and full-suite runtime verification passed in WSL Python 3.11.17: 753 targeted tests and 2473 full-suite tests. Phase 18A is documented below; remaining Phase 18 work is planned.
Durability, authentication, distributed recovery and HTTP deployment are deferred.

## 18. Paper-trading engine

Purpose: simulate ongoing execution for approved, validated strategies.

**Phase 18A implemented.** `quantlab.paper` is an offline, synchronous single-entry market-order kernel with strict immutable contracts, deterministic logical sequence/identities/causation, quote delivery/availability checks, acceptance and pre-fill Phase 11 risk, shared Decimal pricing, cancellation and atomic idempotency. It owns no strategy admission, account ledger, persistence, recovery or transport. Existing backtest timing and MCP/agent contracts are preserved. See [paper-order-kernel.md](paper-order-kernel.md).

**Phase 18B implemented.** Separate deterministic account ownership, strict immutable financial contracts, prefunded linear EQUITY P&L/collateral semantics, aggregate fund reservations, exact Decimal settlement, long/short reductions and closures, causal bid/ask valuation, reconciled snapshots and atomic idempotency are implemented. The narrow owned-kernel adapter preserves Phase 18A entry-only matching; pure accounting exits are not executable orders. See [paper-accounting.md](paper-accounting.md).

Phases 18A through 18F are implemented for the declared bounded offline scope.
The milestones below describe that scope; continuous external execution, live
brokers and broader portfolio capabilities remain deferred.

- **18C implemented:** exact-version formal strategy approval plus an explicit versioned eligibility policy and separately recorded independent research verification; bounded causal tri-state raw-feature runtime; one attributed entry intent; atomic account-owned acceptance/reservation and explicit on-time adjacent locked-opening execution. No automatic approval, live/batch feature overrides or MCP trading authority. See [paper-strategy-runtime.md](paper-strategy-runtime.md).
- **18D implemented:** bounded deterministic offline replay, typed recorded-event envelopes, monotonic logical clock, explicit feed interruption/resumption/exhaustion and versioned stale-data gating; strict idempotent session lifecycle and non-liquidating stop with account-owned pending cancellation; isolated replay verification and coordinated session/financial/opening/audit publication. Historical datasets retain their opening-evidence limitations. See [paper-market-replay.md](paper-market-replay.md) and [phase18d-report.md](phase18d-report.md).
- **18E implemented:** one serialized local SQLite persistence authority, versioned canonical input/output journaling, atomic durable publication, bounded checkpoint accelerators, integrity verification and deterministic new-owner recovery; exact retries and explicit recovery for unknown/post-commit failures preserve financial ownership. Recovered ACTIVE sessions require recorded pause/resume. See [paper-session-persistence.md](paper-session-persistence.md) and [phase18e-report.md](phase18e-report.md).
- **18F supported bounded scope implemented:** explicit v2 quote-side market/limit/stop-market/stop-limit matching, GTC/IOC, partial-fill budgets, exact continuation accounting, sequenced cancellation and position-linked reduce-only protective exits with durable recovery. V1 approval timing and journals are preserved. Explicit v3 two-child protective OCO is supported with atomic sibling reconciliation; durable advanced strategy entries require separately reviewed v2 execution approval/eligibility and explicit v3 session policy, with actual next-opening provenance before later quote matching; retained-history copying is remediated with append-only history, bounded heads and incremental indexes. See [paper-advanced-orders.md](paper-advanced-orders.md) and [phase18f-report.md](phase18f-report.md).

**Phase 18B consistency hardening implemented.** Account-owned kernels now prepare execution and accounting before one shared in-memory publication. Price gaps outside prefunded capacity cancel without committing a fill and release the hold atomically. Unexpected transaction failures leave account/order snapshots, records and indexes unchanged. Standalone Phase 18A semantics are preserved; terminal settlement/release adapters acknowledge committed financial events. Multi-position allocation, aggregate risk, FX, margin, borrowing and concurrent transactions are deferred. Phase 18E adds opt-in durable transactions for the supported session.

Deliverables: declared validation/eligibility policy, feed/clock integration, mandatory risk gates, simulated execution costs, order/state persistence, replay/recovery, stale-feed behavior, and duplicate-event handling. Define account-state/risk interfaces now; the multi-strategy portfolio expands them in phase 19. No real-money broker integration.

## 19. Portfolio system

Purpose: manage multiple strategies/instruments as one risk-aware account.

Deliverables: allocation and attribution, consolidated cash/positions, timestamped currency valuations, aggregate exposures, portfolio analytics, and coordinated risk decisions. Test competing strategies sharing capital and risk budgets.

**Bounded foundation implemented.** `quantlab.portfolio` provides a separate
serialized in-memory reporting owner for up to 32 independently owned Phase 18
paper sessions. Static membership reserves exact starting capital and explicit
encumbrance/gross-exposure budgets from a declared portfolio total. Exact strategy
version/content approval, research admission, session/account and instrument
provenance remain visible. Ordered session-record ingestion, explicit timestamped
same-currency reporting quotes, liquidation equity/P&L and unsigned exposure
aggregation use immutable contracts and Phase 18 exact Decimal accounting.
Missing/stale valuations suppress current financial metrics; committed financial
facts and budget breaches remain visible. Retry-safe publication and verified
caller-retained journal replay are supported without portfolio durability or
cross-session execution. See [portfolio-management.md](portfolio-management.md)
and [phase19-report.md](phase19-report.md).

The roadmap's unified-account purpose is implemented as consolidated views and
competition for static ownership budgets. Execution continues in independent
bounded accounts: no shared collateral, cross-account netting, capital transfers,
portfolio trade permissions or atomic cross-session fills are introduced. Currency
conversion, dynamic allocation (the later Phase 29 capability referenced in the
Phase 19 request), portfolio return-series analytics and persistent portfolio
coordination remain deferred. The checked-in roadmap currently enumerates only
Phases 1–25; this increment does not invent or implement Phase 29.

## 20. Trading journal

Purpose: connect decisions and outcomes into an auditable research/trading record.

Deliverables: linked strategies, approvals, runs, simulated fills, risk decisions, and human notes. Preserve immutable execution/audit records while allowing annotation updates. Backfill/link existing event artifacts without rewriting financial history.

**Bounded scope implemented.** `quantlab.journal` imports retained Phase 18
session records, Phase 17 research operation snapshots and independently verified
research evidence. Strategy/version/content approval, admission, session/account/
instrument, decisions, actual fills, partial quantities, fees, position context,
OCO and causal sources remain linked. Account-derived realized summaries keep
incomplete positions and unrealized marks separate from closed outcomes. Existing
research results retain their original provenance where present; no inference or
historical computation is rerun.

A separate local SQLite journal atomically appends content-addressed imports and
note revisions with rebuildable query indexes. Exact retries are idempotent;
conflicting identities/audit prefixes fail. Schema versions, complete restart
verification, fresh-store replay and fail-closed commit recovery are supported.
Typed queries offer identity filters, half-open UTC ranges and bounded keyset
pagination. Paper storage, operator gates and portfolio ownership remain unchanged;
transactions do not span those stores. See [phase20-report.md](phase20-report.md).

Automatic capture, missing original request retention, new research workflows,
arbitrary standalone accounting imports, snapshot pagination, authentication,
distributed storage, dashboard and live brokerage remain deferred. Phase 21 below
supplies authenticated read-only HTTP queries; journal mutation APIs remain deferred.

## 21. FastAPI application/API

Purpose: expose the mature core through a stable backend contract.

Deliverables: API schemas/routes, application-service wiring, job/status interfaces, configuration, structured logging, and authentication/authorization appropriate to deployment. Introduce PostgreSQL/migrations if now justified by persistence requirements. Verify that API paths share approval and risk gates with MCP/direct services.

**Bounded scope implemented.** The local `quantlab.api` factory exposes authenticated
versioned strategy validation, existing Phase 17 deterministic operation admission,
explicit execution/cancellation/status/typed results, and read-only injected Phase 18
paper, Phase 19 portfolio and Phase 20 journal reports. A dedicated bounded service
thread keeps quant/SQLite work off the event loop and preserves owner lifetimes.
Reader/operator bearer roles, fail-closed configuration, loopback startup, strict
JSON/byte/page/output bounds, restricted CORS, sanitized errors and structured
correlations are implemented. Existing exact approval, risk, causality, idempotency,
audit and recovery semantics are preserved. Offline ASGI tests and representative
latency observations are recorded in [phase21-report.md](phase21-report.md).

No background/durable job worker, multi-user production security, paper commands,
HTTP approval/recovery, journal writes, live brokerage, dashboard or PostgreSQL
migration is introduced. Phase 22 and later phases remain planned.

## 22. Professional dashboard/frontend

Purpose: make research, review, and paper-trading workflows usable.

Deliverables: strategy authoring and exact-version approval, image/text uploads, run/validation comparisons, risk visibility, portfolio views, and journal workflows. Select the frontend framework at this phase. Display backend metrics with provenance and clear historical/simulated labels.

## 23. Integration/end-to-end testing

Purpose: verify cross-layer behavior beyond phase-local tests.

Deliverables: reproducible manual/text/image-to-approval-to-backtest flows; validation/risk-to-paper-to-portfolio/journal flows; error/recovery/cancellation coverage; and regression fixtures. Prove approval invalidation, leakage guards, and risk denial across API and agent/MCP routes.

## 24. Docker, CI, and deployment documentation

Purpose: make builds, checks, and development deployment repeatable.

Deliverables: minimal container configuration, CI for packaging/tests, documented configuration/secrets and database setup, health checks, backup/recovery guidance, and deployment instructions. Deployment remains a separate action; automation must not activate live trading or publish private datasets.

## 25. Final GitHub documentation and demo assets

Purpose: present an accurate, reproducible public portfolio project.

Deliverables: refreshed capability/status documentation, clean setup instructions, suitable demo datasets, sample approved strategies, screenshots/video, architecture updates, known limitations, licensing decisions, and contribution guidance. Verify demos against implemented behavior and inspect assets for credentials/private or unlicensed data. GitHub publication is separate from this foundation step.

## Sequencing and future scope

Approval contracts precede backtesting; execution costs precede realistic performance claims; validation and deterministic risk precede paper trading. Agent workflows depend on existing services before MCP packaging. The roadmap delays full portfolio support while ensuring earlier risk/paper interfaces already account for shared account state.

API/dashboard selection follows stable core contracts. Meaningful tests and documentation accompany every phase; later integration/CI work broadens those checks. Provider, storage, validation-threshold, and frontend decisions are resolved at their relevant phases.

V2 self-evolving/automated alpha research is outside this roadmap. Existing provenance, strategy versions, experiment artifacts, and bounded tool contracts are extension points only. Implementing V2 requires a separate design and explicit project scope.

**Phase 18C scope boundary.** The runtime is an offline single-entry capability,
not a continuous paper-trading service. Evidence and logical observations must be
produced and verified before trusted application delivery. MID/raw OHLC evaluation
and real locked adjacent opening quotes are supported; batch indicators, ML
delivery, gapped/unlocked opening execution, exits/reversals/pyramiding, other
accounting configurations and multi-strategy execution remain unsupported.
The next phase must supply genuine causal feed/opening and session/calendar
contracts rather than treating a later quote or complete OHLC as a next opening.
