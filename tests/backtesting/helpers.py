"""Tiny approved strategies and exact Decimal bars for hand verification."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantlab.backtesting import BacktestConfig, run_backtest
from quantlab.data import AssetClass, Instrument, MarketBar, PriceType, Timeframe, TradingCalendar
from quantlab.features import compute_features, validate_strategy_features
from quantlab.strategies import (
    ApprovalRecord, Comparison, ConstantOperand, Direction, FeatureReference, FeatureArgument,
    FeatureType, MarketField, MarketOperand, Origin, Provenance, Rule, RuleGroup,
    SideRules, StrategyContent, StrategySpecification,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
INSTRUMENT = Instrument(instrument_id="research:TEST", symbol="TEST", asset_class=AssetClass.EQUITY,
    quote_currency="USD", tick_size=D("0.01"), quantity_increment=D("1"),
    calendar=TradingCalendar(calendar_id="fixture", timezone="UTC"))
CONFIG = BacktestConfig(initial_capital=D("1000"), quantity=D("2"))


def bars(closes=(101, 110, 99, 90), opens=None):
    opens = closes if opens is None else opens
    return tuple(MarketBar(instrument_id=INSTRUMENT.instrument_id, source_id="synthetic",
        dataset_id="fixture", timeframe=Timeframe.M1, price_type=PriceType.TRADE,
        start_time=START+i*MINUTE, end_time=START+(i+1)*MINUTE, available_at=START+(i+1)*MINUTE,
        open=D(o), high=max(D(o), D(c)), low=min(D(o), D(c)), close=D(c))
        for i, (o, c) in enumerate(zip(opens, closes)))


def rule(comparison=Comparison.GT, left=None, right=None, offset=0):
    return Rule(left=left if left is not None else MarketOperand(field=MarketField.CLOSE, offset=offset),
        comparison=comparison, right=right if right is not None else ConstantOperand(value=D("100")))


def group(*rules, **kwargs):
    return RuleGroup(rules=rules, **kwargs)


def approve(spec):
    validated = spec.mark_validated()
    return validated.approve(ApprovalRecord(strategy_id=validated.strategy_id,
        strategy_version=validated.version, content_digest=validated.content_digest,
        reviewer="user:fixture", reviewed_at=START))


def strategy(direction=Direction.LONG, entry=None, exit=None, no_exit=False, approved=True, **kwargs):
    long = SideRules(entry=entry if entry is not None else group(rule()),
        exit=None if no_exit else (exit if exit is not None else group(rule(Comparison.LT))))
    short = SideRules(entry=entry if entry is not None else group(rule(Comparison.LT)),
        exit=None if no_exit else (exit if exit is not None else group(rule())))
    content = dict(name="Threshold research", instruments=(INSTRUMENT.instrument_id,),
        timeframe=Timeframe.M1, direction=direction, provenance=Provenance(origin=Origin.MANUAL),
        long=long if direction is not Direction.SHORT else None,
        short=short if direction is not Direction.LONG else None)
    content.update(kwargs)
    spec = StrategySpecification(strategy_id="fixture", version=1, content=StrategyContent(**content))
    return approve(spec) if approved else spec


def reference(name="fast", period=2, **kwargs):
    return FeatureReference(feature_id=name, implementation_id="sma", feature_type=FeatureType.INDICATOR,
        parameters=(FeatureArgument(name="period", value=period),), **kwargs)


def observations(spec, series):
    return compute_features(series, validate_strategy_features(spec), instrument=INSTRUMENT)


def simulate(spec=None, series=None, features=(), **kwargs):
    return run_backtest(strategy() if spec is None else spec, bars() if series is None else series,
        features, instrument=kwargs.pop("instrument", INSTRUMENT), config=kwargs.pop("config", CONFIG), **kwargs)
