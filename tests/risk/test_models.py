"""Strict contracts, unchecked input revalidation and reproducible wire records."""
from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext

import pytest
from pydantic import ValidationError
from quantlab.backtesting import BacktestConfig, BacktestInputError, BacktestResult
from quantlab.risk import (
    RiskAction, RiskConfig, RiskContext, RiskDecision, RiskError, RiskInputError,
    RiskReason, RiskSide, evaluate_entry_risk,
)
from tests.backtesting.helpers import CONFIG, D, MINUTE, START, simulate


def context(**updates):
    values = dict(signal_time=START, execution_time=START + MINUTE,
        side=RiskSide.LONG, requested_quantity=D("2"), reference_price=D("100"),
        current_equity=D("1000"), running_peak_equity=D("1000"))
    values.update(updates)
    return RiskContext(**values)


FIELDS = tuple(RiskConfig.model_fields)


def test_defaults_are_disabled_and_zero_minimum_is_meaningful():
    assert all(getattr(RiskConfig(), field) is None for field in FIELDS)
    assert evaluate_entry_risk(context(), RiskConfig()).action is RiskAction.ALLOW
    assert RiskConfig(minimum_equity=D("0")).minimum_equity == 0


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [D("-1"), D("NaN"), D("sNaN"), D("Infinity"),
    D("-Infinity"), 1.0, 1, True, "1"])
def test_config_strict_finite_decimal_limits(field, value):
    with pytest.raises(ValidationError):
        RiskConfig(**{field: value})


@pytest.mark.parametrize("field", tuple(f for f in FIELDS if f != "minimum_equity"))
def test_zero_is_not_a_disabled_positive_limit(field):
    with pytest.raises(ValidationError):
        RiskConfig(**{field: D("0")})
    assert getattr(RiskConfig(**{field: None}), field) is None


@pytest.mark.parametrize("field", ["max_equity_fraction", "max_drawdown_fraction"])
def test_fraction_bounds_are_positive_through_one(field):
    assert getattr(RiskConfig(**{field: D("1")}), field) == 1
    with pytest.raises(ValidationError):
        RiskConfig(**{field: D("1.00001")})


@pytest.mark.parametrize("field", ["max_capital_at_risk_per_trade", "daily_loss_limit", "unknown"])
def test_unsupported_controls_are_not_silently_accepted(field):
    with pytest.raises(ValidationError):
        RiskConfig(**{field: D("1")})


@pytest.mark.parametrize("field", ["requested_quantity", "reference_price", "running_peak_equity", "current_equity"])
@pytest.mark.parametrize("value", [1.0, True, D("NaN"), D("Infinity")])
def test_context_strict_financial_values(field, value):
    with pytest.raises(ValidationError):
        RiskContext.model_validate(context().model_copy(update={field: value}))


@pytest.mark.parametrize("updates", [
    {"requested_quantity": D("0")}, {"reference_price": D("0")},
    {"running_peak_equity": D("0")}, {"running_peak_equity": D("999")},
    {"side": "long"}, {"signal_time": START.replace(tzinfo=None)},
    {"execution_time": START - MINUTE},
])
def test_context_coherence_and_strict_enums(updates):
    with pytest.raises(ValidationError):
        RiskContext.model_validate(context().model_copy(update=updates))


@pytest.mark.parametrize("updates", [
    {"approved_quantity": D("1")}, {"approved_quantity": 2.0},
    {"reasons": (RiskReason.MAX_POSITION_QUANTITY,)},
    {"action": RiskAction.REJECT}, {"action": "allow"},
])
def test_allow_cannot_represent_partial_or_unexplained_decisions(updates):
    allowed = evaluate_entry_risk(context(), RiskConfig())
    with pytest.raises(ValidationError):
        RiskDecision.model_validate(allowed.model_copy(update=updates))


@pytest.mark.parametrize("updates", [
    {"approved_quantity": D("2")}, {"reasons": ()},
    {"reasons": (RiskReason.MAX_POSITION_QUANTITY,) * 2},
])
def test_reject_requires_zero_quantity_and_unique_reasons(updates):
    rejected = evaluate_entry_risk(context(), RiskConfig(max_position_quantity=D("1")))
    with pytest.raises(ValidationError):
        RiskDecision.model_validate(rejected.model_copy(update=updates))


@pytest.mark.parametrize("value", [D("0"), D("-100")])
def test_nonpositive_equity_is_valid_and_defaults_stay_unrestricted(value):
    ctx = context().model_copy(update={"current_equity": value})
    assert evaluate_entry_risk(ctx, RiskConfig()).action is RiskAction.ALLOW


@pytest.mark.parametrize("field,value", [("max_position_quantity", 1.0),
    ("max_notional_exposure", D("NaN")), ("max_drawdown_fraction", D("2")),
    ("minimum_equity", D("-1"))])
def test_public_boundaries_revalidate_nested_unchecked_config(field, value):
    malformed = RiskConfig().model_copy(update={field: value})
    with pytest.raises(RiskInputError):
        evaluate_entry_risk(context(), malformed)
    forged = CONFIG.model_copy(update={"risk": malformed})
    with pytest.raises(BacktestInputError):
        simulate(config=forged)
    with pytest.raises(ValidationError):
        BacktestConfig.model_validate(forged)


def test_unchecked_context_and_invalid_boundary_types_fail_explicitly():
    with pytest.raises(RiskError):
        evaluate_entry_risk(context().model_copy(update={"requested_quantity": True}), RiskConfig())
    with pytest.raises(RiskInputError):
        evaluate_entry_risk(context(), None)
    with pytest.raises(RiskInputError):
        evaluate_entry_risk(None, RiskConfig())
    constructed = RiskConfig.model_construct(max_position_quantity=D("-1"))
    with pytest.raises(BacktestInputError):
        simulate(config=CONFIG.model_copy(update={"risk": constructed}))


def test_all_models_frozen_extra_forbidden_json_round_trip_and_inputs_unchanged():
    cfg = RiskConfig(max_position_quantity=D("1.00"), max_notional_exposure=D("200.00"),
        max_equity_fraction=D("0.20"), minimum_equity=D("0"), max_drawdown_fraction=D("0.10"))
    ctx = context()
    before = cfg.model_dump_json(), ctx.model_dump_json()
    decision = evaluate_entry_risk(ctx, cfg)
    assert (cfg.model_dump_json(), ctx.model_dump_json()) == before
    result = simulate(config=CONFIG.model_copy(update={"risk": cfg}))
    models = (cfg, ctx, decision, result, *result.risk_decisions)
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, next(iter(type(model).model_fields)), None)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": None})
    assert BacktestResult.model_validate_json(result.model_dump_json()) == result


def test_hostile_caller_context_cannot_change_limits_models_or_replay():
    ctx = context().model_copy(update={"reference_price": D("100.00000000000000000000000000000000001")})
    cfg = RiskConfig(max_notional_exposure=D("200"), max_drawdown_fraction=D("0.10"))
    expected = evaluate_entry_risk(ctx, cfg)
    result = simulate(config=CONFIG.model_copy(update={"risk": cfg}))
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = caller.traps[Rounded] = True
        flags = caller.flags.copy()
        assert evaluate_entry_risk(ctx, cfg) == expected
        assert RiskDecision.model_validate_json(expected.model_dump_json()) == expected
        assert simulate(config=CONFIG.model_copy(update={"risk": cfg})) == result
        assert caller.prec == 2 and caller.rounding == ROUND_DOWN
        assert caller.traps[Inexact] and caller.traps[Rounded] and caller.flags == flags
