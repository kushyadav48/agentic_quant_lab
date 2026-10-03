"""Chronological observation-count windows; never shuffle, repair or sort bars."""
from collections.abc import Iterable
from decimal import Context, ROUND_HALF_EVEN, localcontext

from quantlab.data import Instrument, MarketBar, ValidationOptions, validate_dataset
from .errors import ResearchValidationInputError
from .models import HoldoutConfig, ValidationWindow, WalkForwardConfig, WalkForwardFold, WalkForwardMode, WindowMetadata


def _validated_bars(bars: Iterable[MarketBar], instrument: Instrument) -> tuple[MarketBar, ...]:
    try:
        instrument = Instrument.model_validate(instrument)
        records = tuple(bars)
        if not records or any(type(b) is not MarketBar for b in records):
            raise ValueError("requires nonempty canonical MarketBar objects")
        report = validate_dataset(records, instrument=instrument,
            options=ValidationOptions(homogeneous_source=False))
        if not report.valid:
            raise ValueError(f"invalid chronological bar series: {report.errors}")
        return tuple(MarketBar.model_validate(b) for b in records)
    except (ValueError, TypeError) as exc:
        raise ResearchValidationInputError(f"invalid validation bars/instrument: {exc}") from exc


def _metadata(records: tuple[MarketBar, ...], window: ValidationWindow) -> WindowMetadata:
    if window.end > len(records):
        raise ResearchValidationInputError("window exceeds supplied bar count")
    return WindowMetadata(window=window, observation_count=window.end-window.start,
        first_decision_close=records[window.start].end_time,
        last_decision_close=records[window.end-1].end_time)


def holdout_windows(bars: Iterable[MarketBar], config: HoldoutConfig, *,
                    instrument: Instrument) -> tuple[WindowMetadata, WindowMetadata]:
    """Audit explicit train/test indices; gaps and unused prefix/suffix are allowed."""
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        try:
            config = HoldoutConfig.model_validate(config)
            records = _validated_bars(bars, instrument)
            return _metadata(records, config.train), _metadata(records, config.test)
        except (ValueError, TypeError) as exc:
            raise ResearchValidationInputError(f"invalid holdout: {exc}") from exc


def walk_forward_folds(bars: Iterable[MarketBar], config: WalkForwardConfig, *,
                       instrument: Instrument) -> tuple[WalkForwardFold, ...]:
    """Emit complete sequential folds only; partial trailing tests are omitted.

    Step may be smaller than test_size (overlap) or greater (gaps). Overlapping
    OOS runs remain independent and descriptive totals count repeated exposures.
    """
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        try:
            config = WalkForwardConfig.model_validate(config)
            records = _validated_bars(bars, instrument)
            if config.train_size + config.test_size > len(records):
                raise ValueError("insufficient bars for one complete fold")
            folds = []
            for index, test_start in enumerate(range(config.train_size,
                    len(records)-config.test_size+1, config.step_size)):
                train_start = 0 if config.mode is WalkForwardMode.EXPANDING else test_start-config.train_size
                folds.append(WalkForwardFold(fold_index=index,
                    train=_metadata(records, ValidationWindow(start=train_start, end=test_start)),
                    test=_metadata(records, ValidationWindow(start=test_start, end=test_start+config.test_size))))
            return tuple(folds)
        except (ValueError, TypeError) as exc:
            raise ResearchValidationInputError(f"invalid walk-forward: {exc}") from exc
