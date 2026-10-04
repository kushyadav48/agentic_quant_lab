# Agentic Quant Research & Trading Lab

An AI-assisted, multi-market quantitative research and paper-trading platform, designed to turn human research ideas into explicit, reviewable strategies and evaluate them using deterministic Python calculations.

**Status: early development — Phases 1–15 implemented.** The repository contains the documentation/package foundation, strict market-data domain contracts, and a Dukascopy historical tick-ingestion adapter producing canonical bid/ask quotes for EUR/USD and USD/JPY. Tests use synthetic payloads and mocked HTTP. Dataset quality reports, fixed-UTC causal OHLC aggregation/resampling, and immutable local SQLite storage are implemented. Immutable strategy specification contracts, declarative rules and exact-version approval are implemented. A causal Decimal feature engine with explicit availability and structural strategy-reference checks is implemented. An approved-strategy deterministic bar backtester now implements causal bar-close signals, next-open fills, one-position long/short lifecycles, and Decimal research equity. Deterministic configurable spread, adverse slippage, per-unit commission and per-fill fees extend that execution boundary with auditable reference/execution prices and gross/net P&L. A separate deterministic performance analytics layer consumes completed BacktestResult records to report net trade statistics, account returns, population return volatility, drawdown episodes, holding durations and explicitly configured Sharpe ratios. A separate Phase 10 validation layer adds independent chronological holdouts, rolling/expanding walk-forward folds and explicitly approved parameter-variant sensitivity reports. Phase 11 adds mandatory deterministic ALLOW/REJECT entry controls with configurable quantity, reference-notional, equity-fraction, minimum-equity and causal drawdown limits plus a frozen audit trail. Phase 12 adds offline, window-bounded supervised datasets, deterministic ridge regression artifacts and strictly OOS model predictions as canonical strategy features. Phase 13 adds provider-neutral async LLM contracts, strict structured output, bounded invocation policies and an offline scripted provider. Phase 14 adds natural-language interpretation into unapproved strategy proposals or explicit clarification questions, with strict domain validation and invocation provenance. Phase 15 adds chart + text interpretation with ordered opaque image references, visual evidence, explicit conflicts and unapproved proposals through the same strategy contracts. Real provider adapters, image upload/storage, agents, APIs, and dashboard functionality remain planned. Successful live downloading has not been verified; a manual probe received HTTP 429.

## Planned capabilities

- Define strategies through natural language or structured/manual rules, with a shared StrategySpecification representation.
- Interpret one or multiple chart images with accompanying text using a multimodal LLM, then require user review and approval before testing or paper trading.
- Backtest historical data with explicit spread, slippage, fees, execution timing, and quantitative performance analytics.
- Assess out-of-sample performance, walk-forward results, parameter sensitivity, market regimes, and trading sessions.
- Build reproducible ML research pipelines and LangGraph research agents that access deterministic quant operations through MCP tools.
- Enforce deterministic risk controls, paper trade validated strategies, manage multiple strategies and instruments, and maintain a trading journal.
- Provide a professional research dashboard backed by a separate API.

Forex will be the first deeply implemented market. Shared domain abstractions will allow equities and crypto support through market-specific adapters and policies.

## High-level architecture

Manual rules, natural-language inputs, and chart-plus-text inputs will converge on the same versioned strategy specification. Human-approved specifications will feed deterministic feature, backtesting, validation, risk, and portfolio engines. Agents will orchestrate bounded tool calls and explain recorded results; they will not calculate authoritative financial values or bypass approvals and risk checks.

A future FastAPI backend will expose these capabilities to a separate web dashboard. Storage adapters will persist data and research artifacts with provenance. Paper trading will use simulated execution behind deterministic risk checks. Live brokerage execution and automated V2 alpha evolution are outside the current scope.

See [the architecture source of truth](docs/architecture.md) for layer responsibilities, dependencies, safety boundaries, and extension points.

## Technology direction

| Area | Direction | Current state |
| --- | --- | --- |
| Core | Python 3.11+, Pydantic for typed domain models | Strict Pydantic market-data contracts implemented |
| Quant/data | NumPy, Pandas and/or Polars, SciPy; Statsmodels where useful | Canonical model sequences for Phase 4; dataframe adoption deferred |
| ML | Offline baseline and auditable research artifacts | Phase 12 exact-rational ridge regression; broader ML tooling deferred |
| AI | Provider-neutral LLM boundary | Phase 13 provider infrastructure, Phase 14 text and Phase 15 chart + text interpretation implemented; real adapters, LangGraph and MCP planned |
| Backend/storage | FastAPI, PostgreSQL, storage adapters | Local SQLite dataset adapter implemented; FastAPI/PostgreSQL planned |
| Frontend | Separate professional web dashboard, likely React/Next.js | Planned; final framework not selected |
| Engineering | pytest, Git, structured logging; Docker and CI later | pytest development extra and existing Git metadata |

