# Agentic Quant Research & Trading Lab

An AI-assisted, multi-market quantitative research and paper-trading platform, designed to turn human research ideas into explicit, reviewable strategies and evaluate them using deterministic Python calculations.

**Status: early development — Phases 1–4 implemented.** The repository contains the documentation/package foundation, strict market-data domain contracts, and a Dukascopy historical tick-ingestion adapter producing canonical bid/ask quotes for EUR/USD and USD/JPY. Tests use synthetic payloads and mocked HTTP. Dataset quality reports, fixed-UTC causal OHLC aggregation/resampling, and immutable local SQLite storage are implemented. Strategies, backtesting, financial metrics, AI integration, agents, risk, APIs, and dashboard functionality remain planned. Successful live downloading has not been verified; a manual probe received HTTP 429.

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
| ML | scikit-learn, LightGBM, XGBoost, Optuna, experiment tracking | Planned |
| AI | Provider-agnostic text/vision LLM adapters, LangGraph, MCP | Planned |
| Backend/storage | FastAPI, PostgreSQL, storage adapters | Local SQLite dataset adapter implemented; FastAPI/PostgreSQL planned |
| Frontend | Separate professional web dashboard, likely React/Next.js | Planned; final framework not selected |
| Engineering | pytest, Git, structured logging; Docker and CI later | pytest development extra and existing Git metadata |

Pydantic is the sole runtime dependency at this stage. Historical ingestion uses urllib, lzma, struct, and Decimal from the standard library. Phase 4 validation/resampling/storage also use the standard library plus existing Pydantic models; SQLite requires no new dependency. pytest is available through the development extra. Further dependencies will be introduced only when implemented functionality needs them.

## Repository structure

```text
agentic_quant_lab/
├── README.md
├── .gitignore
├── .env.example
├── pyproject.toml
├── docs/
│   ├── architecture.md
│   └── roadmap.md
├── src/
│   └── quantlab/
│       ├── __init__.py
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

Run `python -m pytest` for domain, offline ingestion, quality, resampling, and local storage tests. These cover Forex conventions, strict field types, UTC normalization, OHLC/quote bounds, volume units, immutability, JSON round trips, binary decoding/scaling, request boundaries, provenance, absent data, and transport/corruption errors. No test requires live HTTP access. The import check above verifies the installed package, not any trading functionality.

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

## Roadmap summary

1. Establish the foundation, data domain, Forex ingestion, validation, resampling, and storage.
2. Implement strategy specifications, features, backtesting, execution costs, analytics, validation, and deterministic risk.
3. Add ML research, LLM abstraction, natural-language and multimodal interpretation, agents, and MCP tools.
4. Add paper trading, portfolio management, journaling, the API, and dashboard.
5. Complete integration testing, deployment tooling, and public documentation/demo assets.

[The detailed roadmap](docs/roadmap.md) defines 25 phases with deliverables and acceptance expectations. V2 self-evolving research remains a future extension, not current implementation work.

## Development philosophy

Build small, inspectable increments in dependency order. Keep domain contracts explicit, calculations deterministic, research reproducible, and execution assumptions visible. Add meaningful tests with each implemented behavior and track provenance for data, strategy versions, runs, and artifacts. Document uncertain choices before committing to infrastructure. Avoid a speculative directory tree and premature dependency installation.

Architecture changes should update `docs/architecture.md`; completed milestones should update the roadmap and this status statement. Do not describe planned capabilities as shipped features.

## Responsible use and limitations

This is a research/engineering platform, not a promise of trading profitability or investment advice. Historical performance does not guarantee future results. Data quality, selection bias, overfitting, changing market conditions, and execution assumptions can invalidate apparent results. Paper-trading outcomes do not establish live-trading performance. Any future financial output must be attributable to deterministic code, documented inputs, and explicit assumptions.
