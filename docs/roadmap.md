# Roadmap

**Status: early development.** Phases 1–12 are implemented: the documentation/package foundation, strict market-data domain contracts, and the Dukascopy historical tick-to-quote adapter with offline tests, followed by dataset quality reports, causal UTC resampling, and immutable local SQLite storage. Successful live downloading remains unverified after an HTTP 429 probe. Phase 5 adds immutable strategy contracts, semantic validation and exact-version approval with offline tests. Phase 6 adds causal Decimal features and structural strategy-reference compatibility. Phase 7 adds approved-strategy deterministic bar replay with zero-cost next-open fills and Decimal research equity. Phase 8 adds deterministic spread/slippage/commissions/fees and gross/net accounting. Phase 9 adds separate deterministic net trade/account-return, volatility, drawdown, holding-duration and Sharpe analytics. Phase 10 adds independent chronological holdouts, rolling/expanding walk-forward folds and explicitly approved parameter-variant sensitivity. Phase 11 adds mandatory deterministic fixed-quantity entry limits, causal drawdown gating and audited decisions. Phase 12 adds leakage-safe offline supervised datasets, deterministic ridge artifacts and OOS predictions integrated as canonical ML features. Phases 13–25 remain planned. The phase order follows dependencies, and later phases must not be treated as available features.

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
learning are deferred. No Phase 13 provider work is included. See
[ml-research.md](ml-research.md) for the implemented scope and limitations.

## 13. LLM provider abstraction

Purpose: isolate provider-specific APIs from domain and quant code.

Deliverables: text/vision-capable adapter contracts, configuration/secrets boundaries, structured output validation, prompt/model provenance, retries/budgets, and fake-provider tests. Provider integration cannot calculate authoritative financial metrics.

## 14. Natural-language strategy interpretation

Purpose: translate text research ideas into reviewable strategy drafts.

Deliverables: structured proposal output, assumptions/ambiguity reporting, validation against StrategySpecification, interpretation provenance, and explicit human approval before backtesting or paper trading. Test unsupported rules, missing information, and edits invalidating approval.

## 15. Chart image + text multimodal interpretation

Purpose: support single-image and multi-image research submissions.

Deliverables: validated image/text bundles, per-image explanations, ordering/context metadata, multimodal interpretation, uncertainty and rule-to-input traceability, and a reviewable interpretation record. Require human approval of the exact resulting strategy version; test untrusted uploaded instructions and unresolved chart context.

## 16. LangGraph research/orchestration agents

Purpose: coordinate bounded research workflows around existing deterministic services.

Deliverables: graph state, checkpoints, artifact references, cancellation/retry/budget policies, and human review nodes. Use service-facing tool interfaces initially; test that agents cannot grant approval or override risk. No autonomous strategy promotion or V2 evolution loops.

## 17. MCP quant tools

Purpose: expose existing quant application services through typed agent tools.

Deliverables: schema-validated bounded tool operations, approval/authorization enforcement, run status/cancellation, provenance-bearing results, audit events, and idempotency. Connect agents to MCP adapters without moving calculations into tool/LLM code; reject unrestricted code/database execution.

## 18. Paper-trading engine

Purpose: simulate ongoing execution for approved, validated strategies.

Deliverables: declared validation/eligibility policy, feed/clock integration, mandatory risk gates, simulated execution costs, order/state persistence, replay/recovery, stale-feed behavior, and duplicate-event handling. Define account-state/risk interfaces now; the multi-strategy portfolio expands them in phase 19. No real-money broker integration.

## 19. Portfolio system

Purpose: manage multiple strategies/instruments as one risk-aware account.

Deliverables: allocation and attribution, consolidated cash/positions, timestamped currency valuations, aggregate exposures, portfolio analytics, and coordinated risk decisions. Test competing strategies sharing capital and risk budgets.

## 20. Trading journal

Purpose: connect decisions and outcomes into an auditable research/trading record.

Deliverables: linked strategies, approvals, runs, simulated fills, risk decisions, and human notes. Preserve immutable execution/audit records while allowing annotation updates. Backfill/link existing event artifacts without rewriting financial history.

## 21. FastAPI application/API

Purpose: expose the mature core through a stable backend contract.

Deliverables: API schemas/routes, application-service wiring, job/status interfaces, configuration, structured logging, and authentication/authorization appropriate to deployment. Introduce PostgreSQL/migrations if now justified by persistence requirements. Verify that API paths share approval and risk gates with MCP/direct services.

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
