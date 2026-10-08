"""Additional strict-contract, authority, policy and cross-layer negative checks."""
import ast
import builtins
import asyncio
from datetime import timedelta
from pathlib import Path
import socket
import subprocess
import threading
import urllib.request

import pytest
from pydantic import ValidationError
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
from quantlab.paper import (
    AdmissionError, EvidenceReference, FillRecord, OrderState, PaperIdentityConflict,
    PaperInputError, ResearchEvidenceStore, StrategyOrderAdapter, StrategyRuntime,
    admit_strategy, stable_id,
)
from quantlab.paper.models import canonical_json
from quantlab.risk import RiskAction, RiskConfig
from tests.backtesting.helpers import D, START, MINUTE, approve, strategy
from .strategy_helpers import close_quote, delivery, opening, ready, runtime, setup
from .test_strategy_admission import check, reseal


def test_activation_verifies_existing_artifacts_without_replaying_research(monkeypatch):
    import quantlab.backtesting as backtesting
    import quantlab.backtesting.engine as engine
    import quantlab.validation as validation
    import quantlab.ml as ml
    import quantlab.features as features
    args, _, _ = setup()
    def denied(*a, **k): raise AssertionError("activation must not rerun research or inference")
    for owner, names in ((backtesting, ("run_backtest",)), (engine, ("run_backtest",)),
            (validation, ("run_holdout", "run_walk_forward", "run_parameter_robustness")),
            (ml, ("predict_oos",)), (features, ("compute_features",))):
        for name in names: monkeypatch.setattr(owner, name, denied)
    assert admit_strategy(**args).admitted
    assert StrategyRuntime(**args).admission.admitted


@pytest.mark.parametrize("kind", ["risk", "cost", "holdout_window", "oos_version", "performance_identity", "walk_fold",
                                  "robustness_baseline", "robustness_structure"])
def test_verified_label_does_not_bypass_research_association_checks(kind):
    args, _, original = setup(walk=kind == "walk_fold")
    ev = list(original)
    index = 0 if kind in ("risk", "cost") else 2 if kind.startswith("robustness") else 1
    report = ev[index].report
    if kind == "risk": report = report.model_copy(update={"risk": RiskConfig(max_position_quantity=D("1"))})
    elif kind == "cost": report = report.model_copy(update={"execution_costs": ExecutionCostConfig(slippage=D("1"))})
    elif kind == "holdout_window": report = report.model_copy(update={"in_sample": report.out_of_sample})
    elif kind == "oos_version":
        segment = report.out_of_sample
        report = report.model_copy(update={"out_of_sample": segment.model_copy(update={
            "backtest": segment.backtest.model_copy(update={"strategy_version": 2})})})
    elif kind == "performance_identity":
        segment = report.out_of_sample
        report = report.model_copy(update={"out_of_sample": segment.model_copy(update={
            "performance": segment.performance.model_copy(update={"strategy_id": "different"})})})
    elif kind == "walk_fold": report = report.model_copy(update={"folds": tuple(reversed(report.folds))})
    elif kind == "robustness_baseline": report = report.model_copy(update={"baseline_candidate_id": "missing"})
    else:
        baseline = report.candidates[0]
        spec = approve(args["strategy"].revise(args["strategy"].content.model_copy(update={"name": "different"})))
        variant = baseline.model_copy(update={"candidate": baseline.candidate.model_copy(update={
            "candidate_id": "invalid-variant", "strategy": spec})})
        report = report.model_copy(update={"candidates": (baseline, variant),
            "summary": report.summary.model_copy(update={"evaluation_count": 2, "breakeven_count": 1})})
    ev[index] = reseal(ev[index], report=report, result_digest=stable_id("paper-research-result-v1", report))
    args["evidence"] = ResearchEvidenceStore(tuple(ev))
    args["eligibility"] = reseal(args["eligibility"], evidence=tuple(
        EvidenceReference(evidence_id=e.record_id, result_digest=e.result_digest) for e in ev))
    check(args, "evidence_mismatch")