Pydantic is the sole runtime dependency at this stage. Historical ingestion uses urllib, lzma, struct, and Decimal from the standard library. Phase 4 validation/resampling/storage also use the standard library plus existing Pydantic models; SQLite requires no new dependency. Phase 12 ridge fitting uses standard-library Fraction and Decimal, requiring no ML dependency or downloads. pytest is available through the development extra. Further dependencies will be introduced only when implemented functionality needs them.

## Repository structure

```text
agentic_quant_lab/
├── README.md
├── .gitignore
├── .env.example
├── pyproject.toml
├── docs/
│   ├── architecture.md
│   ├── llm-provider-abstraction.md
│   ├── natural-language-strategy-interpretation.md
│   ├── multimodal-strategy-interpretation.md
│   └── roadmap.md
├── src/
│   └── quantlab/
│       ├── __init__.py
│       ├── llm/  # Phase 13 contracts, async client and fake
│       ├── interpretation/  # Phase 14 text and Phase 15 chart + text proposals
│       └── data/
│           ├── __init__.py
│           ├── enums.py
│           ├── models.py
│           ├── validation.py
│           ├── resampling.py
│           ├── storage.py
│           └── providers/
│               ├── __init__.py
│               ├── base.py
│               └── dukascopy.py
└── tests/
    ├── __init__.py
    ├── llm/
    └── data/
        ├── __init__.py
        ├── test_models.py
        ├── test_pipeline.py
        └── providers/
            ├── __init__.py
            └── test_dukascopy.py
```

The implemented market-data contracts are exported from `quantlab.data`. The provider-neutral historical quote interface lives in `quantlab.data.providers`; Dukascopy-specific code remains in its adapter module. Other future layers remain architectural concepts, not empty modules in the current tree. Local data, uploads, generated artifacts, databases, logs, and secrets belong outside version control.

## Development setup

Use Python 3.11 or newer. From the repository root:

```sh
python -m venv .venv
# Activate .venv using the command appropriate for your shell.
python -m pip install -e ".[dev]"
python -c "import quantlab"
```

For PowerShell, activation is `.venv\Scripts\Activate.ps1`; for POSIX shells, use `source .venv/bin/activate`.

Run `python -m pytest` for domain, offline ingestion, quality, resampling, local storage, and strategy contract tests. These cover Forex conventions, strict field types, UTC normalization, OHLC/quote bounds, volume units, immutability, JSON round trips, binary decoding/scaling, request boundaries, provenance, absent data, and transport/corruption errors. No test requires live HTTP access. The import check above verifies the installed package, not any trading functionality.

The `.env.example` file contains guidance only: no environment configuration is consumed yet and no credentials are required.

## Historical quote ingestion

Import `HistoricalQuoteRequest` from `quantlab.data.providers` and `DukascopyProvider` from `quantlab.data.providers.dukascopy`. Construct an Instrument with the pair's explicit price metadata, then iterate `provider.fetch(request)`. Requests accept aware start/end datetimes and use an inclusive start and exclusive end. Fetching requires network access and happens only when the iterator is consumed.

