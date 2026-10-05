"""Process-local research metadata, with explicit execution and no worker loop."""
from datetime import datetime, timezone
import json
from threading import Lock

from . import tools
from .canonical import canonical_json, digest_json
from .operation_models import (
    AuditEvent, FailureClass, OperationKind, OperationSnapshot, OperationState,
    OperationSubmission, StrategyBinding, WorkflowBinding,
)

MAX_OPERATIONS = 32
MAX_RESULT_BYTES = 4_194_304


class OperationInputError(ValueError):
    """Fixed operation metadata rejection, never caller-supplied text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("Research operation rejected.")


def _execute(kind: OperationKind, request: dict):
    # Explicit dispatch only. There is no callable registry or user job object.
    if kind is OperationKind.BACKTEST:
        return tools.run_backtest(request)
    if kind is OperationKind.HOLDOUT:
        return tools.run_holdout(request)
    if kind is OperationKind.WALK_FORWARD:
        return tools.run_walk_forward(request)
    if kind is OperationKind.ROBUSTNESS:
        return tools.run_parameter_robustness(request)
    if kind is OperationKind.DATASET:
        return tools.build_ml_dataset(request)
    if kind is OperationKind.TRAINING:
        return tools.train_ml_model(request)
    if kind is OperationKind.PREDICTION:
        return tools.predict_ml_oos(request)
    if kind is OperationKind.PREDICTION_FEATURES:
        return tools.ml_predictions_to_features(request)
    if kind is OperationKind.PERFORMANCE:
        return tools.analyze_performance(request)
    raise AssertionError("Unsupported research operation")


class ResearchOperations:
    """One trusted namespace per server/application; not a financial ledger.

    At most 32 admitted operations, with no eviction of identities or keys.
    Rebuild the namespace to release it. Admission stores immutable canonical
    text; snapshots and audit records are frozen and contain no raw requests.
    Locking serializes state transitions, not quantitative service execution.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshots: dict[str, OperationSnapshot] = {}
        self._requests: dict[str, str] = {}
        self._keys: dict[str, str] = {}

    def submit(self, submission: OperationSubmission, *,
               workflow: WorkflowBinding | None = None) -> OperationSnapshot:
        submission = OperationSubmission.model_validate(submission)
        if workflow is not None:
            workflow = WorkflowBinding.model_validate(workflow)
        operation = submission.operation
        kind = OperationKind(operation.kind)
        wire = canonical_json(operation.model_dump(mode="python"))
        request_digest = digest_json(canonical_json({"operation": operation.model_dump(mode="python"),
            "workflow": None if workflow is None else workflow.model_dump(mode="python")}))
        key_digest = digest_json(canonical_json(submission.idempotency_key))
        operation_id = digest_json(canonical_json((kind, request_digest, key_digest)))
        strategies = ((operation.strategy,) if hasattr(operation, "strategy") else
                      tuple(candidate.strategy for candidate in operation.candidates)
                      if hasattr(operation, "candidates") else ())
        bindings = tuple(StrategyBinding(strategy_id=s.strategy_id, version=s.version,
                                        content_digest=s.content_digest) for s in strategies)
        if kind is OperationKind.PERFORMANCE:
            result = operation.result
            bindings = (StrategyBinding(strategy_id=result.strategy_id,
                version=result.strategy_version, content_digest=result.strategy_content_digest),)
        with self._lock:
            existing = self._keys.get(key_digest)
            if existing is not None:
                snapshot = self._snapshots[existing]
                if snapshot.kind is not kind or snapshot.request_digest != request_digest:
                    raise OperationInputError("idempotency_conflict")
                return snapshot
            if len(self._snapshots) >= MAX_OPERATIONS:
                raise OperationInputError("operation_capacity")
            event = AuditEvent(sequence=1, timestamp=datetime.now(timezone.utc),
                operation_id=operation_id, kind=kind, request_digest=request_digest,
                idempotency_digest=key_digest, strategies=bindings, workflow=workflow,
                previous_state=None, state=OperationState.QUEUED)
            snapshot = OperationSnapshot(operation_id=operation_id, kind=kind,
                request_digest=request_digest, idempotency_digest=key_digest,
                strategies=bindings, workflow=workflow, state=OperationState.QUEUED, audit=(event,))
            self._keys[key_digest] = operation_id
            self._requests[operation_id] = wire
            self._snapshots[operation_id] = snapshot
            return snapshot

    def _get(self, operation_id: str) -> OperationSnapshot:
        snapshot = self._snapshots.get(operation_id)
        if snapshot is None:
            raise OperationInputError("operation_not_found")
        return snapshot

    def get(self, operation_id: str) -> OperationSnapshot:
        with self._lock:
            return self._get(operation_id)

    def _transition(self, snapshot, state, *, result_json=None, failure=None):
        # Revalidation enforces the entire legal transition/audit chain.
        if (self._get(snapshot.operation_id) != snapshot or snapshot.state in (
                OperationState.COMPLETED, OperationState.FAILED, OperationState.CANCELLED)):
            raise AssertionError("Stale or terminal operation transition")
        result_digest = None if result_json is None else digest_json(result_json)
        values = snapshot.model_dump(mode="python")
        event = AuditEvent(sequence=len(snapshot.audit) + 1, timestamp=datetime.now(timezone.utc),
            **{name: getattr(snapshot, name) for name in (
                "operation_id", "kind", "request_digest", "idempotency_digest", "strategies",
                "workflow")},
            previous_state=snapshot.state, state=state,
            result_digest=result_digest, failure=failure)
        values.update(state=state, result_json=result_json, result_digest=result_digest,
                      failure=failure, audit=(*snapshot.audit, event))
        updated = OperationSnapshot.model_validate(values)
        self._snapshots[snapshot.operation_id] = updated
        if state in (OperationState.COMPLETED, OperationState.FAILED, OperationState.CANCELLED):
            self._requests.pop(snapshot.operation_id, None)
        return updated

    def cancel(self, operation_id: str) -> OperationSnapshot:
        with self._lock:
            snapshot = self._get(operation_id)
            if snapshot.state is OperationState.CANCELLED:
                return snapshot
            if snapshot.state is not OperationState.QUEUED:
                raise OperationInputError("operation_not_cancellable")
            return self._transition(snapshot, OperationState.CANCELLED)

    def execute(self, operation_id: str) -> OperationSnapshot:
        with self._lock:
            snapshot = self._get(operation_id)
            if snapshot.state in (OperationState.COMPLETED, OperationState.FAILED):
                return snapshot
            if snapshot.state is not OperationState.QUEUED:
                raise OperationInputError("operation_not_executable")
            wire = self._requests[operation_id]
            running = self._transition(snapshot, OperationState.RUNNING)
        try:
            request = json.loads(wire)
            del request["kind"]
            result = _execute(running.kind, request)
            # Adapters validate domain results outside their input-error catches.
            if not result.success:
                with self._lock:
                    return self._transition(running, OperationState.FAILED,
                                            failure=FailureClass.DOMAIN_REJECTION)
            result_wire = canonical_json(result.model_dump(mode="python"))
            if len(result_wire.encode("utf-8")) > MAX_RESULT_BYTES:
                raise RuntimeError("Research result exceeds retention bound")
            with self._lock:
                return self._transition(running, OperationState.COMPLETED, result_json=result_wire)
        finally:
            # Any path that did not record a terminal outcome failed internally.
            # Cleanup does not catch or translate exceptions/interrupts: they
            # propagate to the SDK unchanged, without application success.
            with self._lock:
                if self._get(operation_id).state is OperationState.RUNNING:
                    self._transition(running, OperationState.FAILED,
                                     failure=FailureClass.INTERNAL_FAILURE)