@pytest.mark.parametrize("kind", ["unsupported_timing", "stale_approval", "unchecked_policy", "unchecked_eligibility"])
def test_unchecked_pydantic_copies_do_not_bypass_activation(kind):
    args, _, _ = setup()
    if kind == "unsupported_timing":
        s = args["strategy"]
        args["strategy"] = s.model_copy(update={"content": s.content.model_copy(update={
            "timing": s.content.timing.model_copy(update={"execution": "next_quote"})})})
    elif kind == "stale_approval": args["strategy"] = args["strategy"].model_copy(update={"version": 2})
    elif kind == "unchecked_policy": args["policy"] = args["policy"].model_copy(update={"version": True})
    else: args["eligibility"] = args["eligibility"].model_copy(update={"eligible": "true"})
    check(args, "invalid_contract")


def test_record_identity_and_policy_identity_bind_exact_canonical_content():
    rt, _, args, ev = runtime()
    rt.process(delivery())
    for model in (rt.snapshot.intent, rt.snapshot.decisions[0], rt.admission, args["eligibility"], *ev):
        with pytest.raises(ValidationError): type(model).model_validate(model.model_copy(update={"record_id": "0"*64}))
    assert args["policy"].digest != args["policy"].model_copy(update={"version": 2}).digest
    assert canonical_json(D("1.000")) == canonical_json(D("1"))
    assert canonical_json(timedelta(days=1, microseconds=2)) == '{"days":1,"microseconds":2,"seconds":0}'


def test_duplicate_result_artifacts_cannot_inflate_oos_evidence_count():
    args, _, original = setup()
    duplicate = reseal(original[1], provenance_reference="another-reference")
    refs = (*args["eligibility"].evidence, EvidenceReference(evidence_id=duplicate.record_id, result_digest=duplicate.result_digest))
    with pytest.raises(ValidationError): reseal(args["eligibility"], evidence=refs)


@pytest.mark.parametrize("phase", ["acceptance", "execution"])
def test_existing_risk_engine_enforces_limits_at_both_strategy_boundaries(phase):
    risk = RiskConfig(max_position_quantity=D("1")) if phase == "acceptance" else RiskConfig(max_notional_exposure=D("250"))
    cfg = BacktestConfig(initial_capital=D("1000"), quantity=D("2"), risk=risk)
    rt, adapter, account, d, _, _ = ready(research_config=cfg)
    accepted = adapter.submit(close_quote(d))
    if phase == "acceptance":
        assert adapter.snapshot.state is OrderState.REJECTED
        assert account.events == ()
        outcomes = accepted
    else:
        assert adapter.snapshot.state is OrderState.ACCEPTED
        outcomes = adapter.process_open(opening(d, price="200"))
        assert adapter.snapshot.state is OrderState.CANCELLED
        assert account.events[-1].kind == "release"
    assert any(getattr(e, "decision", None) is not None and e.decision.action is RiskAction.REJECT for e in outcomes)
    assert not any(isinstance(e, FillRecord) for e in adapter.snapshot.events)
    assert account.snapshot.position is None


