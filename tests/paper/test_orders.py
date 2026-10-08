import pytest
from quantlab.paper import (
    CancellationOutcome, FillRecord, OrderState, OrderTransition,
    PaperIdentityConflict, PaperInputError, PaperOrderKernel, RiskOutcome,
)
from quantlab.risk import RiskAction, RiskConfig, RiskReason
from .helpers import D, START, SECOND, accepted, cancel, config, market, submission


def test_submission_acceptance_then_single_fill():
    kernel = PaperOrderKernel(config())
    assert kernel.process(market()) == ()
    records = kernel.process(submission())
    assert [r.state for r in records if isinstance(r, OrderTransition)] == [
        OrderState.SUBMITTED, OrderState.ACCEPTED]
    assert [r.decision.action for r in records if isinstance(r, RiskOutcome)] == [RiskAction.ALLOW]
    assert kernel.snapshot.state is OrderState.ACCEPTED
    assert not any(isinstance(r, FillRecord) for r in records)
    filled = kernel.process(market(3))
    assert [r.stage for r in filled if isinstance(r, RiskOutcome)] == ["execution"]
    assert len([r for r in filled if isinstance(r, FillRecord)]) == 1
    assert kernel.snapshot.state is OrderState.FILLED
    assert kernel.snapshot.terminated
    before = kernel.snapshot
    with pytest.raises(PaperInputError):
        kernel.process(submission(4, command_id="new-entry"))
    assert kernel.snapshot == before


def test_cancel_before_execution_and_market_afterwards():
    kernel = accepted()
    records = kernel.process(cancel(kernel))
    assert kernel.snapshot.state is OrderState.CANCELLED
    outcome, = [r for r in records if isinstance(r, CancellationOutcome)]
    assert outcome.cancelled and outcome.reason == "cancelled"
    assert kernel.process(market(4)) == ()
    assert not any(isinstance(r, FillRecord) for r in kernel.snapshot.events)


def test_cancel_after_fill_records_denial_without_reopening():
    kernel = accepted()
    kernel.process(market(3))
    before_fills = tuple(r for r in kernel.snapshot.events if isinstance(r, FillRecord))
    records = kernel.process(cancel(kernel, 4))
    outcome, = records
    assert isinstance(outcome, CancellationOutcome)
    assert not outcome.cancelled and outcome.reason == "terminal_order"
    assert kernel.snapshot.state is OrderState.FILLED
    assert tuple(r for r in kernel.snapshot.events if isinstance(r, FillRecord)) == before_fills


@pytest.mark.parametrize("terminal", ["reject", "cancel"])
def test_other_terminal_cancellation_outcomes(terminal):
    kernel = accepted(cfg=config(risk=RiskConfig(max_position_quantity=D("1")))) if terminal == "reject" else accepted()
    if terminal == "cancel":
        kernel.process(cancel(kernel))
    records = kernel.process(cancel(kernel, 4, command_id="another-cancel"))
    assert records[0].reason == "terminal_order"
    assert not records[0].cancelled


def test_execution_rejection_cancels_accepted_order_and_incurs_no_cost():
    kernel = accepted(cfg=config(risk=RiskConfig(max_notional_exposure=D("204"))))
    records = kernel.process(market(3, bid="102", ask="103"))
    risk, = [r for r in records if isinstance(r, RiskOutcome)]
    assert risk.decision.action is RiskAction.REJECT
    assert risk.decision.reasons == (RiskReason.MAX_NOTIONAL_EXPOSURE,)
    assert kernel.snapshot.state is OrderState.CANCELLED
    assert records[-1].reason == "risk_rejected"
    assert not any(isinstance(r, FillRecord) for r in kernel.snapshot.events)


@pytest.mark.parametrize("stage", ["acceptance", "execution"])
@pytest.mark.parametrize("mode", ["raises", "invalid", "forged"])
def test_risk_errors_fail_closed(monkeypatch, stage, mode):
    from quantlab.paper import orders
    kernel = PaperOrderKernel(config())
    kernel.process(market())
    if stage == "execution":
        kernel.process(submission())
    original = orders.evaluate_entry_risk
    def broken(context, policy):
        if mode == "raises":
            raise RuntimeError("untrusted diagnostic")
        result = original(context, policy)
        return result.model_copy(update={"approved_quantity": D("1")}) if mode == "invalid" else result.model_copy(update={"reference_price": D("1")})
    monkeypatch.setattr(orders, "evaluate_entry_risk", broken)
    records = kernel.process(submission() if stage == "acceptance" else market(3))
    risk, = [r for r in records if isinstance(r, RiskOutcome)]
    assert risk.error == "risk_error" and risk.decision is None
    assert records[-1].reason == "risk_error"
    assert kernel.snapshot.state is (OrderState.REJECTED if stage == "acceptance" else OrderState.CANCELLED)
    assert not any(isinstance(r, FillRecord) for r in kernel.snapshot.events)
    assert "untrusted diagnostic" not in kernel.snapshot.canonical_json()


