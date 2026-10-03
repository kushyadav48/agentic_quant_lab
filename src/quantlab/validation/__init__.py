"""Deterministic chronological research validation (Phase 10 only)."""
from .errors import ResearchValidationCompatibilityError, ResearchValidationError, ResearchValidationInputError
from .models import (
    DescriptiveSummary, HoldoutConfig, HoldoutResult, RobustnessCandidate,
    RobustnessCandidateResult, RobustnessReport, SegmentResult, ValidationWindow,
    WalkForwardConfig, WalkForwardFold, WalkForwardFoldResult, WalkForwardMode,
    WalkForwardReport, WindowMetadata,
)
from .robustness import run_parameter_robustness
from .runner import run_holdout, run_walk_forward
from .splits import holdout_windows, walk_forward_folds

__all__ = [
    "ResearchValidationError", "ResearchValidationInputError", "ResearchValidationCompatibilityError",
    "DescriptiveSummary", "HoldoutConfig", "HoldoutResult", "RobustnessCandidate",
    "RobustnessCandidateResult", "RobustnessReport", "SegmentResult", "ValidationWindow",
    "WalkForwardConfig", "WalkForwardFold", "WalkForwardFoldResult", "WalkForwardMode",
    "WalkForwardReport", "WindowMetadata", "run_parameter_robustness", "run_holdout",
    "run_walk_forward", "holdout_windows", "walk_forward_folds",
]
