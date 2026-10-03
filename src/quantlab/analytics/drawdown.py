"""Initial-capital high-water marks and observed underwater episodes."""
from decimal import Decimal, DecimalException, localcontext

from quantlab._decimal import deterministic_context
from quantlab.backtesting import EquityPoint
from ._validation import validate_equity
from .errors import AnalyticsInputError
from .models import DrawdownEpisode, DrawdownPoint, DrawdownStatistics


def compute_drawdown_statistics(initial_capital: Decimal,
                                curve: tuple[EquityPoint, ...]) -> DrawdownStatistics:
    """Drawdowns are nonpositive; duration starts at the first underwater mark.

    Initial capital has no timestamp, so an episode below this initial peak has
    peak_time=None. Equal observed peaks refresh peak_time. Trough and maximum
    ties retain the earliest observation. Unrecovered duration ends at the final
    observation, with recovery_time=None.
    """
    try:
        with localcontext(deterministic_context()):
            points = validate_equity(initial_capital, curve)
            peak, peak_time = initial_capital, None
            series, episodes = [], []
            active = None
            maximum_absolute = maximum_percentage = Decimal(0)
            absolute_time = percentage_time = None
            for point in points:
                if point.equity >= peak:
                    if active is not None:
                        episodes.append(DrawdownEpisode(**active,
                            recovery_time=point.timestamp,
                            duration=point.timestamp - active["start_time"]))
                        active = None
                    peak, peak_time = point.equity, point.timestamp
                absolute = point.equity - peak
                percentage = point.equity / peak - 1
                series.append(DrawdownPoint(timestamp=point.timestamp, running_peak=peak,
                    absolute_drawdown=absolute, percentage_drawdown=percentage))
                if absolute < maximum_absolute:
                    maximum_absolute, absolute_time = absolute, point.timestamp
                if percentage < maximum_percentage:
                    maximum_percentage, percentage_time = percentage, point.timestamp
                if absolute < 0:
                    if active is None:
                        active = dict(peak_time=peak_time, peak_equity=peak,
                            start_time=point.timestamp, trough_time=point.timestamp,
                            absolute_drawdown=absolute, percentage_drawdown=percentage)
                    elif absolute < active["absolute_drawdown"]:
                        active.update(trough_time=point.timestamp, absolute_drawdown=absolute,
                                      percentage_drawdown=percentage)
            current = None
            if active is not None:
                current = points[-1].timestamp - active["start_time"]
                episodes.append(DrawdownEpisode(**active, recovery_time=None, duration=current))
            recovered = tuple(e.duration for e in episodes if e.recovery_time is not None)
            return DrawdownStatistics(series=tuple(series), episodes=tuple(episodes),
                maximum_absolute_drawdown=maximum_absolute,
                maximum_drawdown_percentage=maximum_percentage,
                maximum_absolute_drawdown_time=absolute_time,
                maximum_percentage_drawdown_time=percentage_time,
                longest_recovered_drawdown_duration=max(recovered) if recovered else None,
                current_drawdown_duration=current)
    except (ValueError, TypeError, DecimalException) as exc:
        raise AnalyticsInputError(f"invalid drawdown input or calculation at precision 34: {exc}") from exc
