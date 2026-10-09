"""Exact portfolio ownership, attribution, causality and failure regressions."""
from datetime import timedelta
from decimal import Decimal as D, localcontext, Inexact, ROUND_DOWN
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError
from quantlab.backtesting import ExecutionCostConfig
from quantlab.paper import SessionRecord, stable_id
from quantlab.paper.strategy_models import record
from quantlab.portfolio import (EnrollMember, ObserveSession, Portfolio, PortfolioBudgetError,
    PortfolioError, PortfolioIdentityConflict, PortfolioMember, PortfolioSnapshot, RefreshPortfolio, RiskBudget, ValueMember)
from quantlab.strategies import Direction
from tests.backtesting.helpers import MINUTE
from tests.paper.test_sessions import command, control, T
from tests.persistence.test_advanced import q, exit_command
from .helpers import member_owner, portfolio, enroll, observe, execute, entry_inputs


def refresh(p, t=None, **changes):
    body = dict(operation_id=f"refresh:{p.snapshot.sequence+1}", sequence=p.snapshot.sequence+1,
        timestamp=p.snapshot.timestamp if t is None else t)
    body.update(changes)
    item = RefreshPortfolio(**body)
    return item, p.process(item)


def changed_record(r, **changes):
    body = r.model_dump(mode="python", exclude={"record_id"})
    body.update(changes)
    return record(SessionRecord, **body)


def value(p, m, event, **changes):
    body = dict(operation_id=f"value:{m.member_id}:{event.sequence}", member_id=m.member_id,
        source=event.observation, provenance=event.provenance,
        sequence=p.snapshot.sequence+1, timestamp=max(p.snapshot.timestamp, event.timestamp))
    body.update(changes)
    item = ValueMember(**body)
    return item, p.process(item)


def test_empty_portfolio_conserves_capital_and_has_exact_flat_equity():
    p = portfolio()
    s = p.snapshot
    assert s.balance == s.available_funds == s.unallocated_capital == s.equity == D("3000")
    assert s.allocated_capital == s.gross_exposure == s.net_pnl == 0
    assert s.members == s.risk_breaches == p.events == ()


def test_multiple_strategies_exact_equity_fees_reservations_and_no_netting():
    costs = ExecutionCostConfig(commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))
    a, ao = member_owner("a", advanced=True, costs=costs)
    b, bo = member_owner("b", direction=Direction.SHORT, advanced=True, costs=costs)
    p = portfolio()
    enroll(p, b); enroll(p, a)
    assert [v.member.member_id for v in p.snapshot.members] == ["a", "b"]
    assert p.snapshot.allocated_capital == 2000 and p.snapshot.unallocated_capital == 1000
    assert p.snapshot.equity is None and p.snapshot.reported_equity == 3000
    for item_a, item_b in zip(entry_inputs(), entry_inputs(short=True)):
        observe(p, a, ao.process(item_a)); observe(p, b, bo.process(item_b))
        if item_a.sequence == 3:
            assert p.snapshot.reserved_funds == D("406.4")
            assert p.snapshot.balance == p.snapshot.reported_equity == 3000
            assert p.snapshot.available_funds == D("2593.6")
    s = p.snapshot
    assert s.position_collateral == 404 and s.reserved_funds == 0
    assert s.fees_paid == D("2.4") and s.equity == D("2997.6")
    assert s.gross_exposure == 404 and s.net_pnl == D("-2.4")
    for m, owner in ((a, ao), (b, bo)):
        quote = q(6, "110")
        observe(p, m, owner.process(quote))
        value(p, m, quote)
    s = p.snapshot
    assert s.unrealized_pnl == -4 and s.gross_exposure == 444
    assert s.equity == D("2993.6") and s.net_pnl == D("-6.4")
    assert s.balance == D("2997.6") and s.available_funds == D("2593.6")
    assert s.reported_equity == D("2997.6")  # Session owners were not marked.
    assert all(v.account.position.strategy_id == v.member.strategy.strategy_id for v in s.members)
    before_b = bo.snapshot
    observe(p, a, ao.process(exit_command(ao, n=7, timestamp=T+MINUTE)))
    observe(p, a, ao.process(q(8, "110", time=T+MINUTE)))
    assert bo.snapshot == before_b
    assert p.snapshot.realized_pnl == 18 and p.snapshot.unrealized_pnl == -22
    assert p.snapshot.fees_paid == D("3.6") and p.snapshot.equity == D("2992.4")
    assert p.snapshot.gross_exposure == 224


