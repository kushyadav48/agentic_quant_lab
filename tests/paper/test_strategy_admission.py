"""Exact approval, separate policy decisions and independent artifact verification."""
import pytest
from pydantic import ValidationError
from quantlab.data import PriceType, Timeframe
from quantlab.paper import (
    AdmissionError, EligibilityDecision, EvidenceReference, ResearchEvidence,
    ResearchEvidenceStore, StrategyRuntime, admit_strategy, stable_id,
)
from quantlab.paper.strategy_models import record
from quantlab.strategies import (
    ApprovalState, FeatureArgument, FeatureReference, FeatureType,
    MarketField, MarketOperand, StrategyContent,
)
from tests.backtesting.helpers import D, MINUTE, START, approve, reference, strategy
from .strategy_helpers import setup, policy


def reseal(item, **changes):
    return record(type(item), **{**item.model_dump(exclude={"record_id"}), **changes})


def check(args, reason):
    decision = admit_strategy(**args)
    assert not decision.admitted and decision.reason == reason
    with pytest.raises(AdmissionError) as exc:
        StrategyRuntime(**args)
    assert exc.value.decision == decision
    return decision


@pytest.mark.parametrize("walk", [False, True])
def test_valid_approved_exact_version_admission_and_runtime(walk):
    args, account, evidence = setup(walk=walk)
    decision = admit_strategy(**args)
    assert decision.admitted and decision.reason == "admitted"
    assert decision.policy_digest == args["policy"].digest
    assert decision.causation_id == args["eligibility"].record_id
    assert decision.evidence == args["eligibility"].evidence
    assert StrategyRuntime(**args).admission == decision
    assert account.snapshot == args["account_snapshot"]


@pytest.mark.parametrize("state", ["draft", "validated", "revised", "invalidated"])
def test_unapproved_and_modified_versions_cannot_activate(state):
    args, account, evidence = setup()
    spec = args["strategy"]
    if state == "draft": bad = strategy(no_exit=True, approved=False)
    elif state == "validated": bad = strategy(no_exit=True, approved=False).mark_validated()
    elif state == "revised": bad = spec.revise(spec.content)
    else: bad = spec.model_copy(update={"content": spec.content.model_copy(update={"name": "tampered"})})
    args["strategy"] = bad
    check(args, "invalid_contract" if state == "invalidated" else "unapproved_strategy")
    assert account.snapshot == args["account_snapshot"]


@pytest.mark.parametrize("field,value", [("strategy_id", "different"), ("strategy_version", 2),
                                        ("strategy_digest", "0"*64)])
def test_exact_requested_identity_version_and_digest(field, value):
    args, _, _ = setup()
    args["config"] = args["config"].model_copy(update={field: value})
    check(args, "strategy_mismatch")


@pytest.mark.parametrize("missing,reason", [("policy", "missing_policy"), ("eligibility", "missing_eligibility"),
                                           ("evidence", "evidence_missing")])
def test_required_separate_authorities(missing, reason):
    args, _, _ = setup()
    args[missing] = None
    check(args, reason)


@pytest.mark.parametrize("changes", [{"eligible": False}, {"strategy_version": 2},
    {"strategy_digest": "0"*64}, {"policy_digest": "0"*64}, {"timestamp": START+20*MINUTE}])
def test_recorded_eligibility_cannot_substitute_for_exact_policy_binding(changes):
    args, _, _ = setup()
    args["eligibility"] = reseal(args["eligibility"], **changes)
    check(args, "eligibility_mismatch")


@pytest.mark.parametrize("kind", ["missing", "unverified", "rejected", "same_reviewer", "dataset", "future",
    "premature", "result_ref", "backtest_only", "no_robustness", "mismatched_report"])
def test_evidence_negative_boundaries(kind):
    args, _, original = setup()
    ev = list(original)
    reason = "evidence_mismatch"
    if kind == "missing":
        args["evidence"] = ResearchEvidenceStore(())
        reason = "evidence_missing"
    elif kind in ("unverified", "rejected"):
        ev[0] = reseal(ev[0], status=kind)
        reason = "evidence_unverified"
    elif kind == "same_reviewer": ev[0] = reseal(ev[0], verifier=args["eligibility"].reviewer)
    elif kind == "dataset":
        ev[0] = reseal(ev[0], datasets=(ev[0].datasets[0].model_copy(update={"version_digest": "0"*64}),))
    elif kind == "future": ev[0] = reseal(ev[0], verified_at=START+11*MINUTE)
    elif kind == "premature": ev[0] = reseal(ev[0], verified_at=START)
    elif kind == "result_ref":
        refs = list(args["eligibility"].evidence)
        refs[0] = refs[0].model_copy(update={"result_digest": "0"*64})
        args["eligibility"] = reseal(args["eligibility"], evidence=tuple(refs))
    elif kind == "backtest_only": ev = ev[:1]; reason = "evidence_insufficient"
    elif kind == "no_robustness": ev = ev[:2]; reason = "evidence_insufficient"
    else:
        report = ev[0].report.model_copy(update={"strategy_version": 2})
        ev[0] = reseal(ev[0], report=report, result_digest=stable_id("paper-research-result-v1", report))
    if kind not in ("missing", "result_ref"):
        args["evidence"] = ResearchEvidenceStore(tuple(ev))
        args["eligibility"] = reseal(args["eligibility"], evidence=tuple(
            EvidenceReference(evidence_id=e.record_id, result_digest=e.result_digest) for e in ev))
    check(args, reason)


