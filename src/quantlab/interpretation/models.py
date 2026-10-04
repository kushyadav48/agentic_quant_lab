"""Small untrusted draft envelope around existing Phase 5 value types."""
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from quantlab.llm import InvocationResult
from quantlab.strategies import (
    Direction, FeatureReference, Parameter, SessionFilter, SideRules,
    StrategySpecification, TimingIntent,
)
from quantlab.strategies.schema import Identifier, Reference, StopLoss, TakeProfit, Timeframe


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


StrategyText = Annotated[str, StringConstraints(min_length=1, max_length=16000),
                         AfterValidator(_nonblank)]
Note = Annotated[str, StringConstraints(min_length=1, max_length=2000),
                 AfterValidator(_nonblank)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200),
                 AfterValidator(_nonblank)]


class _Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid",
        validate_default=True, revalidate_instances="always", hide_input_in_errors=True)


class InterpretationInput(_Contract):
    """The application assigns identity and an opaque, never fetched source reference."""
    strategy_text: StrategyText = Field(repr=False)
    input_reference: Reference
    strategy_id: Identifier
    version: int = Field(ge=1)


class StrategyDraft(_Contract):
    """Only intent, never identity/provenance/approval; nested types are Phase 5's.

    Nullable fields are required on the wire: omission is not an implicit decision.
    Timing is the documented Phase 5 bar-close/next-open default.
    """
    name: Name
    description: Note | None
    instruments: tuple[Reference, ...] = Field(min_length=1, max_length=32)
    timeframe: Timeframe
    direction: Direction
    long: SideRules | None
    short: SideRules | None
    session: SessionFilter | None
    features: tuple[FeatureReference, ...] = Field(max_length=64)
    parameters: tuple[Parameter, ...] = Field(max_length=64)
    stop_loss: StopLoss | None
    take_profit: TakeProfit | None
    sizing_reference: Reference | None
    timing: TimingIntent = TimingIntent()


EvidenceField = Literal["instruments", "timeframe", "direction", "long", "short",
    "session", "features", "parameters", "stop_loss", "take_profit", "sizing_reference"]


class SourceEvidence(_Contract):
    """Review aid, not proof of semantic equivalence or of human approval."""
    field: EvidenceField
    quote: StrategyText = Field(repr=False)


class Clarification(_Contract):
    ambiguity: Note
    question: Note


class InterpretationStatus(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class StructuredInterpretation(_Contract):
    status: InterpretationStatus
    draft: StrategyDraft | None
    clarifications: tuple[Clarification, ...] = Field(max_length=32)
    evidence: tuple[SourceEvidence, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if self.status is InterpretationStatus.READY:
            if self.draft is None or self.clarifications or not self.evidence:
                raise ValueError("READY requires draft and evidence, without ambiguities")
        elif self.draft is not None or not self.clarifications or self.evidence:
            raise ValueError("clarification requires questions and forbids a draft or evidence")
        return self


class InterpretationResult(_Contract):
    """Invocation-scoped, replay-checked audit record, never an approval artifact."""
    input: InterpretationInput = Field(repr=False)
    interpretation: StructuredInterpretation = Field(repr=False)
    proposal: StrategySpecification | None = Field(repr=False)
    invocation: InvocationResult = Field(repr=False)

    @model_validator(mode="after")
    def check_binding(self) -> Self:
        # Local import avoids a models/prompts/service dependency cycle.
        from .service import _check_result
        _check_result(self)
        return self

    @property
    def status(self) -> InterpretationStatus:
        return self.interpretation.status
