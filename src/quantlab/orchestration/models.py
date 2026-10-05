"""Immutable workflow contracts; the original interpretation owns the proposal."""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantlab.interpretation import (
    Clarification, InterpretationInput, InterpretationResult, InterpretationStatus,
    MultimodalInput, MultimodalResult,
)
from quantlab.strategies import ApprovalState, StrategySpecification
from quantlab.strategies.schema import Digest, Identifier, Reference

from .enums import InterpretationRoute, ReviewAction, WorkflowStatus
from .errors import OrchestrationContractError

APPROVAL_BOUNDARY = "Separate exact-version Phase 5 validation and human approval are required."
REVIEW_INSTRUCTIONS = "Review the original input, evidence and DRAFT proposal; accept, reject or request revision."


class _Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid",
        validate_default=True, revalidate_instances="always", hide_input_in_errors=True)


class OrchestrationRequest(_Contract):
    thread_id: Identifier
    input: InterpretationInput | MultimodalInput = Field(repr=False)

    @property
    def route(self) -> InterpretationRoute:
        return (InterpretationRoute.TEXT if type(self.input) is InterpretationInput
                else InterpretationRoute.MULTIMODAL)


class WorkflowReference(_Contract):
    thread_id: Identifier


class ReviewBinding(_Contract):
    thread_id: Identifier
    strategy_id: Identifier
    version: int = Field(ge=1)
    content_digest: Digest


class HumanReviewRequest(ReviewBinding):
    proposal_state: Literal[ApprovalState.DRAFT] = ApprovalState.DRAFT
    source_reference: Reference
    route: InterpretationRoute
    instructions: Literal[REVIEW_INSTRUCTIONS] = REVIEW_INSTRUCTIONS
    approval_boundary: Literal[APPROVAL_BOUNDARY] = APPROVAL_BOUNDARY


class HumanReviewDecision(ReviewBinding):
    """Caller-submitted human choice, not an authenticated Phase 5 approval record."""
    action: ReviewAction


def review_for(request: OrchestrationRequest,
               result: InterpretationResult | MultimodalResult) -> HumanReviewRequest:
    proposal = result.proposal
    if proposal is None or proposal.state is not ApprovalState.DRAFT:
        raise OrchestrationContractError()
    return HumanReviewRequest(thread_id=request.thread_id,
        strategy_id=proposal.strategy_id, version=proposal.version,
        content_digest=proposal.content_digest, source_reference=request.input.input_reference,
        route=request.route)


def decision_matches(review: HumanReviewRequest, decision: HumanReviewDecision) -> bool:
    return all(getattr(review, field) == getattr(decision, field)
               for field in ReviewBinding.model_fields)


def decision_status(action: ReviewAction) -> WorkflowStatus:
    return {ReviewAction.APPROVE: WorkflowStatus.ACCEPTED_FOR_APPROVAL,
            ReviewAction.REJECT: WorkflowStatus.REJECTED,
            ReviewAction.REQUEST_REVISION: WorkflowStatus.REVISION_REQUESTED}[action]


class WorkflowSnapshot(_Contract):
    request: OrchestrationRequest = Field(repr=False)
    status: WorkflowStatus
    result: InterpretationResult | MultimodalResult | None = Field(default=None, repr=False)
    review: HumanReviewRequest | None = None
    decision: HumanReviewDecision | None = None
    phase5_approval_occurred: Literal[False] = False
    approval_boundary: Literal[APPROVAL_BOUNDARY] = APPROVAL_BOUNDARY

    @model_validator(mode="after")
    def check_binding(self) -> Self:
        if self.result is None:
            if (self.status is not WorkflowStatus.INTERPRETING
                    or self.review is not None or self.decision is not None):
                raise OrchestrationContractError()
            return self
        expected = (InterpretationResult if self.request.route is InterpretationRoute.TEXT
                    else MultimodalResult)
        if type(self.result) is not expected or self.result.input != self.request.input:
            raise OrchestrationContractError()
        if self.result.status is InterpretationStatus.NEEDS_CLARIFICATION:
            if (self.status is not WorkflowStatus.NEEDS_CLARIFICATION
                    or self.review is not None or self.decision is not None):
                raise OrchestrationContractError()
        else:
            if self.review != review_for(self.request, self.result):
                raise OrchestrationContractError()
            if self.decision is None:
                if self.status is not WorkflowStatus.AWAITING_HUMAN_REVIEW:
                    raise OrchestrationContractError()
            elif (not decision_matches(self.review, self.decision)
                  or self.status is not decision_status(self.decision.action)):
                raise OrchestrationContractError()
        return self

    @property
    def route(self) -> InterpretationRoute:
        return self.request.route

    @property
    def proposal(self) -> StrategySpecification | None:
        return None if self.result is None else self.result.proposal

    @property
    def clarifications(self) -> tuple[Clarification, ...]:
        return () if self.result is None else self.result.interpretation.clarifications