@pytest.mark.parametrize("changes", [{"minimum_oos_observations": 100},
    {"minimum_robustness_candidates": 2}, {"minimum_oos_return": D("100")},
    {"minimum_oos_drawdown": D("0")}, {"oos_requirement": "walk_forward"}])
def test_only_explicit_versioned_policy_sets_required_evidence_and_metrics(changes):
    args, _, _ = setup(policy_changes=changes)
    check(args, "evidence_insufficient")


def test_financial_thresholds_and_requirements_have_no_implicit_defaults():
    for missing in ("minimum_oos_return", "minimum_oos_drawdown", "minimum_oos_observations",
                    "minimum_robustness_candidates", "oos_requirement", "rationale_reference"):
        body = policy().model_dump(exclude={missing})
        with pytest.raises(ValidationError): type(policy())(**body)
    args, _, _ = setup()
    assert admit_strategy(**args).admitted  # None is an explicit descriptive-evidence policy.


@pytest.mark.parametrize("kind", ["timeframe", "quantity", "spread", "wrong_account", "future_clock", "exit",
                                  "bid_rule", "instrument", "stop", "unknown_indicator", "sma", "ml"])
def test_unsupported_rules_features_instruments_and_account_compatibility(kind):
    args, _, _ = setup()
    cfg, spec = args["config"], args["strategy"]
    reason = "account_incompatible"
    if kind == "timeframe": args["config"] = cfg.model_copy(update={"timeframe": Timeframe.H1})
    elif kind == "quantity": args["config"] = cfg.model_copy(update={"quantity": D("1.5")})
    elif kind == "spread": args["config"] = cfg.model_copy(update={"costs": cfg.costs.model_copy(update={"spread": D("1")})})
    elif kind == "wrong_account": args["account_snapshot"] = args["account_snapshot"].model_copy(update={
        "config": cfg.account.model_copy(update={"account_id": "different"})})
    elif kind == "future_clock": args["config"] = cfg.model_copy(update={"timestamp": START+9*MINUTE})
    else:
        content = spec.content.model_dump()
        reason = "unsupported_strategy"
        if kind == "exit": content["long"] = strategy().content.long
        elif kind == "bid_rule": content["long"]["entry"]["rules"][0]["left"] = MarketOperand(field=MarketField.BID)
        elif kind == "instrument": content["instruments"] = ("unknown:instrument",)
        elif kind == "stop":
            from quantlab.strategies import FixedDistance, DistanceUnit
            content["stop_loss"] = FixedDistance(value=D("1"), unit=DistanceUnit.PRICE)
        else:
            if kind == "ml":
                f = FeatureReference(feature_id="prediction", implementation_id="ml_forward_return_v1",
                    feature_type=FeatureType.ML_SIGNAL, parameters=(FeatureArgument(name="model_digest", value=1),))
            else: f = reference() if kind == "sma" else reference().model_copy(update={"implementation_id": "unknown"})
            content["features"] = (f,)
            reason = "unsupported_feature_delivery" if kind in ("sma", "ml") else "unsupported_strategy"
        revised = approve(spec.revise(StrategyContent(**content)))
        args["strategy"] = revised
        args["config"] = cfg.model_copy(update={"strategy_version": revised.version, "strategy_digest": revised.content_digest})
        args["eligibility"] = reseal(args["eligibility"], strategy_version=revised.version, strategy_digest=revised.content_digest)
    check(args, reason)


@pytest.mark.parametrize("kind", ["tampered_id", "tampered_result", "duplicate_evidence", "mutable_list"])
def test_retained_evidence_registry_is_not_a_mutable_external_override(kind):
    _, _, ev = setup()
    if kind == "tampered_id": bad = (ev[0].model_copy(update={"record_id": "0"*64}),)
    elif kind == "tampered_result": bad = (ev[0].model_copy(update={"result_digest": "0"*64}),)
    elif kind == "duplicate_evidence": bad = (ev[0], ev[0])
    else: bad = list(ev)
    with pytest.raises(ValueError): ResearchEvidenceStore(bad)