@pytest.mark.parametrize("capital,encumbered,gross,member_capital,member_enc,member_gross", [
    ("1500", "1000", "1000", "1000", "500", "500"),
    ("3000", "900", "1500", "1000", "500", "500"),
    ("3000", "1500", "900", "1000", "500", "500"),
])
def test_competing_members_cannot_overallocate(capital, encumbered, gross, member_capital, member_enc, member_gross):
    p = portfolio(capital=capital, encumbered=encumbered, gross=gross)
    a, _ = member_owner("a", capital=member_capital, encumbered=member_enc, gross=member_gross)
    b, _ = member_owner("b", capital=member_capital, encumbered=member_enc, gross=member_gross)
    enroll(p, a)
    before, events = p.snapshot, p.events
    with pytest.raises(PortfolioBudgetError):
        enroll(p, b)
    assert p.snapshot is before and p.events == events


def test_exact_budget_boundary_and_unused_capital_are_separate():
    p = portfolio(capital="2000", encumbered="1000", gross="1000")
    for name in ("a", "b"):
        m, _ = member_owner(name)
        enroll(p, m)
    assert p.snapshot.allocated_capital == 2000 and p.snapshot.unallocated_capital == 0


@pytest.mark.parametrize("field", ["member_id", "account_id", "session_id"])
def test_duplicate_member_account_or_session_is_not_counted_twice(field):
    p = portfolio()
    a, _ = member_owner("a")
    b, _ = member_owner("b")
    if field == "member_id":
        b = b.model_copy(update={field: "a"})
    else:
        # A valid second membership using the same immutable session/account.
        b = a.model_copy(update={"member_id": "b"})
    enroll(p, a)
    before = p.snapshot
    with pytest.raises(PortfolioIdentityConflict):
        enroll(p, b)
    assert p.snapshot is before


def test_same_exact_strategy_can_own_independent_instances():
    p = portfolio()
    for name in ("a", "b"):
        m, owner = member_owner(name, strategy_id="same")
        enroll(p, m)
        observe(p, m, owner.start(command("start")))
    assert p.snapshot.equity == 3000 and len(p.snapshot.members) == 2


def test_conflicting_strategy_revision_is_rejected_before_publication():
    p = portfolio()
    a, _ = member_owner("a", strategy_id="same")
    b, _ = member_owner("b", strategy_id="same", version=2)
    enroll(p, a)
    before = p.snapshot
    with pytest.raises(PortfolioError):
        enroll(p, b)
    assert p.snapshot is before


@pytest.mark.parametrize("fault", ["approval", "admission", "digest", "risk"])
def test_invalid_members_are_deeply_revalidated(fault):
    p = portfolio()
    m, _ = member_owner()
    if fault == "approval":
        m = m.model_copy(update={"strategy": m.strategy.mark_validated()})
    elif fault == "admission":
        m = m.model_copy(update={"admission": m.admission.model_copy(update={"admitted": False})})
    elif fault == "digest":
        cfg = m.config.strategy.model_copy(update={"strategy_digest": "0"*64})
        m = m.model_copy(update={"config": m.config.model_copy(update={"strategy": cfg})})
    else:
        m = m.model_copy(update={"risk": RiskBudget(maximum_encumbered=D("1001"), maximum_gross_exposure=D("500"))})
    before = p.snapshot
    with pytest.raises(PortfolioError):
        p.process(EnrollMember.model_construct(operation_id="bad", member=m, sequence=1, timestamp=T))
    assert p.snapshot is before and not p.events


