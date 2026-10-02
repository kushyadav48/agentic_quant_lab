# Strategy specification contract (schema version 1)

## Purpose and boundary

`quantlab.strategies` is the shared, provider-neutral contract for manual,
natural-language, chart multimodal, ML and agent proposals. It performs only
structural/semantic validation and deterministic serialization. It has no network,
file persistence, indicators, signals, execution, backtesting or risk engine.
Future interpreters must produce this same contract and retain source artifacts
externally through references. Raw images and model responses do not belong here.

## Identity, content and lifecycle

StrategySpecification separates strategy_id (stable identity), version (positive
integer revision), schema_version (currently 1), and StrategyContent. Optional
created_at requires an aware timestamp and normalizes to UTC. All models are
strict, frozen, forbid unknown fields and revalidate nested instances. Collections
are tuples of immutable typed values; there are no mutable configuration mappings.
Python callers supply enum members, Decimal, datetime/time objects and tuples.
JSON accepts the corresponding wire forms through model_validate_json.

Every constructible specification passes structural and reference validation.
DRAFT means not yet explicitly promoted; mark_validated() creates VALIDATED;
approve(record) requires VALIDATED and creates APPROVED. This lifecycle records
review intent, not research validation success. Approval confirms interpretation
and structure only, with no claim of profitability, robustness, safety, suitability
or successful financial validation. Authentication and authorization of reviewers
belong to future application services; a local contract cannot authenticate a human.

ApprovalRecord contains strategy_id, strategy_version, content_digest, reviewer,
aware reviewed_at, and optional note. APPROVED requires an exact matching record;
other states forbid one. revise(new_content) creates version + 1 as a new DRAFT
with no approval. Even a revision with identical content requires approval for its
new version. Existing objects remain unchanged. A future registry must enforce
unique (strategy_id, version) assignments and lineage across stored records.
Use constructors/model_validate or revise for edits; Pydantic model_construct and
model_copy(update=...) bypass validation and are not supported authoring APIs.

## Content and deterministic digest

StrategyContent contains name, optional description, nonempty unique instruments,
Timeframe, Direction, long/short SideRules, optional session, features, parameters,
stop_loss, take_profit, sizing_reference, timing and mandatory provenance.
content_digest is computed, never caller-controlled, using SHA-256 over UTF-8 JSON
with sorted keys, compact separators, ASCII escaping, and an envelope containing
schema_version=1 and all StrategyContent fields, including defaults and nulls.
All provenance fields participate: changing origin, references, parent, author or
notes changes content identity. Name and description also participate.
strategy_id, version, created_at, lifecycle state and approval (including review
time/note) are excluded. Approval separately binds identity and revision.

Instrument sets, feature declarations, parameter declarations, feature arguments,
weekdays and ALL/ANY rules are canonically sorted because their order has no
semantic significance. Decimal strings discard trailing fractional zeroes and
normalize signed zero without depending on the ambient Decimal precision.
Canonicalization does not simplify Boolean algebra or remove duplicate rules.
Logical equivalence here means identical declared content modulo these explicit
ordering and numeric normalization policies; it does not prove equivalence of
arbitrary strategies. JSON round trips retain the original declaration order.

## Rules and feature references

Rule has left/right discriminated operands and a Comparison enum: gt, ge, lt, le,
eq, crosses_above, crosses_below. RuleGroup has ALL or ANY and 1–64 rules; groups
cannot nest. A SideRules has required entry and optional exit. LONG requires only
long rules, SHORT only short rules, BOTH requires both. There is no implicit rule
inversion or unspecified side. Stops/targets are independent intent, not magic
exit strings. Missing exit/stop/target is allowed; later engines must define holding
and run-end behavior explicitly.

Operand kinds: market (OPEN/HIGH/LOW/CLOSE/BID/ASK), feature (feature_id), parameter
(name), constant (integer, finite Decimal or boolean). Market/feature offset is a
nonnegative number of completed bars, bounded at 10000; negative/future offsets
are rejected. Cross operators require a previous comparable observation in a
future engine, whose availability/warm-up policy is deferred. Boolean operands
are allowed only for equality with another declared boolean. Other operand
references are numeric; feature output compatibility requires a future registry.
Identifiers follow [A-Za-z][A-Za-z0-9_-]{0,127}; dotted paths, function calls,
arbitrary Python expressions, eval and executable rule strings are unsupported.
Descriptions/notes are inert text, never expressions.

