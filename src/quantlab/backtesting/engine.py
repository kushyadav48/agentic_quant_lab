"""Deterministic one-position bar replay with a single execution-price boundary."""
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from pydantic import ValidationError
from quantlab.data import Instrument, MarketBar, ValidationOptions, validate_dataset
from quantlab.features import FeatureObservation, FeatureRequest, validate_strategy_features
from quantlab.strategies import ApprovalState, MarketField, MarketOperand, StrategySpecification
from .enums import EvaluationResult as E, PositionSide, SignalAction
from .errors import BacktestCompatibilityError, BacktestInputError, BacktestSignalConflictError
from .evaluation import RuleEvaluator
from .models import BacktestConfig, BacktestResult, ClosedTrade, EquityPoint, Fill, Position, Signal


def _validate_strategy(strategy: StrategySpecification, instrument: Instrument
                       ) -> tuple[StrategySpecification, dict[str, FeatureRequest]]:
    try:
        strategy = StrategySpecification.model_validate(strategy)
    except (ValidationError, TypeError) as exc:
        raise BacktestCompatibilityError(f"invalid strategy/approval contract: {exc}") from exc
    if strategy.state is not ApprovalState.APPROVED:
        raise BacktestCompatibilityError("backtesting requires an APPROVED exact strategy version")
    content = strategy.content
    if content.instruments != (instrument.instrument_id,):
        raise BacktestCompatibilityError("Phase 7 requires exactly the supplied single instrument")
    for field in ("stop_loss", "take_profit", "session", "sizing_reference"):
        if getattr(content, field) is not None:
            raise BacktestCompatibilityError(f"Phase 7 does not support {field}")
    for side in (content.long, content.short):
        if side is None:
            continue
        for group in (side.entry, side.exit):
            if group is None:
                continue
            for rule in group.rules:
                for operand in (rule.left, rule.right):
                    if isinstance(operand, MarketOperand) and operand.field in (MarketField.BID, MarketField.ASK):
                        raise BacktestCompatibilityError("BID/ASK operands cannot be resolved from OHLC bars")
    try:
        requests = validate_strategy_features(strategy)
    except ValueError as exc:
        raise BacktestCompatibilityError(f"unsupported strategy features: {exc}") from exc
    return strategy, {r.feature_id: r for r in requests}


def _validate_inputs(bars: Iterable[MarketBar], features: Iterable[FeatureObservation],
                     strategy: StrategySpecification, instrument: Instrument,
                     requests: Mapping[str, FeatureRequest]
                     ) -> tuple[tuple[MarketBar, ...], tuple[FeatureObservation, ...]]:
    records = tuple(bars)
    if not records:
        raise BacktestInputError("backtesting requires at least one MarketBar")
    if any(type(b) is not MarketBar for b in records):
        raise BacktestInputError("backtest inputs must be canonical MarketBar objects")
    report = validate_dataset(records, instrument=instrument,
        options=ValidationOptions(homogeneous_source=False))
    if not report.valid:
        raise BacktestInputError(f"invalid bar series: {report.errors}")
    records = tuple(MarketBar.model_validate(b) for b in records)
    if records[0].timeframe is not strategy.content.timeframe:
        raise BacktestCompatibilityError("strategy timeframe must match bar series")
    observations: list[FeatureObservation] = []
    seen: set[tuple[str, datetime]] = set()
    previous: datetime | None = None
    by_end = {b.end_time: b for b in records}
    delayed_bars = tuple(b for b in records if b.available_at > b.end_time)
    for candidate in features:
        if type(candidate) is not FeatureObservation:
            raise BacktestInputError("features must be canonical FeatureObservation objects")
        try:
            observation = FeatureObservation.model_validate(candidate)
        except ValueError as exc:
            raise BacktestInputError(f"malformed feature observation: {exc}") from exc
        key = (observation.feature_id, observation.timestamp)
        if key in seen:
            raise BacktestInputError(f"duplicate feature observation: {key}")
        seen.add(key)
        if previous is not None and observation.timestamp < previous:
            raise BacktestInputError("feature timestamps must be chronological")
        previous = observation.timestamp
        if observation.instrument_id != instrument.instrument_id:
            raise BacktestInputError("feature instrument mismatch")
        if observation.timeframe is not records[0].timeframe:
            raise BacktestInputError("feature timeframe mismatch")
        if observation.price_type is not records[0].price_type:
            raise BacktestInputError("feature price_type mismatch")
        request = requests.get(observation.feature_id)
        if request is None:
            raise BacktestInputError(f"undeclared feature: {observation.feature_id}")
        if (observation.implementation_id != (request.implementation_id or request.feature_id)
                or observation.parameters != request.parameters):
            raise BacktestInputError(f"feature declaration metadata mismatch: {observation.feature_id}")
        ending_bar = by_end.get(observation.timestamp)
        if ending_bar is not None and observation.input_start > ending_bar.start_time:
            raise BacktestInputError("feature input_start must include its ending bar")
        # On-time bars are already covered by observation.available_at >= timestamp.
        dependencies = (b for b in delayed_bars if observation.input_start <= b.start_time
                        and b.end_time <= observation.timestamp)
        if any(b.available_at > observation.available_at for b in dependencies):
            raise BacktestInputError("feature availability precedes a contributing bar")
        observations.append(observation)
    return records, tuple(observations)