@pytest.mark.parametrize("operation_kind", ["enroll", "observe", "refresh"])
def test_exact_retries_precede_clock_and_capacity_checks(operation_kind):
    p = portfolio(maximum_operations=3)
    m, owner = member_owner()
    en, en_result = enroll(p, m)
    obs, obs_result = observe(p, m, owner.start(command("start")))
    ref, ref_result = refresh(p, T+MINUTE)
    item, result = {"enroll": (en, en_result), "observe": (obs, obs_result), "refresh": (ref, ref_result)}[operation_kind]
    before = p.snapshot
    assert p.process(item) is result
    assert p.snapshot is before and len(p.events) == 3
    with pytest.raises(PortfolioIdentityConflict):
        p.process(item.model_copy(update={"timestamp": T+2*MINUTE}))
    with pytest.raises(PortfolioError):
        refresh(p)


def test_observation_requires_membership_and_complete_ordered_session_chain():
    p = portfolio()
    m, owner = member_owner()
    records = [owner.process(x) for x in entry_inputs()]
    with pytest.raises(PortfolioError):
        observe(p, m, records[0])
    enroll(p, m)
    for r in (records[1], records[-1]):
        with pytest.raises(PortfolioError):
            observe(p, m, r)
    observe(p, m, records[0])
    before = p.snapshot
    with pytest.raises(PortfolioError):
        observe(p, m, records[0], operation_id="duplicate-session-record")
    assert p.snapshot is before


@pytest.mark.parametrize("fault", ["session", "config", "future", "chain", "unrecorded_finance", "strategy"])
def test_conflicting_session_state_fails_closed(fault):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    records = [owner.process(x) for x in entry_inputs()]
    if fault == "session":
        bad = changed_record(records[0], session_id="another")
    elif fault == "config":
        bad = changed_record(records[0], config_digest="0"*64)
    elif fault == "future":
        bad = records[1]
        observe(p, m, records[0])
    elif fault == "chain":
        bad = changed_record(records[0], previous_record_id="0"*64)
    elif fault == "unrecorded_finance":
        bad = changed_record(records[0], account=records[-1].account)
    else:
        observe(p, m, records[0])
        decision = records[1].decision
        body = decision.model_dump(exclude={"record_id"})
        body["strategy_id"] = "another"
        bad = changed_record(records[1], decision=record(type(decision), **body))
    before = p.snapshot
    with pytest.raises(PortfolioError):
        observe(p, m, bad, timestamp=T if fault == "future" else max(T, bad.timestamp))
    assert p.snapshot is before


def test_actual_financial_chain_cannot_be_skipped_or_reassigned():
    p = portfolio()
    a, ao = member_owner("a")
    b, bo = member_owner("b")
    enroll(p, a); enroll(p, b)
    records = [ao.process(x) for x in entry_inputs()]
    observe(p, a, records[0]); observe(p, a, records[1])
    bad = changed_record(records[2], financial=())
    before = p.snapshot
    with pytest.raises(PortfolioError):
        observe(p, a, bad)
    with pytest.raises(PortfolioError):
        observe(p, b, records[2])
    assert p.snapshot is before


def test_missing_stale_boundary_and_refresh_do_not_invent_valuation():
    p = portfolio(age=MINUTE)
    m, owner = member_owner(advanced=True)
    enroll(p, m)
    assert p.snapshot.equity is None and p.snapshot.members[0].valuation_status == "missing"
    execute(p, m, owner)
    assert p.snapshot.equity == 3000 and p.snapshot.gross_exposure == 202
    refresh(p, T+2*MINUTE)
    assert p.snapshot.equity == 3000  # Exact age threshold is fresh.
    original = p.snapshot
    refresh(p, T+2*MINUTE+timedelta(microseconds=1))
    s = p.snapshot
    assert s.equity is s.unrealized_pnl is s.net_pnl is s.gross_exposure is None
    assert s.reported_equity == 3000 and s.reported_unrealized_pnl == 0
    assert s.members[0].valuation_status == "stale"
    assert s.risk_breaches == ("portfolio:valuation_unavailable",)
    assert original.equity == 3000
    # A fresh heartbeat cannot refresh an old quote mark.
    observe(p, m, owner.process(control("heartbeat", 6, T+3*MINUTE)))
    assert p.snapshot.equity is None
    quote = q(7, "110", time=T+3*MINUTE)
    observe(p, m, owner.process(quote))
    value(p, m, quote)
    assert p.snapshot.equity == 3018 and p.snapshot.gross_exposure == 220


