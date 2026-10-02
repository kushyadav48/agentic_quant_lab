# Architecture

**Status: planned architecture; application functionality is not implemented.** This document is the current architectural source of truth. The only existing implementation is the package/configuration foundation described in the README. Layer names below describe responsibilities, not modules already present in the repository.

## Goals and boundaries

The platform will support reproducible quantitative research from human-authored ideas through historical testing, validation, and controlled paper trading. Users will supply structured rules, natural language, or one or multiple chart images with text explanations. Each route will converge on a common StrategySpecification before execution.

Forex is the first detailed market implementation. Instrument, timestamp, execution, currency, and data contracts must support later equities and crypto adapters without rewriting the core. The design favors a modular Python core with explicit interfaces; separate services are not required initially.

In scope for the planned first-generation platform: data ingestion and quality checks, strategy definitions, deterministic quant engines, research validation, ML experiments, AI-assisted interpretation, agent orchestration, MCP tools, risk, paper trading, portfolios, journaling, and a separate API/dashboard.

Outside current scope: real-money brokerage execution, autonomous strategy deployment, guaranteed profitability, and V2 self-evolving alpha research. This foundation step implements none of the functional layers. AI output and screenshots are research inputs, not authoritative historical prices or approved execution instructions.

## Planned system flow

```mermaid
flowchart TD
    User["User"] --> UI["Dashboard / API"]
    UI --> Manual["Manual structured rules"]
    UI --> Inputs["Natural language / chart images + text"]
    Inputs --> AI["Provider-agnostic LLM interpretation"]
    AI --> Draft["Draft StrategySpecification"]
    Manual --> Draft
    Draft --> Review["User review + version-bound approval"]
    Review --> Spec["Validated, approved StrategySpecification"]
    Sources["Historical data providers"] --> Data["Market data validation / resampling"]
    Data --> Features["Deterministic features / indicators"]
    Spec --> Backtest["Deterministic backtest + execution costs"]
    Features --> Backtest
    Backtest --> Analytics["Deterministic analytics"]
    Analytics --> Validation["OOS / walk-forward / robustness"]
    Features --> ML["ML research with isolated splits"]
    ML --> Draft
    UI --> Agents["LangGraph research orchestration"]
    Agents --> Tools["MCP bounded quant tools"]
    Tools --> Services["Application services / approval checks"]
    Services --> Backtest
    Services --> Validation
    Services --> Risk["Deterministic risk controls"]
    Validation --> Eligibility["Recorded validation / paper-trading eligibility"]
    Spec --> Eligibility
    Eligibility --> Risk
    Portfolio["Portfolio state / aggregate exposure"] --> Risk
    Risk --> Paper["Paper-trading simulator"]
    Paper --> Portfolio
    Paper --> Journal["Trading journal"]
    Backtest --> Storage["Storage adapters / provenance / audit"]
    Validation --> Storage
    Review --> Storage
    Portfolio --> Storage
    Journal --> Storage
    Storage --> UI
```

Arrows represent information/control flow, not Python import direction. Every execution entry point, including internal application services and MCP tools, must enforce the same approval and risk rules. The diagram is conceptual; no network services or tools exist yet.

## Architectural layers

### Market data

Own canonical instrument metadata and time-indexed market observations, ingestion adapters, dataset provenance, and quality reports. Record provider, instrument identity, timestamp semantics, timezone, granularity, price type (bid, ask, mid, trade), units, and source/version identifiers.

Forex contracts must describe base/quote currencies, tick and pip sizes, trading calendar, and available bid/ask information. Core contracts should not assume every market uses pips, continuous sessions, or a particular lot size. Preserve raw input separately from normalized datasets; transformations must be traceable.

Normalize timestamps to timezone-aware UTC while preserving exchange/session calendar information. Detect duplicates, gaps, ordering errors, malformed OHLC values, and missing prices. Missing bars must not be silently invented. Resampling must define bar labels, interval closure, availability times, and treatment of partial bars so features cannot access unfinished candles.

Provider licensing and redistribution constraints belong to dataset metadata. Local research data is excluded from Git; public examples will require suitable redistribution rights.

