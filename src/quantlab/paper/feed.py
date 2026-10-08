"""Pure recorded-delivery boundary and logical clock; no transport or wall time."""
from .errors import PaperInputError
from .session_models import ClockState, FeedState


def advance_clock(clock, item):
    if item.sequence <= clock.sequence or item.timestamp < clock.timestamp:
        raise PaperInputError("logical sequence/time cannot go backwards")
    return ClockState(sequence=item.sequence, timestamp=item.timestamp)


def feed_reason(state, timestamp, policy):
    if state.status == "interrupted":
        return "interrupted"
    if state.status == "exhausted":
        return "exhausted"
    if state.last_market_at is None:
        return "missing"
    return "stale" if timestamp - state.last_market_at > policy.maximum_age else "fresh"


def accept_delivery(state, event, policy):
    if state.status == "exhausted":
        raise PaperInputError("exhausted feed accepts only exact retries")
    if state.last_delivery is not None and event.delivered_at < state.last_delivery:
        raise PaperInputError("recorded delivery order cannot go backwards")
    status, market = state.status, state.last_market_at
    if event.kind == "interruption":
        if status != "ready":
            raise PaperInputError("feed already interrupted")
        status, market = "interrupted", None
    elif event.kind == "resumption":
        if status != "interrupted":
            raise PaperInputError("feed resumption requires interruption")
        status, market = "ready", None
    elif event.kind == "exhaustion":
        status = "exhausted"
    elif event.observation is not None and status == "ready":
        # Old occurrences may arrive late, but cannot refresh the latest market.
        if market is None or event.occurred_at > market:
            market = event.occurred_at
    candidate = FeedState(status=status, last_event_id=event.event_id,
        last_delivery=event.delivered_at, last_market_at=market, accepted=state.accepted + 1)
    reason = feed_reason(candidate, event.timestamp, policy)
    if event.observation is not None and status == "ready" and event.timestamp - event.occurred_at > policy.maximum_age:
        reason = "stale"
    return candidate.model_copy(update={"reason": reason})