def test_budget_breaches_report_committed_facts_without_changing_execution():
    p = portfolio(encumbered="100", gross="100")
    m, owner = member_owner(encumbered="100", gross="100")
    enroll(p, m)
    execute(p, m, owner)
    assert owner.snapshot.account.position.quantity == 2
    assert p.snapshot.risk_breaches == ("member:a:encumbered", "member:a:gross_exposure",
        "portfolio:encumbered", "portfolio:gross_exposure")


def test_decimal_hostile_context_and_precise_capital_are_reproducible():
    capital = "1000.00000000000000000000000000000000000001"
    p = portfolio(capital="3000.00000000000000000000000000000000000002")
    m, owner = member_owner(capital=capital)
    with localcontext() as ctx:
        ctx.prec = 2; ctx.rounding = ROUND_DOWN; ctx.traps[Inexact] = True
        enroll(p, m); execute(p, m, owner)
    assert p.snapshot.equity == D("3000.00000000000000000000000000000000000002")
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


def test_snapshots_are_deeply_immutable_and_bad_aggregate_copies_rejected():
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    before = p.snapshot
    with pytest.raises(ValidationError):
        before.members[0].account.balance = D("99999")
    with pytest.raises(ValidationError):
        PortfolioSnapshot.model_validate(before.model_copy(update={"reported_equity": D("99999")}))
    execute(p, m, owner)
    assert before.members[0].account.position is None and before.equity is None
    assert p.snapshot.members[0].member.strategy.approval == m.strategy.approval


@pytest.mark.parametrize("boundary", ["aggregate", "record", "index", "publication_before", "publication_after"])
def test_failure_injection_keeps_state_indexes_and_exact_retry_safe(monkeypatch, boundary):
    import quantlab.portfolio.service as service
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    item = ObserveSession(operation_id="start", member_id=m.member_id, record=owner.start(command("start")),
        sequence=2, timestamp=T)
    before = p._publication
    def fail(*args, **kwargs):
        raise RuntimeError("injected publication failure")
    with monkeypatch.context() as patch:
        if boundary == "aggregate": patch.setattr(service, "aggregate", fail)
        elif boundary == "record": patch.setattr(service, "record", fail)
        elif boundary == "index": patch.setattr(service.RetainedMap, "set", fail)
        elif boundary == "publication_before": patch.setattr(p, "_publish", fail)
        else:
            def publish_then_fail(candidate):
                p._publication = candidate
                fail()
            patch.setattr(p, "_publish", publish_then_fail)
        with pytest.raises(RuntimeError): p.process(item)
    assert p._publication is before
    result = p.process(item)
    assert p.process(item) is result and len(p.events) == 2


def test_reentrant_mutation_denied_at_publication(monkeypatch):
    p = portfolio()
    original = p._publish
    def publish(candidate):
        with pytest.raises(PortfolioError, match="reentrant"):
            p.process(RefreshPortfolio(operation_id="nested", sequence=99, timestamp=T))
        original(candidate)
    monkeypatch.setattr(p, "_publish", publish)
    refresh(p)
    assert len(p.events) == 1


def test_replay_and_json_roundtrip_preserve_order_attribution_and_retry_results():
    from quantlab.portfolio import PortfolioEvent
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner); refresh(p)
    journal = tuple(PortfolioEvent.model_validate_json(e.model_dump_json()) for e in p.events)
    recovered = Portfolio.replay(p.snapshot.config, journal)
    assert recovered.snapshot == p.snapshot and recovered.events == journal
    assert recovered.process(journal[0].operation) == journal[0]
    for bad in (journal[1:], tuple(reversed(journal)), (journal[0], *journal)):
        with pytest.raises(PortfolioError): Portfolio.replay(p.snapshot.config, bad)