### Strategy specification

A versioned, typed StrategySpecification will be the shared contract for manual, natural-language, image-derived, and future ML-informed proposals. Planned fields include instrument universe, timeframe, session filters, feature definitions, entry/exit conditions, sizing policy references, order timing, execution assumptions, parameters, and provenance.

Use a declarative rule representation with a bounded supported vocabulary. Never execute arbitrary model-generated Python, expressions through unsafe evaluation, or uploaded code. Reject unsupported rules and unresolved ambiguities rather than guessing executable semantics.

Separate schema validity from human approval and research eligibility. The intended lifecycle is draft → validated → approved → tested, with validation results and paper-trading eligibility attached to specific versions. Human approval confirms interpretation; it does not establish profitability or risk suitability.

Approval must identify the immutable specification version/content digest, reviewer, and review timestamp. Edits invalidate approval for the revised version. AI-origin drafts require explicit human approval before backtesting or paper trading. Image-derived drafts additionally retain the reviewed input bundle and interpretation record. Manual submissions will use the same review/approval contract for a consistent execution boundary.

### Features and indicators

Compute deterministic, timestamped features from validated historical observations. Document lookback windows, warm-up periods, missing-value handling, and the earliest time each output is available. Feature definitions are shared between backtesting, ML, and paper trading. Fitted transformations must carry their training window and artifact version.

### Backtesting and execution simulation

Turn an approved specification, versioned dataset, and explicit run configuration into deterministic orders, fills, positions, cash balances, and an equity curve. Separate signal formation from order submission and fill timing. An observation available only at bar close must not justify an earlier fill; same-bar execution requires a documented finer-grained observation model.

Execution policies will model bid/ask spread, slippage, fees, latency assumptions, order types, minimum sizes, tick/lot rounding, and market availability. Forex financing/rollover and currency conversion must be explicit when relevant. If both stop and target fall inside a bar and their order is unknown, use a documented conservative policy or reject the ambiguous scenario; do not infer a favorable path.

Research sizing and account constraints must be deterministic from the first engine iteration. A later reusable risk engine will centralize these controls before paper trading. Cost-free engine fixtures may test mechanics, but results cannot be presented as realistic research until the cost-model phase is complete.

### Performance analytics

Calculate P&L, returns, drawdown, trade statistics, exposure, and risk-adjusted metrics using deterministic code. Define valuation currency, annualization conventions, return frequency, financing and fee treatment, benchmark assumptions, and handling of undefined statistics. Preserve metric definitions and engine versions alongside values. An LLM may explain these results, but cannot replace or silently recompute them.

### Research validation

Own chronological out-of-sample splits, rolling/expanding walk-forward windows, parameter sensitivity, regime analysis, and session analysis. Train/validation/test separation applies where fitting or tuning occurs. Keep final holdouts isolated from iterative selection and record all tested candidates to make selection effects visible.

Guard against leakage by fitting preprocessing only on training windows, preventing labels or overlapping targets from crossing split boundaries, and using purge/embargo policies when required by label horizons. Regime definitions, session timezone/DST conventions, and tuning budgets must be explicit. Report robustness and uncertainty; no validation method guarantees future performance.

### ML research

Own reproducible feature/label construction, training, tuning, evaluation, and model artifact metadata. Begin with simple baselines and add scikit-learn, boosting libraries, Optuna, and tracking tools only as needed. Record seeds, dataset/split identities, hyperparameters, preprocessing state, and model versions.

ML predictions are inputs to an explicit strategy specification and deterministic execution/risk pipeline. A trained model is not an independently authorized trader. Model selection must use validation data; final test data cannot feed tuning or feature-selection loops.

### AI and multimodal interpretation

A provider-agnostic adapter will support text and vision requests with structured output validation. Record provider/model identifier, prompt version, relevant parameters, input provenance, interpretation output, and uncertainty. External provider selection, privacy policy, retention, and upload limits remain implementation decisions.