def test_idempotency_returns_original_records_even_after_later_events():
    kernel = PaperOrderKernel(config())
    q, command = market(), submission()
    assert kernel.process(q) == kernel.process(q) == ()
    records = kernel.process(command)
    count = len(kernel.snapshot.events)
    assert kernel.process(command) == records
    assert len(kernel.snapshot.events) == count
    q2 = market(3)
    fill_records = kernel.process(q2)
    frozen = kernel.snapshot
    assert kernel.process(q2) == fill_records
    assert kernel.process(command) == records
    assert kernel.process(q) == ()
    assert kernel.snapshot == frozen


@pytest.mark.parametrize("kind", ["market", "submit", "cancel"])
def test_identity_reuse_conflicts_atomically(kind):
    kernel = accepted()
    if kind == "market":
        conflicting = market(1, ask="103")
    elif kind == "submit":
        conflicting = submission(quantity="3")
    else:
        command = cancel(kernel)
        kernel.process(command)
        conflicting = command.model_copy(update={"timestamp": START + SECOND})
    before = kernel.snapshot
    with pytest.raises(PaperIdentityConflict):
        kernel.process(conflicting)
    assert kernel.snapshot == before


def test_cancellation_idempotency_and_global_input_identity():
    kernel = accepted()
    command = cancel(kernel)
    result = kernel.process(command)
    before = kernel.snapshot
    assert kernel.process(command) == result
    assert kernel.snapshot == before
    with pytest.raises(PaperIdentityConflict):
        kernel.process(market(4, event_id="submit-1"))


def test_unknown_order_cause_and_second_submission_are_atomic():
    kernel = accepted()
    for bad in [cancel(kernel, order_id="0" * 64), cancel(kernel, causation_id="unknown"),
                submission(3, command_id="second")]:
        before = kernel.snapshot
        with pytest.raises(PaperInputError):
            kernel.process(bad)
        assert kernel.snapshot == before


def test_distinct_equal_quotes_are_retained_and_can_fill():
    kernel = accepted()
    assert kernel.snapshot.market.quote == market(3).quote
    kernel.process(market(3))
    assert kernel.snapshot.last_sequence == 3 and kernel.snapshot.state is OrderState.FILLED


def test_independent_kernels_and_unchecked_input_copies():
    first, second = accepted(), accepted()
    first.process(market(3))
    assert second.snapshot.state is OrderState.ACCEPTED
    before = second.snapshot
    with pytest.raises(PaperInputError):
        second.process(market(3).model_copy(update={"sequence": True}))
    assert second.snapshot == before


def test_reused_identity_with_invalid_payload_is_an_explicit_conflict():
    kernel = accepted()
    before = kernel.snapshot
    with pytest.raises(PaperIdentityConflict):
        kernel.process(submission().model_copy(update={"quantity": D("-1")}))
    assert kernel.snapshot == before


@pytest.mark.parametrize("quantity", ["0.5", "1.5", "2.00000000000000000000000000000000001"])
def test_off_grid_quantity_is_rejected_without_consuming_submission(quantity):
    kernel = PaperOrderKernel(config())
    kernel.process(market())
    before = kernel.snapshot
    with pytest.raises(PaperInputError):
        kernel.process(submission(quantity=quantity))
    assert kernel.snapshot == before
    kernel.process(submission())
    assert kernel.snapshot.state is OrderState.ACCEPTED


def test_quote_instrument_mismatch_does_not_change_market_or_clock():
    kernel = accepted()
    before = kernel.snapshot
    bad = market(3).model_copy(update={"quote": market(3).quote.model_copy(
        update={"instrument_id": "other"})})
    with pytest.raises(PaperInputError):
        kernel.process(bad)
    assert kernel.snapshot == before