def test_growing_history_is_not_traversed_or_serialized_in_hot_path(monkeypatch):
    import quantlab.portfolio.service as service
    p = portfolio()
    for _ in range(128): refresh(p)
    original = p._publication
    def denied(*args, **kwargs): raise AssertionError("history traversal on hot path")
    with monkeypatch.context() as patch:
        patch.setattr(service.History, "__iter__", denied)
        patch.setattr(service.History, "__reversed__", denied)
        patch.setattr(service.PortfolioEvent, "canonical_json", lambda self:
            "{}" if self.operation.sequence > 128 else denied())
        refresh(p)
    assert p._publication.history.previous is original.history
    assert len(p.snapshot.canonical_json()) < 2000 and len(p.events) == 129


@pytest.mark.parametrize("field,value", [("sequence", 0), ("sequence", 1), ("timestamp", T-MINUTE)])
def test_bad_logical_order_is_rejected_without_mutation(field, value):
    p = portfolio()
    refresh(p)
    item = RefreshPortfolio.model_construct(operation_id="bad", sequence=2, timestamp=T)
    before = p.snapshot
    with pytest.raises(PortfolioError): p.process(item.model_copy(update={field: value}))
    assert p.snapshot is before


def test_portfolio_has_no_execution_io_or_agent_authority_dependencies():
    import quantlab.portfolio as public
    assert len(public.__all__) == len(set(public.__all__))
    assert all(hasattr(public, name) for name in public.__all__)
    forbidden = ("quantlab.mcp", "quantlab.orchestration", "quantlab.llm", "quantlab.persistence",
        "quantlab.paper.sessions", "quantlab.paper.accounts", "socket", "sqlite3", "threading", "asyncio")
    for path in (Path(__file__).parents[2]/"src"/"quantlab"/"portfolio").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom): assert not (node.module or "").startswith(forbidden)
            elif isinstance(node, ast.Import): assert not any(a.name.startswith(forbidden) for a in node.names)


@pytest.mark.parametrize("fault", ["instrument", "source", "dataset", "version", "future", "older_quote", "older_delivery"])
def test_incompatible_valuation_cannot_publish(fault):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    quote = q(6, "110", time=T+2*MINUTE)
    item = ValueMember(operation_id="valuation", member_id=m.member_id, source=quote.observation,
        provenance=quote.provenance, sequence=p.snapshot.sequence+1, timestamp=quote.timestamp)
    if fault in ("instrument", "source", "dataset"):
        name = {"instrument": "instrument_id", "source": "source_id", "dataset": "dataset_id"}[fault]
        source = item.source.model_copy(update={"quote": item.source.quote.model_copy(update={name: "another"})})
        item = item.model_copy(update={"source": source})
    elif fault == "version":
        item = item.model_copy(update={"provenance": item.provenance.model_copy(update={"version_digest": "0"*64})})
    elif fault == "future":
        item = item.model_copy(update={"timestamp": T+MINUTE})
    elif fault == "older_quote":
        src = item.source.model_copy(update={"quote": item.source.quote.model_copy(update={
            "timestamp": T, "available_at": T})})
        item = item.model_copy(update={"source": src})
    else:
        src = item.source.model_copy(update={"timestamp": T, "delivered_at": T,
            "quote": item.source.quote.model_copy(update={"timestamp": T, "available_at": T})})
        item = item.model_copy(update={"source": src})
    before = p._publication
    with pytest.raises(PortfolioError): p.process(item)
    assert p._publication is before


@pytest.mark.parametrize("flat", [True, False])
def test_valuation_requires_observed_open_position(flat):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    if flat: observe(p, m, owner.start(command("start")))
    with pytest.raises(PortfolioError): value(p, m, q(6))


def test_valuation_retry_conflict_staleness_and_independent_sequence_domain():
    p = portfolio(age=MINUTE)
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    financial_before = owner.snapshot
    quote = q(1, "110", time=T+MINUTE)  # Portfolio quote sequence is a separate domain.
    item, outcome = value(p, m, quote)
    assert p.snapshot.equity == 3018 and owner.snapshot == financial_before
    assert p.process(item) is outcome
    with pytest.raises(PortfolioIdentityConflict):
        p.process(item.model_copy(update={"timestamp": T+2*MINUTE}))
    with pytest.raises(PortfolioError):
        value(p, m, q(1, "111", time=T+2*MINUTE), operation_id="repeat-source-sequence")
    refresh(p, T+3*MINUTE)
    assert p.snapshot.equity is None and p.snapshot.reported_equity == 3000
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