Treat chart uploads, extracted text, and provider responses as untrusted content. Uploaded text must never become privileged orchestration instructions. Validate file types and sizes at the future upload boundary and associate each image with its explanation. Multiple images form a versioned input bundle with explicit ordering/context.

Interpretation will expose chart observations, proposed rules, assumptions, unresolved ambiguities, and links between proposed rules and inputs. Screenshots do not supply reliable historical time series. Missing instrument, timeframe, entry/exit, or sizing semantics must remain unresolved until the user supplies them. All resulting proposals use StrategySpecification and the approval gate.

### LangGraph agents

Agents will coordinate research tasks, request tool operations, and explain persisted results. State should contain artifact/run references, bounded task progress, errors, and checkpoints. Define tool permissions, cancellation, retry limits, time/cost budgets, and human review checkpoints.

An agent cannot grant approval, change risk policy, deploy live trading, or promote an unreviewed strategy through a privileged route. Agents consume deterministic tool outputs with provenance instead of treating their own generated numbers as financial evidence.

### MCP and application tools

Expose narrow, typed operations over application services, such as loading approved specifications, starting backtests, querying run status, and retrieving computed analytics. Use validated schemas, permitted resource identifiers, explicit error responses, audit records, and idempotency for operations that create runs or simulated orders.

Do not expose arbitrary shell/code execution or an unrestricted database interface. Long-running work should return a run identifier and support status/cancellation. The same approval, authorization, and risk checks must apply whether a request arrives through MCP, the API, or a direct internal service.

### Deterministic risk

Own position sizing and policies for per-trade loss, leverage/margin, instrument concentration, aggregate exposure, daily loss, drawdown, and data freshness as those policies are implemented. Policies will be versioned and administered through explicit human-controlled configuration, never by an LLM-generated override.

Before every simulated order, evaluate current account/portfolio state and relevant policy limits. Denials must include machine-readable reasons. Missing or stale risk inputs fail closed; an engine failure is not permission to trade. Account state updates and checks must be coordinated so concurrent strategies cannot independently spend the same risk budget.

Hard limits apply after strategy sizing proposals. Stop controls must prevent new orders and document the treatment of pending orders/positions. Reusable risk policy interfaces support consistent backtest and paper-trading decisions while preserving execution-mode differences.

### Paper trading

Own simulated order lifecycle, fills, balances, positions, clock/feed integration, and event recovery. Entry requires an approved strategy version, recorded validation satisfying a declared eligibility policy, usable data, and current risk approval. Eligibility thresholds will be specified before this phase; historical validation is not a guarantee.

Use deterministic replayable execution rules, explicit simulated costs, duplicate-event/order handling, and journaled state transitions. Restart/reconciliation behavior and disconnect/stale-data handling must be tested before continuous use. No live broker adapter or real-money order path is part of the first-generation scope.

### Portfolio

Maintain multiple strategies and instruments, allocations, cash, valuation currency, currency conversions, and aggregate exposures. Attribute trades and P&L to strategies without losing the portfolio-wide view. Publish consistent portfolio state to the risk engine before execution and derive valuations from timestamped, provenance-bearing prices.

### Journal

Link strategy versions, approval records, research runs, simulated orders/fills, risk decisions, and human notes. Keep immutable execution/audit events distinct from editable annotations. Journal records will support reconciliation, attribution, and research review; an AI-written narrative cannot rewrite recorded fills or metrics.

### API/backend

A future FastAPI application will provide transport schemas, authentication/authorization when deployed, input validation, job control, and access to application services. Business rules remain in the Python core/services, not route handlers. The API must not expose credentials or permit clients to assert approval/eligibility without authorized transitions.

Introduce background workers only when workload demands them. Design request/run identifiers and error contracts before adding distributed infrastructure. CLI/tests and the API should invoke the same services rather than duplicate financial logic.

### Dashboard/frontend

A separate web interface will support strategy authoring/review, chart uploads, run comparison, validation results, risk visibility, paper-trading state, portfolios, and journaling. React/Next.js is a likely direction, not a final dependency decision.

Show draft versus approved state, historical versus simulated results, data ranges, execution assumptions, and run provenance. Review screens must display unresolved interpretation details and the exact version being approved. Display authoritative backend results; frontend formatting must not become a second financial calculation engine.

