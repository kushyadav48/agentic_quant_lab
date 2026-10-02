"""Immutable, provider-neutral strategy intent; no calculations or execution."""
from datetime import datetime, time, timezone
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from quantlab.data.enums import Timeframe
from .enums import (
    ApprovalState, Comparison, Direction, DistanceUnit, ExecutionTiming,
    FeatureType, GroupMode, MarketField, Origin, ParameterType, SignalTiming,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}\z")]
Reference = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"^\S+\z")]
Text = Annotated[str, StringConstraints(min_length=1), AfterValidator(
    lambda v: v if v.strip() else _blank())]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}\z")]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Scalar = int | FiniteDecimal | bool

def _blank() -> str:
    raise ValueError("text must not be blank")

def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return v.astimezone(timezone.utc)

UtcTimestamp = Annotated[datetime, AfterValidator(_utc)]

class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid",
                              validate_default=True, revalidate_instances="always")

class Parameter(Contract):
    name: Identifier
    type: ParameterType
    default: Scalar
    minimum: int | FiniteDecimal | None = None
    maximum: int | FiniteDecimal | None = None
    description: Text | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        expected = {ParameterType.INTEGER: int, ParameterType.DECIMAL: Decimal,
                    ParameterType.BOOLEAN: bool}[self.type]
        if type(self.default) is not expected:
            raise ValueError("default must match declared parameter type")
        bounds = (self.minimum, self.maximum)
        if self.type is ParameterType.BOOLEAN and any(v is not None for v in bounds):
            raise ValueError("boolean parameters cannot have bounds")
        if any(v is not None and type(v) is not expected for v in bounds):
            raise ValueError("bounds must match parameter type")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if self.minimum is not None and self.default < self.minimum:
            raise ValueError("default below minimum")
        if self.maximum is not None and self.default > self.maximum:
            raise ValueError("default above maximum")
        return self

class FeatureArgument(Contract):
    name: Identifier
    value: Scalar

class FeatureReference(Contract):
    feature_id: Identifier
    implementation_id: Identifier | None = None
    feature_type: FeatureType
    parameters: tuple[FeatureArgument, ...] = Field(default=(), max_length=32)
    timeframe: Timeframe | None = None

    @model_validator(mode="after")
    def unique_arguments(self) -> Self:
        if len({p.name for p in self.parameters}) != len(self.parameters):
            raise ValueError("duplicate feature argument names")
        return self

class MarketOperand(Contract):
    kind: Literal["market"] = "market"
    field: MarketField
    offset: int = Field(default=0, ge=0, le=10000)

class FeatureOperand(Contract):
    kind: Literal["feature"] = "feature"
    feature_id: Identifier
    offset: int = Field(default=0, ge=0, le=10000)

class ParameterOperand(Contract):
    kind: Literal["parameter"] = "parameter"
    name: Identifier

class ConstantOperand(Contract):
    kind: Literal["constant"] = "constant"
    value: Scalar

Operand = Annotated[MarketOperand | FeatureOperand | ParameterOperand | ConstantOperand,
                    Field(discriminator="kind")]

class Rule(Contract):
    left: Operand
    comparison: Comparison
    right: Operand

class RuleGroup(Contract):
    mode: GroupMode = GroupMode.ALL
    rules: tuple[Rule, ...] = Field(min_length=1, max_length=64)

class SideRules(Contract):
    entry: RuleGroup
    exit: RuleGroup | None = None

class FixedDistance(Contract):
    kind: Literal["fixed"] = "fixed"
    value: PositiveDecimal
    unit: DistanceUnit

    @model_validator(mode="after")
    def check_percent(self) -> Self:
        if self.unit is DistanceUnit.PERCENT and self.value > 100:
            raise ValueError("percentage must be in (0, 100]")
        return self

class FeatureDistance(Contract):
    kind: Literal["feature"] = "feature"
    feature_id: Identifier
    multiplier: PositiveDecimal = Decimal("1")
    unit: Literal["price"] = "price"

class RiskRewardTarget(Contract):
    kind: Literal["risk_reward"] = "risk_reward"
    multiple: PositiveDecimal

StopLoss = Annotated[FixedDistance | FeatureDistance, Field(discriminator="kind")]
TakeProfit = Annotated[FixedDistance | FeatureDistance | RiskRewardTarget, Field(discriminator="kind")]