def test_explicit_stale_quote_retained_without_becoming_authoritative():
    p = portfolio(age=MINUTE)
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    observe(p, m, owner.process(control("heartbeat", 6, T+4*MINUTE)))
    stale_quote = q(7, "110", time=T+4*MINUTE, age=2*MINUTE)
    value(p, m, stale_quote)
    assert p.snapshot.members[0].valuation.quote.timestamp == T+2*MINUTE
    assert p.snapshot.equity is None and p.snapshot.members[0].valuation_status == "stale"


def test_stale_session_status_is_not_refreshed_by_portfolio_quote():
    p = portfolio(age=MINUTE)
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    value(p, m, q(7, "110", time=T+4*MINUTE))
    assert p.snapshot.equity is None


@pytest.mark.parametrize("field", ["equity", "balance", "available_funds", "fees_paid", "reserved_funds", "position_collateral"])
def test_malformed_account_copy_in_session_record_is_revalidated(field):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    r = owner.start(command("start"))
    bad_account = r.account.model_copy(update={field: D("42")})
    bad = r.model_copy(update={"account": bad_account})
    item = ObserveSession.model_construct(operation_id="bad", member_id=m.member_id,
        record=bad, sequence=2, timestamp=T)
    before = p.snapshot
    with pytest.raises(PortfolioError): p.process(item)
    assert p.snapshot is before


def test_member_capacity_and_conflicting_currency_are_explicit():
    p = portfolio(maximum_members=1)
    a, _ = member_owner("a")
    b, _ = member_owner("b")
    enroll(p, a)
    with pytest.raises(PortfolioError): enroll(p, b)
    incompatible = Portfolio(p.snapshot.config.model_copy(update={"denomination": "EUR"}))
    with pytest.raises(PortfolioError): enroll(incompatible, a)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_batch_order_is_explicit_and_member_aggregation_is_order_independent(width):
    members = [member_owner(f"strategy-{i}") for i in range(width)]
    a = portfolio(capital="5000", encumbered="2500", gross="2500")
    b = portfolio(capital="5000", encumbered="2500", gross="2500")
    for m, _ in members: enroll(a, m)
    for m, _ in reversed(members): enroll(b, m)
    for m, owner in members:
        r = owner.start(command("start"))
        observe(a, m, r); observe(b, m, r)
    assert a.snapshot.members == b.snapshot.members
    assert a.snapshot.equity == b.snapshot.equity == 5000
    assert a.snapshot.last_event_id != b.snapshot.last_event_id or width == 1
    assert Portfolio.replay(a.snapshot.config, a.events).snapshot == a.snapshot


@pytest.mark.parametrize("length", [0, 8, 64, 256])
def test_retry_index_update_copies_exactly_64_bounded_nodes(monkeypatch, length):
    import builtins
    import quantlab.paper.history as history
    from quantlab.paper.history import RetainedMap, History
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    for _ in range(length): refresh(p)
    widths = []
    def counted(branch):
        widths.append(len(branch))
        return builtins.dict(branch)
    def denied(*args): raise AssertionError("retained history/index traversal")
    with monkeypatch.context() as patch:
        patch.setattr(history, "dict", counted, raising=False)
        patch.setattr(RetainedMap, "__iter__", denied)
        patch.setattr(History, "__iter__", denied)
        item, result = refresh(p)
        assert p.process(item) is result
    assert len(widths) == 64 and max(widths) <= 16


@pytest.mark.parametrize("boundary", ["serialization", "history", "snapshot"])
def test_additional_prepare_failures_leave_retryable_operation(monkeypatch, boundary):
    import quantlab.portfolio.service as service
    p = portfolio()
    m, _ = member_owner()
    item = EnrollMember(operation_id="enroll", member=m, sequence=1, timestamp=T)
    before = p._publication
    def denied(*args, **kwargs): raise RuntimeError("injected preparation failure")
    with monkeypatch.context() as patch:
        if boundary == "serialization": patch.setattr(service.PortfolioEvent, "canonical_json", denied)
        elif boundary == "history": patch.setattr(service.History, "append", denied)
        else: patch.setattr(service.PortfolioSnapshot, "canonical_json", denied)
        with pytest.raises(RuntimeError): p.process(item)
    assert p._publication is before
    assert p.process(item) == p.process(item)