def test_pricing_and_explicit_costs_are_owned_by_existing_kernel_and_account():
    cfg = BacktestConfig(initial_capital=D("1000"), quantity=D("2"), execution_costs=ExecutionCostConfig(
        slippage=D("0.5"), commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1")))
    rt, adapter, account, d, _, _ = ready(research_config=cfg)
    adapter.submit(close_quote(d))
    records = adapter.process_open(opening(d))
    fill = next(e for e in records if isinstance(e, FillRecord))
    assert fill.execution.execution_price == D("101.5")
    assert fill.execution.costs.commission == D("0.2")
    assert fill.execution.costs.fees == D("1")
    assert account.snapshot.fees_paid == D("1.2")
    assert account.snapshot.balance == D("998.8")
    assert account.snapshot.position_collateral == D("203")
    assert adapter.audit_snapshot.intent == rt.snapshot.intent
    assert adapter.audit_snapshot.decision == rt.snapshot.decisions[0]
    audit = adapter.audit_snapshot
    assert type(audit).model_validate_json(audit.model_dump_json()) == audit


def test_new_admission_rejects_an_already_occupied_account():
    rt, adapter, account, d, args, _ = ready()
    adapter.submit(close_quote(d))
    args["account_snapshot"] = account.snapshot
    check(args, "account_incompatible")
    adapter.process_open(opening(d))
    args["account_snapshot"] = account.snapshot
    check(args, "account_incompatible")


@pytest.mark.parametrize("kind", ["invalid_opening_retry", "invalid_close_retry", "cross_input_collision"])
def test_invalid_conflicting_identities_are_explicit_and_atomic(kind):
    rt, adapter, account, d, _, _ = ready()
    close, op = close_quote(d), opening(d)
    adapter.submit(close)
    if kind != "cross_input_collision": adapter.process_open(op)
    before = adapter.snapshot, account.snapshot, adapter.openings
    if kind == "invalid_opening_retry": operation = lambda: adapter.process_open(op.model_copy(update={"sequence": True}))
    elif kind == "invalid_close_retry": operation = lambda: adapter.submit(close.model_copy(update={"sequence": True}))
    else: operation = lambda: adapter.process_open(op.model_copy(update={"event_id": close.event_id}))
    with pytest.raises(PaperIdentityConflict): operation()
    assert (adapter.snapshot, account.snapshot, adapter.openings) == before


def test_activated_version_has_no_public_mutation_or_switch_surface():
    rt, adapter, account, d, _, _ = ready()
    with pytest.raises(AttributeError): rt.config = rt.config.model_copy(update={"strategy_version": 2})
    with pytest.raises(ValidationError): rt.config.strategy_version = 2
    with pytest.raises(TypeError): rt.process(d, strategy=strategy())
    with pytest.raises(TypeError): adapter.submit(close_quote(d), approval=rt.admission)
    assert account.events == ()


def test_complete_runtime_path_has_no_io_workers_sleep_or_llm_authority(monkeypatch):
    rt, adapter, account, d, _, _ = ready()
    close, op = close_quote(d), opening(d)
    def denied(*args, **kwargs): raise AssertionError("external execution forbidden")
    with monkeypatch.context() as patch:
        for owner, name in ((socket, "socket"), (socket, "create_connection"),
                (urllib.request, "urlopen"), (threading.Thread, "start"),
                (asyncio, "create_task"), (builtins, "open"), (Path, "open"), (subprocess, "Popen")):
            patch.setattr(owner, name, denied)
        adapter.submit(close)
        rt.process(delivery(1))  # Independent rule evaluation, no external feature calls.
        # Use a fresh already delivered intent for the opening boundary.
        # A later completed bar explicitly blocks this missed open.
        with pytest.raises(PaperInputError): adapter.process_open(op)
    assert account.snapshot.position is None


def test_mcp_and_orchestration_expose_no_paper_execution_imports_or_tools():
    root = Path(__file__).parents[2] / "src" / "quantlab"
    for directory in ("mcp", "orchestration", "llm", "interpretation"):
        for path in (root / directory).glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom): assert not (node.module or "").startswith("quantlab.paper")
                if isinstance(node, ast.Import): assert not any(a.name.startswith("quantlab.paper") for a in node.names)


@pytest.mark.parametrize("phase", ["before_submission", "before_opening"])
def test_external_reservations_invalidate_exclusive_strategy_execution(phase):
    from quantlab.paper import FundReservation, ReserveFunds
    rt, adapter, account, d, _, _ = ready()
    if phase == "before_opening": adapter.submit(close_quote(d))
    reservation = FundReservation(reservation_id="external-reservation", account_id="account",
        strategy_id="other", order_id="2"*64, accepted_event_id="1"*64, amount=D("1"))
    account.apply_trusted(ReserveFunds(event_id="external-reserve", account_id="account",
        strategy_id="other", sequence=account.snapshot.last_input_sequence+1, timestamp=d.timestamp,
        transaction_id=reservation.order_id, causation_id=reservation.accepted_event_id, reservation=reservation))
    before = adapter.snapshot, account.snapshot, account.events
    operation = (lambda: adapter.submit(close_quote(d))) if phase == "before_submission" else (lambda: adapter.process_open(opening(d)))
    with pytest.raises(PaperInputError, match="exclusive entry"): operation()
    assert (adapter.snapshot, account.snapshot, account.events) == before