class SessionFilter(Contract):
    start_utc: time
    end_utc: time
    weekdays: tuple[int, ...] = Field(min_length=1, max_length=7)
    overnight: bool = False

    @model_validator(mode="after")
    def check_session(self) -> Self:
        if self.start_utc.tzinfo is not None or self.end_utc.tzinfo is not None:
            raise ValueError("UTC wall times must have no tzinfo")
        if len(set(self.weekdays)) != len(self.weekdays) or any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("unique weekdays must be Monday=0 through Sunday=6")
        if self.start_utc == self.end_utc or self.overnight != (self.end_utc < self.start_utc):
            raise ValueError("session ordering must match explicit overnight flag")
        return self

class TimingIntent(Contract):
    signal: SignalTiming = SignalTiming.BAR_CLOSE
    execution: ExecutionTiming = ExecutionTiming.NEXT_BAR_OPEN

class ParentVersion(Contract):
    strategy_id: Identifier
    version: int = Field(ge=1)
    content_digest: Digest

class Provenance(Contract):
    origin: Origin
    source_reference: Reference | None = None
    parent: ParentVersion | None = None
    author_reference: Reference | None = None
    notes: Text | None = None

class ApprovalRecord(Contract):
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    content_digest: Digest
    reviewer: Reference
    reviewed_at: UtcTimestamp
    note: Text | None = None

class StrategyContent(Contract):
    name: Text
    description: Text | None = None
    instruments: tuple[Reference, ...] = Field(min_length=1)
    timeframe: Timeframe
    direction: Direction
    long: SideRules | None = None
    short: SideRules | None = None
    session: SessionFilter | None = None
    features: tuple[FeatureReference, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    stop_loss: StopLoss | None = None
    take_profit: TakeProfit | None = None
    sizing_reference: Reference | None = None
    timing: TimingIntent = TimingIntent()
    provenance: Provenance

    @model_validator(mode="after")
    def check_semantics(self) -> Self:
        from .validation import validate_content
        validate_content(self)
        return self

    def content_digest(self) -> str:
        """SHA-256 of canonical schema-v1 content, including all provenance."""
        body = _canonical(self.model_dump(mode="python"))
        if body["session"]:
            body["session"]["weekdays"].sort()
        for feature in body["features"]:
            feature["parameters"].sort(key=lambda v: v["name"])
        for side in ("long", "short"):
            if body[side]:
                for group in ("entry", "exit"):
                    if body[side][group]:
                        body[side][group]["rules"].sort(key=lambda v: json.dumps(v, sort_keys=True))
        # Sort parent collections only after their nested content is canonical.
        for key in ("instruments", "features", "parameters"):
            body[key] = sorted(body[key], key=lambda v: json.dumps(v, sort_keys=True))
        wire = json.dumps({"schema_version": 1, "content": body},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

class StrategySpecification(Contract):
    schema_version: Literal[1] = 1
    strategy_id: Identifier
    version: int = Field(ge=1)
    content: StrategyContent
    created_at: UtcTimestamp | None = None
    state: ApprovalState = ApprovalState.DRAFT
    approval: ApprovalRecord | None = None

    @property
    def content_digest(self) -> str:
        return self.content.content_digest()

    @model_validator(mode="after")
    def check_approval(self) -> Self:
        if (self.state is ApprovalState.APPROVED) != (self.approval is not None):
            raise ValueError("APPROVED requires approval; other states forbid approval")
        if self.approval and (self.approval.strategy_id != self.strategy_id or
            self.approval.strategy_version != self.version or
            self.approval.content_digest != self.content_digest):
            raise ValueError("approval must bind to exact strategy ID, version and digest")
        return self

    def revise(self, content: StrategyContent, *, created_at: datetime | None = None) -> Self:
        """Create the next draft version, dropping the previous approval."""
        return type(self)(strategy_id=self.strategy_id, version=self.version + 1,
                          content=content, created_at=created_at)

    def mark_validated(self) -> Self:
        return type(self)(strategy_id=self.strategy_id, version=self.version,
                         content=self.content, created_at=self.created_at,
                         state=ApprovalState.VALIDATED)

    def approve(self, record: ApprovalRecord) -> Self:
        if self.state is not ApprovalState.VALIDATED:
            raise ValueError("approval transition requires VALIDATED")
        return type(self)(strategy_id=self.strategy_id, version=self.version,
                         content=self.content, created_at=self.created_at,
                         state=ApprovalState.APPROVED, approval=record)

def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Avoid normalize(), which can round under the ambient Decimal context.
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    return value
