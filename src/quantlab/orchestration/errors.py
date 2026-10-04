"""Sanitized local errors; provider errors remain owned by Phase 13."""


class OrchestrationError(Exception):
    message = "orchestration failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class OrchestrationInputError(OrchestrationError):
    message = "invalid orchestration input"


class OrchestrationContractError(OrchestrationError):
    message = "inconsistent orchestration artifact"


class WorkflowResumeError(OrchestrationError):
    message = "workflow is not available for this review decision"