FeatureReference declares a unique feature_id, category (indicator, ml_signal,
level), at most 32 uniquely named FeatureArgument scalar values, and optional
Timeframe override. Market fields are direct operands, not indicator definitions.
Feature parameters are immutable structured name/value data, not arbitrary
objects. Feature and parameter operands, including stop/target feature references,
must resolve to declarations. Indicator algorithms, registry lookup, ML artifacts,
feature units and actual calculations are deferred to Phase 6 or later.

## Configurable parameters

Parameter declares name, type (integer, decimal, boolean), default, optional
minimum/maximum and description. Defaults and bounds must exactly match their
declared Python type; bool is not an integer. Decimals must be finite. Bounds are
inclusive, minimum <= maximum, and defaults must lie inside them. Boolean bounds
are forbidden. Parameter names must be unique; no arbitrary object defaults.

## Stop/target and sizing intent

FixedDistance uses a positive finite Decimal and explicit PRICE or PERCENT unit.
PRICE is a distance in the instrument's quoted price units; PERCENT means percentage
points of entry price (2 means 2%), bounded to (0,100]. No universal pip convention
is assumed. FeatureDistance uses a declared numeric feature with price-distance
units and positive Decimal multiplier; feature units require later registry checks.
RiskRewardTarget is take-profit only, with a positive multiple of initial stop
risk and a required stop_loss. These contracts calculate no price levels.
The optional sizing_reference identifies an external future sizing policy; no
quantity, allocation or risk computation occurs here. Stops and targets apply to
both sides, measured from their respective entry prices.

## Session and execution intent

SessionFilter has naive UTC wall-clock start/end times, unique weekdays Monday=0
through Sunday=6, and explicit overnight. End > start requires overnight=False;
end < start requires True; equal times are rejected (no implicit full-day session).
Intervals are half-open [start,end); overnight weekdays label the session start
date and the end is on the following UTC date. No membership evaluation, exchange
calendar, holidays, DST conversion or market-open inference is implemented.
TimingIntent supports only signal=BAR_CLOSE and execution=NEXT_BAR_OPEN. These
safe defaults declare intent and do not imply orders, fills or calendar resolution.
Same-bar execution policies are currently rejected.

## Example: manual draft and exact-version approval

```python
from datetime import datetime, timezone
from decimal import Decimal
from quantlab.data import Timeframe
from quantlab.strategies import (
    ApprovalRecord, Comparison, ConstantOperand, Direction, MarketField,
    MarketOperand, Origin, Provenance, Rule, RuleGroup, SideRules,
    StrategyContent, StrategySpecification,
)

spec = StrategySpecification(
    strategy_id="manual_baseline", version=1,
    content=StrategyContent(
        name="Manual threshold", instruments=("fx:EURUSD",),
        timeframe=Timeframe.M5, direction=Direction.LONG,
        long=SideRules(entry=RuleGroup(rules=(Rule(
            left=MarketOperand(field=MarketField.CLOSE),
            comparison=Comparison.GT,
            right=ConstantOperand(value=Decimal("1.10")),
        ),))), provenance=Provenance(origin=Origin.MANUAL),
    ),
)
validated = spec.mark_validated()
approved = validated.approve(ApprovalRecord(
    strategy_id=validated.strategy_id, strategy_version=validated.version,
    content_digest=validated.content_digest, reviewer="user:reviewer",
    reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
))
restored = StrategySpecification.model_validate_json(approved.model_dump_json())
assert restored == approved
```

This reviewable baseline is a contract example, not an implemented trading strategy.
Natural-language/chart/ML/agent adapters will emit drafts with their Origin and
artifact references, resolve ambiguities before validation, and use the same
version-bound human review. There is no interpreter or agent implementation yet.

## Limitations

No execution eligibility, persistence/registry, reviewer authentication, indicator
registry, units inference, recursive logic, arithmetic expressions, dynamic
universe, side-specific stops, trailing stops, exchange calendars or strategy
performance analysis is implemented. References are syntactically validated, not
resolved against external systems. Schema evolution requires an explicit new
schema version and migration policy; unknown versions are rejected.
