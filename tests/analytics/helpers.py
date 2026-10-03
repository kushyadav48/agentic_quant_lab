"""Hand-calculated equity paths and actual engine trade fixtures."""
from quantlab.backtesting import EquityPoint
from tests.backtesting.helpers import D, MINUTE, START, bars, simulate


def equity(values, capital=D("1000")):
    return tuple(EquityPoint(timestamp=START + (i + 1) * MINUTE,
        equity=D(value), realized_pnl=D("0"), unrealized_pnl=D(value) - capital)
        for i, value in enumerate(values))


def four_trades():
    # Net trades +200, +100, -100, 0. Entry/exit opens at indices 1/2,
    # 3/4, 5/6, 7/8; holding times 1, 2, 3, 4 elapsed minutes.
    series = bars((101, 99, 101, 99, 101, 99, 101, 99, 99),
                  (100, 100, 200, 100, 150, 100, 50, 100, 100))
    offsets = (0, 1, 2, 3, 5, 6, 9, 10, 14)
    series = tuple(b.model_copy(update={"start_time": START + offset * MINUTE,
        "end_time": START + (offset + 1) * MINUTE,
        "available_at": START + (offset + 1) * MINUTE}) for b, offset in zip(series, offsets))
    return simulate(series=series)