def test_recovered_durable_paper_records_rebuild_same_portfolio(tmp_path):
    from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy
    from tests.paper.test_sessions import session
    base, owners = session()
    store = SQLitePaperStore(tmp_path/"member.db")
    policy = StoragePolicy(checkpoint_interval=2)
    durable = DurablePaperSession(store, base.config, storage_policy=policy, **owners)
    m = PortfolioMember(member_id="durable", config=base.config, strategy=owners["strategy"],
        admission=base.snapshot.runtime.admission,
        risk=RiskBudget(maximum_encumbered=D("500"), maximum_gross_exposure=D("500")))
    p = portfolio()
    enroll(p, m)
    execute(p, m, durable)
    expected = durable.snapshot
    store.close()
    store = SQLitePaperStore(tmp_path/"member.db")
    recovered = DurablePaperSession.recover(store, base.config, storage_policy=policy, **owners)
    assert recovered.snapshot == expected
    report = portfolio()
    enroll(report, m)
    for r in recovered.records: observe(report, m, r)
    assert report.snapshot == p.snapshot
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot
    assert recovered.operator_required  # Portfolio replay grants no resume authority.
    store.close()


@pytest.mark.parametrize("kind", ["market", "limit", "stop_market", "stop_limit"])
def test_separately_approved_advanced_entry_sessions_retain_authorization(kind):
    from tests.paper.advanced_entry_helpers import session, prefix, quote
    owner, owners = session(kind=kind)
    p = portfolio()
    m = PortfolioMember(member_id="advanced", config=owner.config, strategy=owners["strategy"],
        admission=owner.snapshot.runtime.admission,
        risk=RiskBudget(maximum_encumbered=D("500"), maximum_gross_exposure=D("500")))
    enroll(p, m)
    for item in prefix(): observe(p, m, owner.process(item))
    for n, price in enumerate(("104", "99", "99"), 6):
        observe(p, m, owner.process(quote(n, price)))
    assert owner.snapshot.account.position.quantity == 2
    assert p.snapshot.members[0].member.config.strategy.execution_approval == owner.config.strategy.execution_approval
    assert p.snapshot.balance == D("2997.8") and p.snapshot.fees_paid == D("2.2")
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


@pytest.mark.parametrize("fault", ["input_digest", "input_id", "missing_input", "market_instrument"])
def test_session_record_input_provenance_cannot_be_rewritten(fault):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m)
    r = owner.start(command("start"))
    if fault == "input_digest": bad = changed_record(r, input_digest="0"*64)
    elif fault == "input_id": bad = changed_record(r, input_id="another")
    elif fault == "missing_input": bad = changed_record(r, command=None)
    else:
        observe(p, m, r)
        r = owner.process(entry_inputs()[1])
        event = r.market_event
        obs = event.observation.model_copy(update={"bar": event.observation.bar.model_copy(update={"instrument_id": "another"})})
        event = event.model_copy(update={"observation": obs})
        bad = changed_record(r, market_event=event, input_digest=stable_id("paper-session-input-v1", event))
    before = p.snapshot
    with pytest.raises(PortfolioError): observe(p, m, bad)
    assert p.snapshot is before


@pytest.mark.parametrize("kind", ["heartbeat", "quote"])
def test_unchanged_session_account_preserves_equal_time_explicit_mark(kind):
    p = portfolio()
    m, owner = member_owner(advanced=True)
    enroll(p, m); execute(p, m, owner)
    valuation_item, valuation_result = value(p, m, q(50, "110", time=T+MINUTE))
    before = p.snapshot
    assert before.equity == 3018 and before.gross_exposure == 220
    item = control("heartbeat", 6, T+MINUTE) if kind == "heartbeat" else q(6, "120", time=T+MINUTE)
    observation, result = observe(p, m, owner.process(item))
    assert p.snapshot.members[0].account == before.members[0].account
    assert p.snapshot.members[0].valuation == before.members[0].valuation
    assert p.snapshot.equity == 3018 and p.snapshot.gross_exposure == 220
    assert p.process(observation) is result
    assert p.process(valuation_item) is valuation_result
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