The initial registry supports EUR/USD and USD/JPY. It selects the public hourly `.bi5` archive, decodes LZMA-Alone binary ticks, and returns existing MarketQuote objects without bar aggregation or storage. Additional providers can implement the same quote protocol. See [the implemented ingestion boundary](docs/architecture.md#implemented-phase-3-ingestion) for exact format assumptions, the source-event availability policy, limits, and error handling.

Downloaded data remains subject to Dukascopy's applicable terms/licensing; public access does not automatically permit redistribution. No real historical dataset is included in this repository.

## Validated research datasets

Import DataQualityReport, ValidationOptions, validate_dataset,
normalize_observations, ResampleRequest, MissingDataPolicy, resample,
DatasetMetadata, and SQLiteDatasetStore from quantlab.data. Validate collected
quotes first, aggregate whole UTC intervals with an explicit missing-data policy,
and persist raw and derived datasets with provenance. For example, with an
existing instrument and collected provider quotes:

```python
from quantlab.data import (
    DatasetMetadata, MissingDataPolicy, PriceType, ResampleRequest,
    SQLiteDatasetStore, Timeframe, resample, validate_dataset,
)

quotes = tuple(quotes)  # exhaust ingestion before validation/persistence
report = validate_dataset(quotes, instrument=instrument)
if not report.valid or report.observation_count == 0:
    raise ValueError(report.issues)
request = ResampleRequest(
    instrument=instrument, timeframe=Timeframe.M1, price_type=PriceType.BID,
    start_time=start, end_time=end,  # aware, aligned UTC minute endpoints
    missing_policy=MissingDataPolicy.OMIT,
)
bars = resample(quotes, request)
store = SQLiteDatasetStore("data/research.sqlite")  # create data/ first
raw_id = store.save(quotes, DatasetMetadata(
    instrument=instrument, description="Historical quotes",
    licensing="Record applicable provider terms here", transformation="identity-v1",
))
bar_id = store.save(bars, DatasetMetadata(
    instrument=instrument, description="UTC minute bid bars",
    licensing="Record applicable provider terms here", parent_ids=(raw_id,),
    transformation="utc-ohlc:" + request.model_dump_json(), transformation_version="2",
))
loaded = store.load(bar_id)
```

Reports distinguish exact duplicates, repeated timestamps, ordering errors,
source/dataset consistency, and empty datasets. Quote gap detection uses an
optional positive ValidationOptions.max_gap threshold; no fixed tick cadence is
assumed. A separate caller-supplied expected-start schedule can check missing
bars/session coverage. normalize_observations provides opt-in stable sorting
with explicit keep/reject/remove policies for exact duplicates.

Missing quote bins and incomplete bar groups are omitted or rejected, never
filled. availability equals max(interval end, all member available_at values).
Quote-derived volume and volume_type remain None. Daily/weekly aggregation uses
fixed UTC windows, not exchange/session calendars. SQLite is portable and uses
only the standard library, with atomic writes of metadata, records, and reports.
Storage rejects quality errors, preserves provider provenance, and verifies
content identities on read. Metadata includes source/type, bounds/count,
creation time, transformation version and parents; creation time is excluded
from the content hash so identical saves retain the same identity.
See [Phase 4 contracts and limitations](docs/architecture.md#implemented-phase-4-datasets).

## Strategy specifications

Import immutable strategy contracts from `quantlab.strategies`. All authoring routes share the same typed content, bounded declarative rules, parameters, provenance, safe timing intent, and version-bound approval contract. Feature computation and approved-strategy research backtesting are available separately. See [the strategy contract and manual approval example](docs/strategy-spec.md) for digest policy, lifecycle, validation and limitations.

## Feature computation

Import FeatureRequest, FeatureParameter and compute_features from quantlab.features.
The engine computes raw OHLC, simple/log returns, SMA, EMA, Wilder RSI and population
rolling volatility from validated bars, with omitted warm-up values and causal
availability. It uses Decimal and adds no dependencies. See [formulas, usage and
strategy compatibility](docs/feature-engine.md). Feature computation does not execute strategies.

## Deterministic research backtesting

Import BacktestConfig, ExecutionCostConfig and run_backtest from quantlab.backtesting. The engine
requires exact-version APPROVED strategy content and validated canonical bars,
consumes supplied Phase 6 features, and evaluates rules at bar close with fills
at the following input bar open. Frozen results retain signals, fills, closed
trades, any final open position, and Decimal research equity. Fixed quantity and
zero default costs preserve the Phase 7 baseline. Optional immutable execution_costs
configure price-basis-aware spread, adverse fixed slippage, per-unit commission and
per-fill fees. Entry cash costs affect realized account P&L immediately; spread and
slippage affect execution prices. TRADE bars reject nonzero synthetic spread.
Stop/target, session and external sizing intent are rejected. See
[contracts, timing and examples](docs/backtesting-engine.md) and
[execution formulas and accounting](docs/execution-cost-model.md).

## Deterministic entry risk

BacktestConfig.risk nests a strict frozen RiskConfig from quantlab.risk. All limits
default to None, preserving previous economics. Every next-open entry receives an
audited ALLOW/REJECT decision before fill pricing or costs. Quantity remains fixed;
exits always follow existing execution rules, and risk never invents liquidation.
BacktestResult retains the policy and ordered risk_decisions. Holdout, walk-forward
and robustness runs automatically reuse the supplied policy with independent state.
See [exact limits, causal peak policy and deferrals](docs/risk-engine.md).

## Performance analytics

Import analyze_performance and AnalyticsConfig from quantlab.analytics and call
`report = analyze_performance(result)` on a completed BacktestResult. Frozen reports
separate closed-trade net P&L from total ending equity, including any open position.
Returns include the initial-capital-to-first-mark observation; drawdowns include
initial capital as the first peak. Undefined ratios are None. Population volatility
and nonannualized Sharpe use isolated 34-digit Decimal arithmetic; annualization
requires an explicit caller factor. No execution, strategy evaluation or robustness
assessment is performed. See [formulas, policies and limitations](docs/performance-analytics.md).

## Research validation

Import run_holdout, run_walk_forward and run_parameter_robustness from
quantlab.validation. Explicit half-open bar-index windows keep in-sample and OOS
runs separate; each starts flat and cannot fill beyond its segment. Supplied causal
features may retain historical indicator warm-up, while rule offsets/crossings restart
within each segment. Descriptive summaries use Decimal and reuse Phase 9 analytics
with supplied Phase 8 costs. Parameter candidates require their own exact-version
approvals; there is no selection, ranking or optimization. See
[window, warm-up, approval and replay policies](docs/research-validation.md).

## Offline ML research

Import build_dataset, train_model, predict_oos, prediction_feature_reference and
predictions_to_features from quantlab.ml. Explicit ordered feature schemas join
canonical observations at exact decision timestamps. Window-local forward-return
labels train a small deterministic ridge baseline with auditable immutable parameters
and SHA-256 provenance. OOS decisions must follow the complete training information
cutoff, including future label availability. Model-bound ML_SIGNAL declarations flow
through normal strategy approval, backtesting, costs, analytics and mandatory risk.
Phase 10 bar-index windows are reused; no fitting/selection orchestration is added.
See [dataset, numerical, cutoff and integration policies](docs/ml-research.md).

## LLM provider boundary

`quantlab.llm` supplies strict immutable text/future-image contracts, explicit
provider/model capabilities and prompt provenance, an async provider protocol,
strict Pydantic structured output, bounded retries and per-invocation resource
policies. Its scripted fake supports entirely offline testing. LLM output is
untrusted and never creates fills, risk decisions, financial metrics or account
state. No vendor SDK or credential configuration is required. See
[the Phase 13 contracts, example and limitations](docs/llm-provider-abstraction.md).

## Natural-language strategy interpretation

`quantlab.interpretation` uses Phase 13 structured output to produce either a
reviewable Phase 5 DRAFT `StrategySpecification` or explicit clarification questions
with no proposal. Deterministic validation checks domain semantics, supported
feature declarations and source-quote coverage. Identity/provenance belong to the
application; approval remains a separate human action. No quant engine or vendor
adapter is added. Future local Ollama/Qwen-class and cloud
adapters use the same neutral interface. See [the Phase 14 contracts, vocabulary,
prompt version and limitations](docs/natural-language-strategy-interpretation.md).

## Chart + text strategy interpretation

Phase 15 accepts 1–16 ordered opaque `ImageReference` objects and optional text.
Its versioned `quantlab.multimodal-strategy` prompt uses Phase 13 structured output;
visual/text evidence and reported conflicts are checked before reusing Phase 14's
draft conversion and vocabulary. READY remains an unapproved DRAFT; incomplete or
conflicting intent requires questions with no proposal. Screenshots never become
authoritative price history or execution/risk inputs. Full artifacts support replay
consistency checks. Tests use only the offline FakeProvider; future local
Qwen3-VL/Ollama adapters can use the same provider interface. Image transport,
storage and OCR are not implemented. See [Phase 15 contracts and limitations](docs/multimodal-strategy-interpretation.md).

## Roadmap summary

1. Establish the foundation, data domain, Forex ingestion, validation, resampling, and storage.
2. Implement strategy specifications, features, backtesting, execution costs, analytics, validation, deterministic risk, and offline ML research.
3. Add LLM abstraction, natural-language and multimodal interpretation, agents, and MCP tools.
4. Add paper trading, portfolio management, journaling, the API, and dashboard.
5. Complete integration testing, deployment tooling, and public documentation/demo assets.

[The detailed roadmap](docs/roadmap.md) defines 25 phases with deliverables and acceptance expectations. V2 self-evolving research remains a future extension, not current implementation work.

## Development philosophy

Build small, inspectable increments in dependency order. Keep domain contracts explicit, calculations deterministic, research reproducible, and execution assumptions visible. Add meaningful tests with each implemented behavior and track provenance for data, strategy versions, runs, and artifacts. Document uncertain choices before committing to infrastructure. Avoid a speculative directory tree and premature dependency installation.

Architecture changes should update `docs/architecture.md`; completed milestones should update the roadmap and this status statement. Do not describe planned capabilities as shipped features.

## Responsible use and limitations

This is a research/engineering platform, not a promise of trading profitability or investment advice. Historical performance does not guarantee future results. Data quality, selection bias, overfitting, changing market conditions, and execution assumptions can invalidate apparent results. Paper-trading outcomes do not establish live-trading performance. Any future financial output must be attributable to deterministic code, documented inputs, and explicit assumptions.
