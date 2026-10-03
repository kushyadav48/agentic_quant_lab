"""Explicit approved parameter variants; no overrides, approvals or ranking."""
from collections.abc import Iterable
from decimal import DecimalException, localcontext

from quantlab._decimal import deterministic_context
from quantlab.analytics import AnalyticsConfig
from quantlab.backtesting import BacktestConfig
from quantlab.data import Instrument, MarketBar
from quantlab.features import FeatureObservation
from .errors import ResearchValidationCompatibilityError, ResearchValidationInputError
from .models import RobustnessCandidate, RobustnessCandidateResult, RobustnessReport, ValidationWindow
from .runner import _prepare, _raise_boundary, _segment
from .splits import _metadata
from ._summary import summarize


def _structure(strategy):
    content = strategy.content.model_dump()
    # Revision provenance may change. Everything else except declared defaults
    # must match, including parameter types/bounds and feature arguments.
    content.pop("provenance")
    for parameter in content["parameters"]:
        parameter.pop("default")
    return content


def run_parameter_robustness(candidates: Iterable[RobustnessCandidate],
        bars: Iterable[MarketBar], features: Iterable[FeatureObservation] = (), *,
        baseline_candidate_id: str, window: ValidationWindow, instrument: Instrument,
        config: BacktestConfig, analytics_config: AnalyticsConfig | None = None) -> RobustnessReport:
    """Preserve explicit candidate order and separately approved variant identity.

    Variants change only declared defaults and revision provenance; types, bounds,
    feature definitions and rule structure are fixed. Baseline is explicit. Every
    candidate uses the same window, initial capital, quantity, costs and analytics.
    """
    with localcontext(deterministic_context()):
        try:
            variants = tuple(RobustnessCandidate.model_validate(c) for c in candidates)
            ids = tuple(c.candidate_id for c in variants)
            if not variants or len(set(ids)) != len(ids):
                raise ResearchValidationInputError("requires nonempty candidates with unique identifiers")
            if baseline_candidate_id not in ids:
                raise ResearchValidationInputError("baseline_candidate_id must explicitly identify a candidate")
            baseline = variants[ids.index(baseline_candidate_id)].strategy
            structure = _structure(baseline)
            if any(c.strategy.strategy_id != baseline.strategy_id or _structure(c.strategy) != structure
                   for c in variants):
                raise ResearchValidationCompatibilityError("variants must share strategy identity/structure and parameter declarations")
            records, observations = tuple(bars), tuple(features)
            window = ValidationWindow.model_validate(window)
            results = []
            for candidate in variants:
                strategy, validated, prepared, instrument, config, analytics_config = _prepare(
                    candidate.strategy, records, observations, instrument, config, analytics_config)
                metadata = _metadata(validated, window)
                results.append(RobustnessCandidateResult(candidate=candidate,
                    evaluation=_segment(strategy, validated, prepared, instrument, config, analytics_config, metadata)))
            return RobustnessReport(baseline_candidate_id=baseline_candidate_id, metadata=metadata,
                backtest_config=config, analytics_config=analytics_config, candidates=tuple(results),
                summary=summarize(tuple(r.evaluation.performance for r in results)))
        except (ValueError, TypeError, DecimalException) as exc:
            _raise_boundary(exc)