def test_explicit_valuation_sequence_survives_superseding_account_mark():
    p = portfolio()
    m, owner = member_owner(advanced=True)
    enroll(p, m); execute(p, m, owner)
    value(p, m, q(50, "110", time=T+MINUTE))
    observe(p, m, owner.process(exit_command(owner, n=6, quantity=D("1"), timestamp=T+MINUTE)))
    observe(p, m, owner.process(q(7, "111", time=T+2*MINUTE)))
    assert p.snapshot.members[0].account.position.quantity == 1
    assert p.snapshot.members[0].valuation is None  # A newly committed account mark takes precedence.
    before = p._publication
    with pytest.raises(PortfolioError, match="chronology"):
        value(p, m, q(49, "112", time=T+3*MINUTE))
    assert p._publication is before
    value(p, m, q(51, "112", time=T+3*MINUTE))
    assert p.snapshot.equity == 3021 and p.snapshot.gross_exposure == 112
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


def test_new_equal_time_financial_mark_supersedes_explicit_mark_and_keeps_head():
    p = portfolio()
    m, owner = member_owner(advanced=True)
    enroll(p, m); execute(p, m, owner)
    value(p, m, q(50, "110", time=T+MINUTE))
    old = p.snapshot
    observe(p, m, owner.process(exit_command(owner, n=6, quantity=D("1"), timestamp=T+MINUTE)))
    observe(p, m, owner.process(q(7, "111", time=T+MINUTE)))
    assert p.snapshot.members[0].valuation is None
    assert p.snapshot.members[0].valuation_head == old.members[0].valuation
    assert p.snapshot.equity == 3020 and p.snapshot.gross_exposure == 111
    assert old.equity == 3018 and old.gross_exposure == 220
    assert Portfolio.replay(p.snapshot.config, p.events).snapshot == p.snapshot


def test_explicit_valuation_delivery_clock_survives_superseding_account_mark():
    p = portfolio()
    m, owner = member_owner(advanced=True)
    enroll(p, m); execute(p, m, owner)
    value(p, m, q(50, "110", time=T+4*MINUTE, age=3*MINUTE))
    observe(p, m, owner.process(exit_command(owner, n=6, quantity=D("1"), timestamp=T+MINUTE)))
    observe(p, m, owner.process(q(7, "111", time=T+2*MINUTE)))
    assert p.snapshot.members[0].valuation is None
    before = p._publication
    with pytest.raises(PortfolioError, match="chronology"):
        value(p, m, q(51, "112", time=T+3*MINUTE))
    assert p._publication is before
    value(p, m, q(51, "112", time=T+4*MINUTE))
    assert p.snapshot.equity == 3021


@pytest.mark.parametrize("boundary", ["publication_before", "publication_after"])
def test_valuation_head_publication_failure_is_atomic(monkeypatch, boundary):
    p = portfolio()
    m, owner = member_owner()
    enroll(p, m); execute(p, m, owner)
    value(p, m, q(50, "110", time=T+MINUTE))
    quote = q(51, "111", time=T+2*MINUTE)
    item = ValueMember(operation_id="next-valuation", member_id=m.member_id,
        source=quote.observation, provenance=quote.provenance,
        sequence=p.snapshot.sequence+1, timestamp=quote.timestamp)
    before = p._publication
    def fail(candidate):
        if boundary == "publication_after": p._publication = candidate
        raise RuntimeError("injected valuation publication failure")
    with monkeypatch.context() as patch:
        patch.setattr(p, "_publish", fail)
        with pytest.raises(RuntimeError): p.process(item)
    assert p._publication is before and p.snapshot.members[0].valuation_head.sequence == 50
    result = p.process(item)
    assert p.process(item) is result and p.snapshot.members[0].valuation_head.sequence == 51


def test_member_configuration_hard_ceiling_is_32():
    from quantlab.portfolio import PortfolioConfig
    config = portfolio().snapshot.config
    assert config.maximum_members == 32
    with pytest.raises(ValidationError):
        PortfolioConfig.model_validate(config.model_copy(update={"maximum_members": 33}))
