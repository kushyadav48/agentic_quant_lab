# Roadmap

**Status: early development.** Phases 1–3 are implemented: the documentation/package foundation, strict market-data domain contracts, and the Dukascopy historical tick-to-quote adapter with offline tests. Successful live downloading remains unverified after an HTTP 429 probe. Phases 4–25 remain planned. The phase order follows dependencies, and later phases must not be treated as available features.

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

Implemented: provider-neutral quote request/protocol; Dukascopy public hourly tick adapter for EUR/USD and USD/JPY; injectable urllib transport with finite timeouts and payload bounds; isolated LZMA-Alone/big-endian decoding; metadata-based Decimal scaling; UTC range filtering; payload-hash provenance; and distinct no-data, transport, corruption, and unsupported-configuration handling. Tiny generated binary fixtures and HTTP doubles keep all tests offline. Phase 2 contracts and runtime dependencies are unchanged. No storage or bar aggregation is implemented.

Deliverables: selected provider/format adapter, licensing notes, raw-source provenance, import configuration, error handling, and small legal fixtures. Verify ingestion with reproducible inputs; do not imply screenshots are historical datasets.

## 4. Data validation, resampling, and storage

Purpose: produce reliable, versioned research datasets.

Deliverables: duplicate/gap/OHLC/timestamp checks, explicit missing-data policy, causal resampling and bar-availability semantics, normalized storage adapter, dataset identifiers, and quality reports. Choose dataframe and file formats here. A local storage adapter can precede PostgreSQL.

## 5. Strategy specification

Purpose: define the shared executable contract for all strategy input routes.

Deliverables: versioned StrategySpecification, bounded declarative rules, parameter/schema validation, provenance, immutable version identities, and user approval state. Define approval invalidation on edits and reject unsupported or ambiguous rules. Establish an approved manual strategy fixture for subsequent engine work.

## 6. Feature/indicator engine

Purpose: compute reusable, deterministic strategy inputs.

Deliverables: an initial small indicator set, feature registry/contracts, warm-up and missing-value policies, availability timestamps, and reference-value/causality checks. Add only the quantitative libraries required by implemented features.

## 7. Backtesting engine

Purpose: replay approved strategies against historical data with explicit timing.

Deliverables: simulation clock, signal/order/fill lifecycle, account state, deterministic sizing/account constraints, cash/positions/equity artifacts, and replayable run configurations. Test no future-data access and signal/fill ordering. Any cost-free fixtures are engine tests, not realistic performance claims.

## 8. Spread, slippage, and cost modeling

Purpose: make execution assumptions explicit and useful for research.

Deliverables: bid/ask policies, configurable spread/slippage/fees, latency/timing assumptions, size/tick rounding, Forex financing and currency-conversion conventions where relevant, and intrabar ambiguity handling. Record assumptions per run and verify cost effects using controlled examples.

## 9. Performance analytics

Purpose: derive authoritative performance results from simulated account/trade records.

Deliverables: deterministic return/P&L/drawdown/trade/exposure statistics, documented annualization and currency conventions, defined handling of undefined metrics, and reproducible result artifacts. Verify against hand-computed small cases.

## 10. Out-of-sample, walk-forward, and robustness validation

Purpose: evaluate stability and expose selection/overfitting risks.

Deliverables: chronological split contracts, untouched holdouts, rolling/expanding walk-forward evaluation, parameter sensitivity, regime/session analysis, leakage checks, and candidate/tuning history. Apply purge/embargo when target horizons require it; report assumptions and uncertainty.

## 11. Deterministic risk engine

Purpose: centralize enforceable sizing and account/portfolio limits before simulated trading.

Deliverables: versioned risk policies, per-order decisions and rejection reasons, exposure/leverage/drawdown/loss controls, stale-data checks, stop controls, and atomic risk-budget handling. Integrate reusable checks into the backtest path and test fail-closed behavior and attempted bypasses.

## 12. ML research pipeline

Purpose: support fitted models without weakening research isolation.

Deliverables: causal features/labels, split-aware preprocessing, baseline models, reproducible training/evaluation, experiment metadata and artifacts. Add tuning, boosting libraries, and tracking incrementally. Verify fitting only on training windows and prevent final-test feedback into selection.

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
