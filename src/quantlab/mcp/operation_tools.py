"""Strict adapters bound to one process-local operation namespace."""
from typing import Any

from .decoding import decode_request, service_issue
from .models import AdapterIssue
from .operation_models import (
    FailureClass, OperationAdapterResult, OperationReference, OperationState, OperationSubmission,
)
from .operations import OperationInputError, ResearchOperations


def _rejected(error: OperationInputError) -> OperationAdapterResult:
    return OperationAdapterResult(success=False, issues=(AdapterIssue(
        code=error.code, message="Research operation rejected."),))


class OperationTools:
    def __init__(self, operations: ResearchOperations) -> None:
        self.operations = operations

    def submit_research_operation(self, request: dict[str, Any]) -> OperationAdapterResult:
        """Admit one of nine typed kinds without executing it; bind its idempotency key."""
        decoded, issues = decode_request(request, OperationSubmission)
        if decoded is None:
            return OperationAdapterResult(success=False, issues=issues)
        try:
            value = self.operations.submit(decoded)
        except OperationInputError as error:
            return _rejected(error)
        return OperationAdapterResult(success=True, value=value)

    def execute_research_operation(self, request: dict[str, Any]) -> OperationAdapterResult:
        """Execute a queued operation once; running/terminal work is never restarted."""
        decoded, issues = decode_request(request, OperationReference)
        if decoded is None:
            return OperationAdapterResult(success=False, issues=issues)
        try:
            value = self.operations.execute(decoded.operation_id)
        except OperationInputError as error:
            return _rejected(error)
        if value.state is OperationState.FAILED:
            if value.failure is FailureClass.INTERNAL_FAILURE:
                raise RuntimeError("Research operation previously failed internally")
            return OperationAdapterResult(success=False, issues=service_issue())
        return OperationAdapterResult(success=True, value=value)

    def get_research_operation(self, request: dict[str, Any]) -> OperationAdapterResult:
        """Read an immutable snapshot, result digest and bounded audit chain."""
        decoded, issues = decode_request(request, OperationReference)
        if decoded is None:
            return OperationAdapterResult(success=False, issues=issues)
        try:
            value = self.operations.get(decoded.operation_id)
        except OperationInputError as error:
            return _rejected(error)
        return OperationAdapterResult(success=True, value=value)

    def cancel_research_operation(self, request: dict[str, Any]) -> OperationAdapterResult:
        """Cancel queued work only; synchronous running services cannot be interrupted."""
        decoded, issues = decode_request(request, OperationReference)
        if decoded is None:
            return OperationAdapterResult(success=False, issues=issues)
        try:
            value = self.operations.cancel(decoded.operation_id)
        except OperationInputError as error:
            return _rejected(error)
        return OperationAdapterResult(success=True, value=value)
