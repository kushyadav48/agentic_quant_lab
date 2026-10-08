import pytest
from quantlab.paper import FillRecord, OrderState, PaperInputError, PaperOrderKernel
from .helpers import START, SECOND, accepted, config, market, submission


def test_later_sequence_at_equal_timestamp_fills():
    kernel = accepted()
    records = kernel.process(market(3))
    fill, = [r for r in records if isinstance(r, FillRecord)]
    assert fill.timestamp == START == fill.execution.execution_time
    assert fill.source.sequence > fill.submission.sequence


def test_pre_submission_observation_delivered_later_does_not_fill():
    kernel = PaperOrderKernel(config())
    kernel.process(market())
    kernel.process(submission(timestamp=START + 2 * SECOND))
    assert kernel.process(market(3, observed=START + SECOND,
        delivered=START + 3 * SECOND)) == ()
    assert kernel.snapshot.state is OrderState.ACCEPTED
    assert any(isinstance(r, FillRecord) for r in kernel.process(market(4,
        observed=START + 4 * SECOND)))


@pytest.mark.parametrize("bad", [
    lambda: market(2),
    lambda: market(3, observed=START - SECOND),
    lambda: market(3).model_copy(update={"timestamp": START - SECOND}),
])
def test_invalid_chronology_is_atomic(bad):
    kernel = accepted()
    before = kernel.snapshot
    with pytest.raises(PaperInputError):
        kernel.process(bad())
    assert kernel.snapshot == before
    kernel.process(market(3))
    assert kernel.snapshot.state is OrderState.FILLED


def test_unchecked_unavailable_and_future_quotes_cannot_change_state():
    kernel = accepted()
    before = kernel.snapshot
    for bad in [
        market(3).model_copy(update={"timestamp": START - SECOND}),
        market(3).model_copy(update={"quote": market(3, observed=START + SECOND).quote}),
        market(3).model_copy(update={"quote": market(3, available=START + SECOND,
            processed=START + SECOND).quote}),
    ]:
        with pytest.raises(PaperInputError):
            kernel.process(bad)
        assert kernel.snapshot == before


def test_explicit_availability_and_delivery_gate_execution_time():
    kernel = accepted()
    event = market(3, observed=START + SECOND, available=START + 2 * SECOND,
        delivered=START + 3 * SECOND, processed=START + 4 * SECOND)
    fill, = [r for r in kernel.process(event) if isinstance(r, FillRecord)]
    assert fill.execution.execution_time == START + 4 * SECOND
    assert fill.source == event
    assert fill.source.quote.timestamp == START + SECOND


def test_future_suffix_does_not_rewrite_records_and_cannot_reenter():
    first, second = accepted(), accepted()
    first.process(market(3))
    second.process(market(3))
    prefix = first.snapshot
    first.process(market(4, observed=START + SECOND, bid="1000", ask="1002"))
    assert first.snapshot.events == second.snapshot.events == prefix.events
    assert first.snapshot.config.flat_equity == prefix.config.flat_equity


def test_submission_requires_delivered_market_and_known_cause():
    kernel = PaperOrderKernel(config())
    with pytest.raises(PaperInputError):
        kernel.process(submission())
    assert kernel.snapshot.last_sequence == 0
    kernel.process(market())
    with pytest.raises(PaperInputError):
        kernel.process(submission(causation_id="future"))
    assert kernel.snapshot.state is None