### Storage

Persist structured metadata, strategy versions, approvals, run configurations/results, risk policies, portfolios, and journal events through repository/storage interfaces. PostgreSQL is the planned relational direction once persistence requirements justify it. Historical tables and large images/model artifacts may use separate file/columnar/object storage; final formats remain open.

Prefer immutable dataset and artifact versions with identifiers/content hashes, explicit schema migrations, and traceable transformations. A reproducible run record should reference specification version, approval, dataset, feature/model artifacts, code version, cost/risk configuration, seeds, and output definitions.

Keep secrets and personal/local research artifacts out of Git. Structured logs should include run/correlation identifiers and redact credentials and sensitive uploaded content. Backup, retention, deletion, and access policies must be specified when storage is implemented.

## Dependency direction

- Domain contracts and deterministic calculations must not depend on FastAPI, dashboard code, LLM providers, LangGraph, MCP, or a specific database.
- Application services coordinate domain engines through explicit interfaces for data, storage, jobs, clocks, and execution.
- Provider, database, API, MCP, and agent integrations sit outside the core and depend on its contracts. Concrete adapters are wired at an application composition boundary.
- The frontend depends on the API contract. AI/agents depend on bounded services/tools; financial engines do not require an LLM to run.
- Approval and risk policies are enforced inside application/domain boundaries, so changing transport or model provider cannot bypass them.

These boundaries are conceptual now. Add concrete modules incrementally with tested behavior rather than generating an empty module for every layer.

## Safety and reproducibility invariants

| Concern | Required boundary |
| --- | --- |
| Authoritative financial values | Python engines compute indicators, signals, orders, fills, P&L, statistics, sizing, portfolio values, and risk. LLMs only interpret, reason, orchestrate, and explain. |
| Human review | Approval records bind to exact specification versions; interpreted drafts cannot run without explicit human approval. |
| Risk authority | Human-controlled deterministic policies cannot be overridden by AI, agents, tools, or frontend requests. |
| Look-ahead bias | Availability timestamps, feature warm-up, split boundaries, and fill timing enforce causality. |
| Unrealistic execution | Record price basis, spread, fees, slippage, financing, calendar, and intrabar ambiguity policies. |
| Overfitting/data leakage | Isolate holdouts, fit on training windows, record tuning histories, and use walk-forward/robustness evaluation. |
| Reproducibility | Version inputs, assumptions, code, schemas, models, and run artifacts; record stochastic seeds. |

These are planned acceptance requirements, not claims of implemented protections.

## Multi-market extensibility

Shared contracts will cover instrument identifiers, asset class, price/quantity increments, contract multipliers, currencies, calendars, timestamped observations, orders, fills, positions, and valuation. Market policies/adapters supply conventions:

- Forex: pip/lot conventions, bid/ask spread, rollover, base/quote conversion, and session boundaries.
- Equities: exchange calendars, corporate actions, adjusted versus raw prices, delistings/universe history, and short/borrow constraints.
- Crypto: continuous calendars, venue-specific sizes/fees, and funding/leverage rules for derivatives when supported.

These are future requirements, not market implementations. Avoid universal assumptions such as a fixed trading-day count, constant spread, USD valuation, or identical leverage rules.

## Future V2 extension points

Versioned strategy registries, provenance-bearing experiments, bounded orchestration, model artifacts, and deterministic tool contracts can later support automated candidate generation and alpha research. Any future evolution loop would still need isolated evaluation, budgets, human promotion policy, and unchanged risk authority.

Do not implement evolution loops, self-modifying code, autonomous promotions, or live execution now. V2 requires a separate proposal and scope decision after the first-generation platform is validated.

## Open decisions

Historical data provider and licensing; Pandas versus Polars; event-driven versus vectorized engine internals; historical storage formats; LLM providers and upload/privacy constraints; validation/eligibility thresholds; authentication model; frontend framework; and deployment topology remain open. Resolve each through a focused design decision when its phase begins and update this document with the resulting tradeoffs.
