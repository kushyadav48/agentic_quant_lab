# Agentic Quant Research & Trading Lab

An AI-assisted, multi-market quantitative research and paper-trading platform, designed to turn human research ideas into explicit, reviewable strategies and evaluate them using deterministic Python calculations.

**Status: early development — Phases 1–3 implemented.** The repository contains the documentation/package foundation, strict market-data domain contracts, and a Dukascopy historical tick-ingestion adapter producing canonical bid/ask quotes for EUR/USD and USD/JPY. Tests use synthetic payloads and mocked HTTP. Dataset-wide validation, OHLC aggregation/resampling, storage, strategies, backtesting, financial metrics, AI integration, agents, risk, APIs, and dashboard functionality remain planned. Successful live downloading has not been verified; a manual probe received HTTP 429.

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
| Quant/data | NumPy, Pandas and/or Polars, SciPy; Statsmodels where useful | Planned; dataframe choice remains open |
| ML | scikit-learn, LightGBM, XGBoost, Optuna, experiment tracking | Planned |
| AI | Provider-agnostic text/vision LLM adapters, LangGraph, MCP | Planned |
| Backend/storage | FastAPI, PostgreSQL, storage adapters | Planned |
| Frontend | Separate professional web dashboard, likely React/Next.js | Planned; final framework not selected |
| Engineering | pytest, Git, structured logging; Docker and CI later | pytest development extra and existing Git metadata |

Pydantic is the sole runtime dependency at this stage. Historical ingestion uses urllib, lzma, struct, and Decimal from the standard library. pytest is available through the development extra. Further dependencies will be introduced only when implemented functionality needs them.

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
│           └── providers/
│               ├── __init__.py
│               ├── base.py
│               └── dukascopy.py
└── tests/
    ├── __init__.py
    └── data/
        ├── __init__.py
        ├── test_models.py
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

Run `python -m pytest` for domain and offline ingestion tests. These cover Forex conventions, strict field types, UTC normalization, OHLC/quote bounds, volume units, immutability, JSON round trips, binary decoding/scaling, request boundaries, provenance, absent data, and transport/corruption errors. No test requires live HTTP access. The import check above verifies the installed package, not any trading functionality.

The `.env.example` file contains guidance only: no environment configuration is consumed yet and no credentials are required.

## Historical quote ingestion

Import `HistoricalQuoteRequest` from `quantlab.data.providers` and `DukascopyProvider` from `quantlab.data.providers.dukascopy`. Construct an Instrument with the pair's explicit price metadata, then iterate `provider.fetch(request)`. Requests accept aware start/end datetimes and use an inclusive start and exclusive end. Fetching requires network access and happens only when the iterator is consumed.

The initial registry supports EUR/USD and USD/JPY. It selects the public hourly `.bi5` archive, decodes LZMA-Alone binary ticks, and returns existing MarketQuote objects without bar aggregation or storage. Additional providers can implement the same quote protocol. See [the implemented ingestion boundary](docs/architecture.md#implemented-phase-3-ingestion) for exact format assumptions, the source-event availability policy, limits, and error handling.

Downloaded data remains subject to Dukascopy's applicable terms/licensing; public access does not automatically permit redistribution. No real historical dataset is included in this repository.

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
