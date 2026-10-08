from datetime import timedelta, timezone
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from quantlab.backtesting import ExecutionCostConfig
from quantlab.paper import (
    CancellationRequest, FillRecord, KernelConfig, KernelSnapshot, MarketDelivery,
    OrderSide, OrderState, OrderSubmission, OrderTransition, PaperInputError,
    PaperOrderKernel, stable_id,
)
from .helpers import D, INSTRUMENT, START, SECOND, accepted, cancel, config, market, submission


@pytest.mark.parametrize("field,value", [
    ("sequence", 0), ("sequence", -1), ("sequence", True), ("sequence", "2"),
    ("timestamp", START.replace(tzinfo=None)), ("quantity", 2), ("quantity", 2.0),
    ("quantity", "2"), ("quantity", True), ("quantity", D("0")), ("quantity", D("-1")),
    ("quantity", D("NaN")), ("quantity", D("Infinity")), ("side", "buy"),
    ("command_id", ""), ("command_id", "with space"), ("command_id", "x" * 129),
    ("causation_id", ""), ("order_type", "limit"), ("order_type", "stop"),
    ("time_in_force", "ioc"), ("time_in_force", "fok"), ("kind", "market"),
])
def test_strict_submission_contract(field, value):
    values = submission().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        OrderSubmission(**values)


@pytest.mark.parametrize("extra", ["approval", "risk_decision", "current_equity", "strategy", "callback"])
def test_commands_cannot_override_authority(extra):
    with pytest.raises(ValidationError):
        submission(**{extra: "override"})


@pytest.mark.parametrize("field,value", [
    ("flat_equity", 1000.0), ("flat_equity", D("NaN")),
    ("running_peak_equity", D("0")), ("running_peak_equity", D("999")),
    ("costs", ExecutionCostConfig(spread=D("1"))),
    ("instrument", INSTRUMENT.model_copy(update={"contract_multiplier": D("2")})),
])
def test_config_rejects_unsupported_or_invalid_state(field, value):
    values = dict(session_id="fixture", instrument=INSTRUMENT, flat_equity=D("1000"),
        running_peak_equity=D("1000"), **{field: value}) if field not in (
            "instrument", "flat_equity", "running_peak_equity") else {
        **dict(session_id="fixture", instrument=INSTRUMENT, flat_equity=D("1000"),
            running_peak_equity=D("1000")), field: value}
    with pytest.raises(ValidationError):
        KernelConfig(**values)


@pytest.mark.parametrize("case", ["delivery", "availability", "future"])
def test_market_delivery_semantics(case):
    values = market().model_dump()
    values["quote"] = market().quote
    if case == "delivery":
        values["delivered_at"] = START - SECOND
    elif case == "availability":
        values["quote"] = market(available=START + SECOND, processed=START + SECOND).quote
    else:
        values["quote"] = market(observed=START + SECOND).quote
    with pytest.raises(ValidationError):
        MarketDelivery(**values)


def test_models_are_frozen_and_normalize_utc():
    local = START.astimezone(timezone(timedelta(hours=5, minutes=30)))
    event = market(observed=local)
    assert event.quote.timestamp == START and event.timestamp.tzinfo == timezone.utc
    with pytest.raises(ValidationError):
        event.sequence = 3
    with pytest.raises(ValidationError):
        event.quote.bid = D("1")


def test_all_input_output_and_snapshot_json_roundtrips():
    kernel = accepted()
    records = kernel.process(market(3))
    command = cancel(kernel, 4)
    records += kernel.process(command)
    for model in (config(), market(), submission(), command, *kernel.snapshot.events, kernel.snapshot):
        assert type(model).model_validate_json(model.model_dump_json()) == model
        assert type(model).model_validate_json(model.canonical_json()) == model
    assert KernelSnapshot.model_validate_json(kernel.snapshot.canonical_json()) == kernel.snapshot


def test_event_identity_binds_content_and_schema_has_closed_contracts():
    kernel = accepted()
    event = kernel.snapshot.events[0]
    assert event.event_id == stable_id("paper-event-v1", event.model_dump(exclude={"event_id"}))
    with pytest.raises(ValidationError):
        type(event).model_validate(event.model_copy(update={"timestamp": START + SECOND}))
    schema = OrderSubmission.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["order_type"]["const"] == "market"
    assert schema["properties"]["time_in_force"]["const"] == "gtc"
    assert "risk_decision" not in schema["properties"]


@pytest.mark.parametrize("previous,target,reason", [
    (None, OrderState.FILLED, None),
    (OrderState.FILLED, OrderState.ACCEPTED, None),
    (OrderState.ACCEPTED, OrderState.REJECTED, "risk_rejected"),
    (OrderState.SUBMITTED, OrderState.REJECTED, None),
    (OrderState.ACCEPTED, OrderState.CANCELLED, None),
    (None, OrderState.SUBMITTED, "risk_error"),
])
def test_transition_contract_rejects_illegal_edges(previous, target, reason):
    event = accepted().snapshot.events[0]
    values = event.model_dump(exclude={"event_id"})
    values.update(previous_state=previous, state=target, reason=reason)
    values["event_id"] = stable_id("paper-event-v1", values)
    with pytest.raises(ValidationError):
        OrderTransition(**values)


def test_snapshot_and_nested_unchecked_contracts_are_revalidated():
    kernel = accepted()
    with pytest.raises(ValidationError):
        KernelSnapshot.model_validate(kernel.snapshot.model_copy(update={"state": OrderState.FILLED}))
    bad = market(3).model_copy(update={"quote": market().quote.model_copy(update={"ask": D("1")})})
    before = kernel.snapshot
    with pytest.raises(PaperInputError):
        kernel.process(bad)
    assert kernel.snapshot == before
    with pytest.raises(PaperInputError):
        PaperOrderKernel(config().model_copy(update={"costs": ExecutionCostConfig().model_copy(
            update={"slippage": D("-1")})}))


def test_canonical_decimal_identity_normalizes_equivalent_spellings_without_rounding():
    one = submission(quantity="2.000")
    two = submission(quantity="2")
    assert one.canonical_json() == two.canonical_json()
    assert json.loads(one.canonical_json())["quantity"] == "2"
    assert stable_id("command", one.model_dump()) == stable_id("command", two.model_dump())
