"""Workflow outcomes are separate from interpretation and strategy approval."""
from enum import StrEnum


class InterpretationRoute(StrEnum):
    TEXT = "TEXT"
    MULTIMODAL = "MULTIMODAL"


class WorkflowStatus(StrEnum):
    INTERPRETING = "INTERPRETING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AWAITING_HUMAN_REVIEW = "AWAITING_HUMAN_REVIEW"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    ACCEPTED_FOR_APPROVAL = "ACCEPTED_FOR_APPROVAL"


class ReviewAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"
