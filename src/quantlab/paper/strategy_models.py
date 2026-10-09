"""Strict, frozen Phase 18C records; construction grants no execution authority."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, model_validator
from quantlab.backtesting import BacktestResult, EvaluationResult, ExecutionCostConfig
from quantlab.data import MarketBar, MarketQuote, PriceType, Timeframe
from quantlab.data.models import PositiveDecimal, UtcTimestamp
from quantlab.features import FeatureObservation
from quantlab.risk import RiskConfig
from quantlab.strategies.schema import Digest
from quantlab.validation import HoldoutResult, RobustnessReport, WalkForwardReport
from .account_models import AccountConfig
from .models import Identity, KernelSnapshot, LogicalInput, OrderSide, PaperContract, stable_id


class StrategyBinding(PaperContract):
    strategy_id: Identity
    strategy_version: Annotated[int, Field(ge=1)]
    strategy_digest: Digest


class ContentRecord(PaperContract):
    namespace: ClassVar[str]
    schema_version: Literal[1] = 1
    record_id: Digest

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.record_id != stable_id(self.namespace,
                self.model_dump(mode="python", exclude={"record_id"})):
            raise ValueError("record identity must bind canonical content")
        return self


def record(cls, **values):
    body = {name: field.default for name, field in cls.model_fields.items()
            if not field.is_required() and name != "record_id"}
    body.update(values)
    if "entry_intent" in body and body["entry_intent"] is None:
        body.pop("entry_intent")
    if "entry_order" in body and body["entry_order"] is None:
        body.pop("entry_order")
    if "exit_order" in body and body["exit_order"] is None:
        body.pop("exit_order")
    if "oco" in body and body["oco"] is None:
        body.pop("oco")
    if "oco_events" in body and not body["oco_events"]:
        body.pop("oco_events")
    result = cls(record_id=stable_id(cls.namespace, body), **body)
    result.canonical_json()
    return result


class DatasetVersion(PaperContract):
    dataset_id: Identity
    version_digest: Digest
    source_reference: Identity


class EvidenceReference(PaperContract):
    evidence_id: Digest
    result_digest: Digest


class ResearchEvidence(ContentRecord):
    """Application-retained independent verification, not a research success flag.

    Report contracts do not embed datasets. This attestation binds the retained
    report to exact dataset versions/request provenance verified outside activation.
    No signatures, dataset recomputation or verifier authentication are implied.
    """
    namespace = "paper-research-evidence-v1"
    report: BacktestResult | HoldoutResult | WalkForwardReport | RobustnessReport
    result_digest: Digest
    request_digest: Digest
    datasets: tuple[DatasetVersion, ...] = Field(min_length=1, max_length=32)
    provenance_reference: Identity
    verifier: Identity
    verified_at: UtcTimestamp
    status: Literal["verified", "unverified", "rejected"]

    @model_validator(mode="after")
    def report_binding(self) -> Self:
        if self.result_digest != stable_id("paper-research-result-v1", self.report):
            raise ValueError("result digest must bind the retained report")
        if len({d.dataset_id for d in self.datasets}) != len(self.datasets):
            raise ValueError("duplicate dataset identities")
        return self


class EligibilityPolicy(PaperContract):
    """No default financial thresholds. None explicitly opts out of that metric."""
    policy_id: Identity
    version: Annotated[int, Field(ge=1)]
    rationale_reference: Identity
    oos_requirement: Literal["holdout", "walk_forward", "either"]
    minimum_oos_observations: Annotated[int, Field(ge=1)]
    minimum_robustness_candidates: Annotated[int, Field(ge=1)]
    minimum_oos_return: Annotated[Decimal, Field(allow_inf_nan=False)] | None
    minimum_oos_drawdown: Annotated[Decimal, Field(le=0, allow_inf_nan=False)] | None
    feature_policy: Literal["causal-raw-only-v1"] = "causal-raw-only-v1"
    execution_policy: Literal["explicit-locked-next-open-v1"] = "explicit-locked-next-open-v1"

    @property
    def digest(self) -> str:
        return stable_id("paper-eligibility-policy-v1", self)


class EligibilityDecision(ContentRecord, StrategyBinding):
    namespace = "paper-eligibility-decision-v1"
    policy_digest: Digest
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=32)
    datasets: tuple[DatasetVersion, ...] = Field(min_length=1, max_length=32)
    reviewer: Identity
    timestamp: UtcTimestamp
    eligible: bool
    reason_reference: Identity

    @model_validator(mode="after")
    def unique(self) -> Self:
        if (len({e.evidence_id for e in self.evidence}) != len(self.evidence)
                or len({e.result_digest for e in self.evidence}) != len(self.evidence)
                or len({d.dataset_id for d in self.datasets}) != len(self.datasets)):
            raise ValueError("duplicate eligibility references")
        return self


class StrategySessionConfig(StrategyBinding):
    schema_version: Literal[1] = 1
    session_id: Identity
    account: AccountConfig
    timestamp: UtcTimestamp
    timeframe: Timeframe
    price_type: Literal[PriceType.MID] = PriceType.MID
    quantity: PositiveDecimal
    risk: RiskConfig
    costs: ExecutionCostConfig
    maximum_events: Annotated[int, Field(ge=1, le=10_000)] = 5000


class AdvancedEntryPolicy(PaperContract):
    """Reviewed v2 extension of close decisions, never an implicit v1 permission.

    Submission/activation uses an actual close quote. Matching is gated by the
    actual adjacent opening, then explicitly permits ordered fresh quote inputs.
    Prices are fixed approved values; no caller or AI parameter substitution.
    """
    schema_version: Literal[2] = 2
    policy_id: Identity
    version: Annotated[int, Field(ge=1)]
    activation_policy: Literal["close-submit-next-open-gate-v2"] = "close-submit-next-open-gate-v2"
    observation_policy: Literal["opening-then-ordered-fresh-quotes-v2"] = "opening-then-ordered-fresh-quotes-v2"
    order_type: Literal["market", "limit", "stop_market", "stop_limit"]
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    liquidity_per_observation: PositiveDecimal | None = None
    maximum_inputs: Annotated[int, Field(ge=3, le=256)] = 128
    maximum_age: timedelta = timedelta(seconds=60)

    @model_validator(mode="after")
    def supported(self) -> Self:
        if self.maximum_age < timedelta(0):
            raise ValueError("negative entry quote age")
        from .models import AdvancedOrderSubmission
        AdvancedOrderSubmission(command_id="validation", causation_id="validation",
            sequence=1, timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc), instrument_id="validation",
            side=OrderSide.BUY, quantity=Decimal("1"), order_type=self.order_type,
            time_in_force=self.time_in_force, limit_price=self.limit_price, stop_price=self.stop_price)
        return self

    @property
    def digest(self):
        return stable_id("paper-advanced-entry-policy-v2", self)


class AdvancedEntryApproval(ContentRecord, StrategyBinding):
    """Human/application review of the exact additional execution configuration."""
    namespace = "paper-advanced-entry-approval-v2"
    schema_version: Literal[2] = 2
    configuration_digest: Digest
    reviewer: Identity
    timestamp: UtcTimestamp
    reason_reference: Identity


class AdvancedEligibilityPolicy(EligibilityPolicy):
    execution_policy: Literal["explicit-next-open-resting-v2"] = "explicit-next-open-resting-v2"


class AdvancedEligibilityDecision(EligibilityDecision):
    namespace = "paper-advanced-eligibility-decision-v2"
    schema_version: Literal[2] = 2
    execution_approval_id: Digest
    configuration_digest: Digest


class AdvancedStrategySessionConfig(StrategySessionConfig):
    schema_version: Literal[2] = 2
    entry_policy: AdvancedEntryPolicy
    execution_approval: AdvancedEntryApproval

    @property
    def authorization_digest(self):
        return stable_id("paper-advanced-entry-configuration-v2",
            self.model_dump(mode="python", exclude={"execution_approval"}))

    @model_validator(mode="after")
    def approved_configuration(self) -> Self:
        approval = self.execution_approval
        if (approval.configuration_digest != self.authorization_digest or
                any(getattr(approval, name) != getattr(self, name) for name in
                    ("strategy_id", "strategy_version", "strategy_digest")) or
                approval.timestamp > self.timestamp):
            raise ValueError("advanced execution approval must bind the exact configuration")
        budget = self.entry_policy.liquidity_per_observation
        if budget is not None:
            n, d = budget.as_integer_ratio()
            sn, sd = self.account.instrument.quantity_increment.as_integer_ratio()
            if (n * sd) % (d * sn):
                raise ValueError("entry liquidity must respect instrument quantity increment")
        return self


def validate_session_config(config):
    cls = AdvancedStrategySessionConfig if type(config) is AdvancedStrategySessionConfig else StrategySessionConfig
    return cls.model_validate(config)


AdmissionReason = Literal["admitted", "invalid_contract", "unapproved_strategy",
    "strategy_mismatch", "missing_policy", "missing_eligibility", "eligibility_mismatch",
    "evidence_missing", "evidence_unverified", "evidence_mismatch", "evidence_insufficient",
    "unsupported_strategy", "unsupported_feature_delivery", "account_incompatible"]


class AdmissionRecord(ContentRecord, StrategyBinding):
    namespace = "paper-admission-v1"
    session_id: Identity
    account_id: Identity
    config_digest: Digest
    timestamp: UtcTimestamp
    sequence: Literal[0] = 0
    policy_digest: Digest | None
    causation_id: Digest | None
    evidence: tuple[EvidenceReference, ...]
    admitted: bool
    reason: AdmissionReason

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.admitted != (self.reason == "admitted"):
            raise ValueError("admission outcome and reason must agree")
        return self


class BarCloseDelivery(LogicalInput):
    kind: Literal["bar_close"] = "bar_close"
    event_id: Identity
    delivered_at: UtcTimestamp
    bar: MarketBar

    @model_validator(mode="after")
    def available(self) -> Self:
        if self.timestamp < max(self.delivered_at, self.bar.available_at):
            raise ValueError("completed bar requires delivery and availability")
        if self.delivered_at < self.bar.end_time:
            raise ValueError("complete OHLC cannot be delivered before bar close")
        return self


class RuntimeRecord(ContentRecord, StrategyBinding):
    session_id: Identity
    account_id: Identity
    admission_id: Digest
    policy_digest: Digest
    sequence: Annotated[int, Field(gt=0)]
    timestamp: UtcTimestamp
    causation_id: Identity


class EntryIntent(RuntimeRecord):
    namespace = "paper-strategy-intent-v1"
    decision_id: Digest
    instrument_id: Identity
    side: OrderSide
    quantity: PositiveDecimal
    source_bar_start: UtcTimestamp
    source_bar_end: UtcTimestamp
    execution_policy: Literal["explicit-locked-next-open-v1"] = "explicit-locked-next-open-v1"
    reason: Literal["entry_signal"] = "entry_signal"

    @model_validator(mode="after")
    def close_signal(self) -> Self:
        if self.source_bar_start >= self.source_bar_end or self.timestamp != self.source_bar_end:
            raise ValueError("entry intent must occur at its source bar close")
        return self


class AdvancedEntryIntent(EntryIntent):
    namespace = "paper-strategy-advanced-intent-v2"
    schema_version: Literal[2] = 2
    execution_policy: Literal["explicit-next-open-resting-v2"] = "explicit-next-open-resting-v2"
    entry_policy: AdvancedEntryPolicy
    execution_approval_id: Digest
    configuration_digest: Digest
    command_id: Digest

    @model_validator(mode="after")
    def command_binding(self) -> Self:
        if self.command_id != stable_id("paper-strategy-submission-v2",
                (self.admission_id, self.decision_id, self.configuration_digest)):
            raise ValueError("advanced command identity must bind admission, decision and configuration")
        return self


class StrategyDecision(RuntimeRecord):
    namespace = "paper-strategy-decision-v1"
    source_bar_start: UtcTimestamp
    source_bar_end: UtcTimestamp
    long: EvaluationResult | None
    short: EvaluationResult | None
    dependencies: tuple[Identity, ...]
    features: tuple[FeatureObservation, ...]
    reason: Literal["entry_signal", "no_signal", "unavailable", "late_bar",
                    "conflicting_signals", "single_entry_consumed"]
    side: OrderSide | None

    @model_validator(mode="after")
    def decision_outcome(self) -> Self:
        if (self.source_bar_start >= self.source_bar_end or self.timestamp < self.source_bar_end
                or not self.dependencies or self.dependencies[-1] != self.causation_id
                or (self.side is not None) != (self.reason == "entry_signal")):
            raise ValueError("decision causation, timing and outcome must agree")
        if self.side is not None and (self.timestamp != self.source_bar_end or
                (self.long if self.side is OrderSide.BUY else self.short) is not EvaluationResult.TRUE):
            raise ValueError("entry decision requires an on-time TRUE side")
        return self


class RuntimeSnapshot(PaperContract):
    admission: AdmissionRecord
    config: AdvancedStrategySessionConfig | StrategySessionConfig
    last_sequence: Annotated[int, Field(ge=0)]
    timestamp: UtcTimestamp
    state: Literal["active", "entry_intent_emitted"]
    inputs: tuple[BarCloseDelivery, ...]
    decisions: tuple[StrategyDecision, ...]
    intent: AdvancedEntryIntent | EntryIntent | None


    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (not self.admission.admitted
                or self.admission.config_digest != stable_id("paper-strategy-config-v1", self.config)
                or len(self.inputs) != len(self.decisions)
                or (self.state == "entry_intent_emitted") != (self.intent is not None)):
            raise ValueError("runtime snapshot must describe an admitted coherent state")
        for item, decision in zip(self.inputs, self.decisions):
            if (item.event_id != decision.causation_id or item.sequence != decision.sequence
                    or item.timestamp != decision.timestamp
                    or decision.admission_id != self.admission.record_id):
                raise ValueError("decision must bind its retained input and admission")
        if self.inputs:
            if (self.last_sequence, self.timestamp) != (self.inputs[-1].sequence, self.inputs[-1].timestamp):
                raise ValueError("runtime clock must match its retained final input")
        elif self.last_sequence != 0 or self.timestamp != self.config.timestamp:
            raise ValueError("empty runtime must retain activation clock")
        if type(self.config) is AdvancedStrategySessionConfig and self.intent is not None:
            cfg, intent = self.config, self.intent
            if (type(intent) is not AdvancedEntryIntent or intent.entry_policy != cfg.entry_policy
                    or intent.configuration_digest != cfg.authorization_digest
                    or intent.execution_approval_id != cfg.execution_approval.record_id
                    or intent.quantity != cfg.quantity or intent.instrument_id != cfg.account.instrument.instrument_id
                    or intent.account_id != cfg.account.account_id or intent.session_id != cfg.session_id
                    or intent.admission_id != self.admission.record_id
                    or any(getattr(intent, name) != getattr(cfg, name) for name in
                        ("strategy_id", "strategy_version", "strategy_digest"))):
                raise ValueError("advanced intent must bind its admitted reviewed configuration")
        if self.intent is not None and not any(d.record_id == self.intent.decision_id
                and d.reason == "entry_signal" for d in self.decisions):
            raise ValueError("intent requires its retained TRUE decision")
        return self


class OpeningDelivery(LogicalInput):
    """An explicitly classified next opening observation, never a complete bar.

    V1 only accepts a genuinely delivered locked quote equal to the opening price.
    A producer must attest adjacency via previous_close_id; no calendar inference.
    """
    kind: Literal["bar_open"] = "bar_open"
    event_id: Identity
    previous_close_id: Identity
    delivered_at: UtcTimestamp
    bar_start: UtcTimestamp
    bar_end: UtcTimestamp
    timeframe: Timeframe
    opening_price: PositiveDecimal
    quote: MarketQuote

    @model_validator(mode="after")
    def causal_open(self) -> Self:
        if (self.bar_start >= self.bar_end or self.quote.timestamp != self.bar_start
                or self.timestamp != self.bar_start or self.delivered_at != self.bar_start
                or self.quote.available_at != self.bar_start
                or self.quote.bid != self.opening_price or self.quote.ask != self.opening_price):
            raise ValueError("requires an on-time actual locked opening quote, without OHLC")
        return self


class StrategyOrderSnapshot(PaperContract):
    """Immutable audit linkage from exact admission and decision to owned fills."""
    admission: AdmissionRecord
    decision: StrategyDecision | None
    intent: AdvancedEntryIntent | EntryIntent | None
    kernel: KernelSnapshot
    openings: tuple[OpeningDelivery, ...]
