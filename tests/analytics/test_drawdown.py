"""Initial baseline, independent maxima, observed recovery and elapsed time."""
from datetime import timedelta
from decimal import Context, Inexact, ROUND_DOWN, localcontext

import pytest
from quantlab.analytics import AnalyticsInputError, compute_drawdown_statistics
from .helpers import D, MINUTE, equity


def test_first_point_below_initial_capital_is_immediately_underwater():
    curve = equity((900, 800))
    report = compute_drawdown_statistics(D("1000"), curve)
    assert report.series[0].running_peak == D("1000")
    assert report.series[0].absolute_drawdown == D("-100")
    assert report.series[0].percentage_drawdown == D("-0.1")
    episode, = report.episodes
    assert episode.peak_time is None
    assert episode.start_time == curve[0].timestamp
    assert episode.trough_time == curve[1].timestamp
    assert episode.recovery_time is None
    assert episode.duration == report.current_drawdown_duration == MINUTE
    assert report.longest_recovered_drawdown_duration is None


def test_hand_computed_path_and_recovered_episode():
    curve = equity((1000, 1100, 1050, 900, 950, 1100, 1200))
    report = compute_drawdown_statistics(D("1000"), curve)
    assert tuple(p.running_peak for p in report.series) == tuple(map(D, (1000, 1100, 1100, 1100, 1100, 1100, 1200)))
    assert tuple(p.absolute_drawdown for p in report.series) == tuple(map(D, (0, 0, -50, -200, -150, 0, 0)))
    assert tuple(p.percentage_drawdown for p in report.series) == (
        D("0"), D("0"), D("-0.0454545454545454545454545454545455"),
        D("-0.1818181818181818181818181818181818"),
        D("-0.1363636363636363636363636363636364"), D("0"), D("0"))
    assert report.maximum_absolute_drawdown == D("-200")
    assert report.maximum_drawdown_percentage == D("-0.1818181818181818181818181818181818")
    assert report.maximum_absolute_drawdown_time == report.maximum_percentage_drawdown_time == curve[3].timestamp
    episode, = report.episodes
    assert episode.peak_time == curve[1].timestamp and episode.peak_equity == D("1100")
    assert episode.start_time == curve[2].timestamp and episode.trough_time == curve[3].timestamp
    assert episode.recovery_time == curve[5].timestamp
    assert episode.duration == report.longest_recovered_drawdown_duration == 3 * MINUTE
    assert report.current_drawdown_duration is None


def test_maximum_amount_and_percentage_can_have_different_troughs():
    curve = equity((500, 1000, 2000, 1400))
    report = compute_drawdown_statistics(D("1000"), curve)
    assert report.maximum_absolute_drawdown == D("-600")
    assert report.maximum_absolute_drawdown_time == curve[3].timestamp
    assert report.maximum_drawdown_percentage == D("-0.5")
    assert report.maximum_percentage_drawdown_time == curve[0].timestamp
    assert report.episodes[0].recovery_time == curve[1].timestamp
    assert report.episodes[1].recovery_time is None
    assert report.current_drawdown_duration == timedelta(0)


def test_equal_peaks_refresh_time_and_trough_ties_keep_first():
    curve = equity((1100, 1100, 900, 900, 1200, 1100, 1200))
    report = compute_drawdown_statistics(D("1000"), curve)
    first, second = report.episodes
    assert first.peak_time == curve[1].timestamp
    assert first.trough_time == curve[2].timestamp
    assert first.recovery_time == curve[4].timestamp  # exceeds the old peak
    assert second.recovery_time == curve[6].timestamp  # equals the old peak
    assert report.maximum_absolute_drawdown_time == curve[2].timestamp
    assert report.longest_recovered_drawdown_duration == 2 * MINUTE


@pytest.mark.parametrize("values", [(), (1000,), (1000, 1100, 1200)])
def test_no_drawdown_has_zero_maxima_and_no_invented_episode(values):
    report = compute_drawdown_statistics(D("1000"), equity(values))
    assert report.maximum_absolute_drawdown == report.maximum_drawdown_percentage == 0
    assert report.maximum_absolute_drawdown_time is report.maximum_percentage_drawdown_time is None
    assert report.episodes == ()
    assert report.longest_recovered_drawdown_duration is report.current_drawdown_duration is None


def test_negative_equity_drawdown_can_exceed_one_hundred_percent_and_context_isolated():
    curve = equity((900, -200, 0))
    expected = compute_drawdown_statistics(D("1000"), curve)
    assert expected.maximum_absolute_drawdown == D("-1200")
    assert expected.maximum_drawdown_percentage == D("-1.2")
    assert expected.episodes[0].recovery_time is None
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = True
        assert compute_drawdown_statistics(D("1000"), curve) == expected


@pytest.mark.parametrize("change", ["duplicate", "ordering", "equity", "float", "naive", "mapping", "list"])
def test_public_equity_boundary_rejects_malformed_input(change):
    curve = equity((1000, 1100))
    if change == "duplicate": curve = (curve[0], curve[0])
    elif change == "ordering": curve = tuple(reversed(curve))
    elif change == "mapping": curve = (curve[0].model_dump(),)
    elif change == "list": curve = list(curve)
    else:
        value = {"equity": {"equity": D("999")}, "float": {"equity": 1000.0},
                 "naive": {"timestamp": curve[0].timestamp.replace(tzinfo=None)}}[change]
        curve = (curve[0].model_copy(update=value),)
    with pytest.raises(AnalyticsInputError):
        compute_drawdown_statistics(D("1000"), curve)
