"""Validate existing result envelopes without executing a research service."""
from quantlab.mcp.canonical import canonical_json, digest_json
from quantlab.mcp.models import BacktestExecutionResult, PerformanceAnalysisResult
from quantlab.mcp.operation_models import OperationState
from quantlab.mcp.research_models import (DatasetAdapterResult, HoldoutAdapterResult,
    PredictionAdapterResult, PredictionFeaturesAdapterResult, RobustnessAdapterResult,
    TrainingAdapterResult, WalkForwardAdapterResult)
from quantlab.analytics import PerformanceReport
from quantlab.backtesting import BacktestResult
from quantlab.validation import HoldoutResult, RobustnessReport, WalkForwardReport


RESULTS = {
    "backtest": BacktestExecutionResult,
    "holdout": HoldoutAdapterResult,
    "walk_forward": WalkForwardAdapterResult,
    "robustness": RobustnessAdapterResult,
    "ml_dataset": DatasetAdapterResult,
    "ml_training": TrainingAdapterResult,
    "ml_prediction": PredictionAdapterResult,
    "ml_prediction_features": PredictionFeaturesAdapterResult,
    "performance": PerformanceAnalysisResult,
}


def _binding(result):
    return (result.strategy_id, result.strategy_version, result.strategy_content_digest)


def _segment_binding(segment):
    binding = _binding(segment.backtest)
    if _binding(segment.performance) != binding:
        raise ValueError("research strategy binding differs between execution and performance")
    return binding


def report_bindings(report):
    """Check all retained segments/candidates, not merely the first result."""
    if isinstance(report, (BacktestResult, PerformanceReport)):
        return (_binding(report),)
    if isinstance(report, RobustnessReport):
        bindings = []
        for candidate in report.candidates:
            spec = candidate.candidate.strategy
            binding = (spec.strategy_id, spec.version, spec.content_digest)
            if _segment_binding(candidate.evaluation) != binding:
                raise ValueError("research strategy binding differs from robustness candidate")
            bindings.append(binding)
        return tuple(bindings)
    if isinstance(report, HoldoutResult):
        segments = (report.in_sample, report.out_of_sample)
    elif isinstance(report, WalkForwardReport):
        segments = tuple(s for fold in report.folds for s in (fold.in_sample,fold.out_of_sample))
    else:
        raise ValueError("unsupported research strategy report")
    bindings = tuple(_segment_binding(s) for s in segments)
    if len(set(bindings)) != 1:
        raise ValueError("research strategy binding differs across validation segments")
    return (bindings[0],)


def validate_operation(operation):
    expected = digest_json(canonical_json((operation.kind, operation.request_digest, operation.idempotency_digest)))
    if operation.operation_id != expected:
        raise ValueError("research operation identity does not bind its request and key digests")
    if operation.state is not OperationState.COMPLETED:
        return
    try:
        result = RESULTS[operation.kind.value].model_validate_json(operation.result_json, strict=True)
        if not result.success or canonical_json(result.model_dump(mode="python")) != operation.result_json:
            raise ValueError("noncanonical or unsuccessful completed result")
    except (ValueError, TypeError, RecursionError, KeyError) as exc:
        raise ValueError("invalid or incompatible research result") from exc
    expected_bindings = (report_bindings(result.value) if operation.kind.value in
        ("backtest","performance","holdout","walk_forward","robustness") else ())
    actual_bindings = tuple((s.strategy_id,s.version,s.content_digest) for s in operation.strategies)
    if actual_bindings != expected_bindings:
        raise ValueError("research result strategy differs from declared operation provenance")