def _next_open_fill(signal: Signal, bar: MarketBar, quantity: Decimal) -> Fill:
    """Phase 8 can extend this boundary with explicit prices and costs."""
    return Fill(action=signal.action, signal_time=signal.signal_time,
                execution_time=bar.start_time, execution_price=bar.open, quantity=quantity)


def _pnl(position: Position, price: Decimal) -> Decimal:
    change = (price - position.entry_price if position.side is PositionSide.LONG
              else position.entry_price - price)
    return change * position.quantity


def run_backtest(strategy: StrategySpecification, bars: Iterable[MarketBar],
                 features: Iterable[FeatureObservation] = (), *,
                 instrument: Instrument, config: BacktestConfig) -> BacktestResult:
    """Replay approved single-instrument intent; never approve or mutate inputs.

    A complete bar's availability gates its close decision. Fills at the following
    input bar's open are a declared zero-cost execution assumption, independent of
    that complete bar's later publication. Marks are retrospective close prices.
    """
    try:
        instrument = Instrument.model_validate(instrument)
        config = BacktestConfig.model_validate(config)
    except (ValueError, TypeError) as exc:
        raise BacktestInputError(f"invalid instrument/config: {exc}") from exc
    strategy, requests = _validate_strategy(strategy, instrument)
    # Exact divisibility, independent of Decimal precision/rounding.
    numerator, denominator = config.quantity.as_integer_ratio()
    step_numerator, step_denominator = instrument.quantity_increment.as_integer_ratio()
    if (numerator * step_denominator) % (denominator * step_numerator):
        raise BacktestInputError("quantity must be a multiple of instrument.quantity_increment")
    records, observations = _validate_inputs(bars, features, strategy, instrument, requests)
    evaluator = RuleEvaluator(strategy, records, observations)
    signals: list[Signal] = []
    fills: list[Fill] = []
    trades: list[ClosedTrade] = []
    curve: list[EquityPoint] = []
    position: Position | None = None
    pending: Signal | None = None
    realized = unrealized = Decimal("0")
    digest = strategy.content_digest
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        for index, bar in enumerate(records):
            if pending is not None:
                fill = _next_open_fill(pending, bar, config.quantity)
                fills.append(fill)
                if fill.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                    position = Position(
                        side=PositionSide.LONG if fill.action is SignalAction.ENTER_LONG else PositionSide.SHORT,
                        quantity=fill.quantity, entry_signal_time=fill.signal_time,
                        entry_time=fill.execution_time, entry_price=fill.execution_price)
                else:
                    assert position is not None
                    gross = _pnl(position, fill.execution_price)
                    trades.append(ClosedTrade(**position.model_dump(), exit_signal_time=fill.signal_time,
                        exit_time=fill.execution_time, exit_price=fill.execution_price, gross_pnl=gross))
                    realized += gross
                    position = None
                pending = None
            action = None
            if position is None:
                long = strategy.content.long
                short = strategy.content.short
                enter_long = long is not None and evaluator.evaluate(long.entry, index) is E.TRUE
                enter_short = short is not None and evaluator.evaluate(short.entry, index) is E.TRUE
                if enter_long and enter_short:
                    raise BacktestSignalConflictError(f"simultaneous long/short entries at {bar.end_time.isoformat()}")
                if enter_long:
                    action = SignalAction.ENTER_LONG
                elif enter_short:
                    action = SignalAction.ENTER_SHORT
            else:
                side = strategy.content.long if position.side is PositionSide.LONG else strategy.content.short
                assert side is not None
                if side.exit is not None and evaluator.evaluate(side.exit, index) is E.TRUE:
                    action = SignalAction.EXIT_LONG if position.side is PositionSide.LONG else SignalAction.EXIT_SHORT
            if action is not None:
                pending = Signal(strategy_id=strategy.strategy_id, strategy_version=strategy.version,
                    strategy_digest=digest, instrument_id=bar.instrument_id, action=action,
                    signal_time=bar.end_time, source_bar_start=bar.start_time, source_bar_end=bar.end_time)
                signals.append(pending)
            unrealized = _pnl(position, bar.close) if position is not None else Decimal("0")
            curve.append(EquityPoint(timestamp=bar.end_time, realized_pnl=realized,
                unrealized_pnl=unrealized, equity=config.initial_capital + realized + unrealized))
    return BacktestResult(strategy_id=strategy.strategy_id, strategy_version=strategy.version,
        strategy_content_digest=digest, instrument_id=instrument.instrument_id,
        timeframe=records[0].timeframe, price_type=records[0].price_type,
        initial_capital=config.initial_capital, quantity=config.quantity,
        signals=tuple(signals), fills=tuple(fills), closed_trades=tuple(trades),
        open_position=position, equity_curve=tuple(curve), realized_pnl=realized,
        unrealized_pnl=unrealized, final_equity=curve[-1].equity)
