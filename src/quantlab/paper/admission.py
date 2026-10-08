"""Offline admission: approval, independent evidence and explicit policy are distinct."""
from types import MappingProxyType

from quantlab.backtesting import BacktestResult
from quantlab.backtesting.engine import _validate_strategy
from quantlab.features import DEFAULT_REGISTRY
from quantlab.strategies import ApprovalState, StrategySpecification
from quantlab.validation import HoldoutResult, RobustnessReport, WalkForwardReport
from quantlab.validation.robustness import _structure
from .account_models import AccountSnapshot
from .errors import PaperInputError
from .models import stable_id
from .strategy_models import (
    AdmissionRecord, EligibilityDecision, EligibilityPolicy, ResearchEvidence,
    StrategySessionConfig, record,
)


def binding(strategy):
    return dict(strategy_id=strategy.strategy_id, strategy_version=strategy.version,
                strategy_digest=strategy.content_digest)


class ResearchEvidenceStore:
    """Read-only trusted application artifact set. No agent/wire recording API."""
    def __init__(self, evidence: tuple[ResearchEvidence, ...]):
        if type(evidence) is not tuple or len(evidence) > 32:
            raise PaperInputError("expected a bounded tuple of retained research evidence")
        try:
            records = tuple(ResearchEvidence.model_validate(e) for e in evidence)
            if len({e.record_id for e in records}) != len(records):
                raise ValueError("duplicate evidence identity")
            for item in records:
                item.canonical_json()
        except (ValueError, TypeError) as exc:
            raise PaperInputError("invalid retained research evidence") from exc
        self._records = MappingProxyType({e.record_id: e for e in records})

    def get(self, evidence_id: str) -> ResearchEvidence | None:
        return self._records.get(evidence_id)


class AdmissionError(PaperInputError):
    def __init__(self, decision: AdmissionRecord):
        self.decision = decision
        super().__init__(decision.reason)


class _Denied(Exception):
    def __init__(self, reason):
        self.reason = reason


def _require(ok, reason):
    if not ok:
        raise _Denied(reason)


def _result_matches(result, strategy, config):
    return (result.strategy_id, result.strategy_version, result.strategy_content_digest,
            result.instrument_id, result.timeframe, result.price_type,
            result.quantity, result.execution_costs, result.risk) == (
        strategy.strategy_id, strategy.version, strategy.content_digest,
        config.account.instrument.instrument_id, config.timeframe, config.price_type,
        config.quantity, config.costs, config.risk)


def _segment(segment, strategy, config, research_config):
    result, performance, meta = segment.backtest, segment.performance, segment.metadata
    _require(_result_matches(result, strategy, config), "evidence_mismatch")
    _require((result.initial_capital, result.quantity, result.execution_costs, result.risk) == (
        research_config.initial_capital, research_config.quantity,
        research_config.execution_costs, research_config.risk), "evidence_mismatch")
    _require((performance.strategy_id, performance.strategy_version,
        performance.strategy_content_digest, performance.instrument_id,
        performance.timeframe, performance.price_type, performance.initial_capital,
        performance.final_equity, performance.realized_pnl, performance.unrealized_pnl,
        performance.has_open_position) == (
        result.strategy_id, result.strategy_version, result.strategy_content_digest,
        result.instrument_id, result.timeframe, result.price_type, result.initial_capital,
        result.final_equity, result.realized_pnl, result.unrealized_pnl,
        result.open_position is not None), "evidence_mismatch")
    _require(len(result.equity_curve) == meta.observation_count and
        result.equity_curve[0].timestamp == meta.first_decision_close and
        result.equity_curve[-1].timestamp == meta.last_decision_close, "evidence_mismatch")


def _verify_evidence(strategy, config, policy, eligibility, store):
    _require(type(store) is ResearchEvidenceStore, "evidence_missing")
    kinds, oos = set(), []
    expected_datasets = set(eligibility.datasets)
    for ref in eligibility.evidence:
        evidence = store.get(ref.evidence_id)
        _require(evidence is not None, "evidence_missing")
        _require(evidence.status == "verified", "evidence_unverified")
        _require(evidence.result_digest == ref.result_digest
            and set(evidence.datasets) == expected_datasets
            and evidence.verified_at <= eligibility.timestamp
            and evidence.verifier != eligibility.reviewer, "evidence_mismatch")
        report = evidence.report
        results = ([report] if type(report) is BacktestResult else
            [report.in_sample.backtest, report.out_of_sample.backtest] if type(report) is HoldoutResult else
            [s.backtest for f in report.folds for s in (f.in_sample, f.out_of_sample)]
                if type(report) is WalkForwardReport else
            [c.evaluation.backtest for c in report.candidates])
        _require(all(r.equity_curve and r.equity_curve[-1].timestamp <= evidence.verified_at
            for r in results), "evidence_mismatch")
        if type(report) is BacktestResult:
            kinds.add("backtest")
            _require(_result_matches(report, strategy, config), "evidence_mismatch")
            _require(bool(report.equity_curve), "evidence_insufficient")
        elif type(report) is HoldoutResult:
            kinds.add("holdout")
            _require(report.in_sample.metadata.window == report.config.train
                and report.out_of_sample.metadata.window == report.config.test
                and report.in_sample.metadata.last_decision_close <
                    report.out_of_sample.metadata.first_decision_close, "evidence_mismatch")
            for s in (report.in_sample, report.out_of_sample):
                _segment(s, strategy, config, report.backtest_config)
                _require(s.performance.config == report.analytics_config, "evidence_mismatch")
            oos.append(report.out_of_sample)
        elif type(report) is WalkForwardReport:
            kinds.add("walk_forward")
            _require(report.out_of_sample_summary.evaluation_count == len(report.folds),
                     "evidence_mismatch")
            for i, fold in enumerate(report.folds):
                plan = report.config
                test_start = plan.train_size + i * plan.step_size
                train_start = 0 if plan.mode.value == "expanding" else test_start - plan.train_size
                _require(fold.fold.fold_index == i
                    and (fold.fold.train.window.start, fold.fold.train.window.end,
                         fold.fold.test.window.start, fold.fold.test.window.end) == (
                            train_start, test_start, test_start, test_start + plan.test_size)
                    and fold.in_sample.metadata == fold.fold.train
                    and fold.out_of_sample.metadata == fold.fold.test, "evidence_mismatch")
                for s in (fold.in_sample, fold.out_of_sample):
                    _segment(s, strategy, config, report.backtest_config)
                    _require(s.performance.config == report.analytics_config, "evidence_mismatch")
                oos.append(fold.out_of_sample)
        elif type(report) is RobustnessReport:
            kinds.add("robustness")
            _require(len({c.candidate.candidate_id for c in report.candidates}) == len(report.candidates)
                and report.summary.evaluation_count == len(report.candidates), "evidence_mismatch")
            baseline = next((c for c in report.candidates
                if c.candidate.candidate_id == report.baseline_candidate_id), None)
            _require(baseline is not None and binding(baseline.candidate.strategy) == binding(strategy),
                     "evidence_mismatch")
            _require(len(report.candidates) >= policy.minimum_robustness_candidates,
                     "evidence_insufficient")
            for candidate in report.candidates:
                spec = candidate.candidate.strategy
                _require(spec.state is ApprovalState.APPROVED
                    and spec.strategy_id == strategy.strategy_id
                    and _structure(spec) == _structure(strategy)
                    and candidate.evaluation.metadata == report.metadata, "evidence_mismatch")
                _segment(candidate.evaluation, spec, config, report.backtest_config)
                _require(candidate.evaluation.performance.config == report.analytics_config,
                         "evidence_mismatch")
        else:
            raise _Denied("evidence_mismatch")
    _require({"backtest", "robustness"} <= kinds and bool(oos), "evidence_insufficient")
    _require(policy.oos_requirement == "either" or policy.oos_requirement in kinds,
             "evidence_insufficient")
    _require(sum(s.metadata.observation_count for s in oos) >= policy.minimum_oos_observations,
             "evidence_insufficient")
    for segment in oos:
        performance = segment.performance
        _require(policy.minimum_oos_return is None or
            performance.returns.cumulative_return >= policy.minimum_oos_return, "evidence_insufficient")
        _require(policy.minimum_oos_drawdown is None or
            performance.drawdown.maximum_drawdown_percentage >= policy.minimum_oos_drawdown,
            "evidence_insufficient")


def admit_strategy(strategy: StrategySpecification, config: StrategySessionConfig, *,
                   policy: EligibilityPolicy | None, eligibility: EligibilityDecision | None,
                   evidence: ResearchEvidenceStore | None,
                   account_snapshot: AccountSnapshot) -> AdmissionRecord:
    """Return an immutable accepted/denied audit. Never run research or approve intent.

    A malformed session config cannot supply trustworthy audit metadata and raises.
    All other admission denials retain a stable reason bound to that request.
    """
    try:
        config = StrategySessionConfig.model_validate(config)
        config_digest = stable_id("paper-strategy-config-v1", config)
    except (ValueError, TypeError) as exc:
        raise PaperInputError("invalid strategy session configuration") from exc
    policy_digest = causation = None
    refs = ()
    try:
        strategy = StrategySpecification.model_validate(strategy)
        _require(strategy.state is ApprovalState.APPROVED, "unapproved_strategy")
        _require(binding(strategy) == {k: getattr(config, k) for k in binding(strategy)},
                 "strategy_mismatch")
        _require(strategy.approval.reviewed_at <= config.timestamp, "unapproved_strategy")
        _require(policy is not None, "missing_policy")
        policy = EligibilityPolicy.model_validate(policy)
        policy_digest = policy.digest
        _require(eligibility is not None, "missing_eligibility")
        eligibility = EligibilityDecision.model_validate(eligibility)
        causation, refs = eligibility.record_id, eligibility.evidence
        _require(eligibility.eligible and eligibility.policy_digest == policy_digest
            and eligibility.timestamp <= config.timestamp
            and {k: getattr(eligibility, k) for k in binding(strategy)} == binding(strategy),
            "eligibility_mismatch")
        snapshot = AccountSnapshot.model_validate(account_snapshot)
        _require(snapshot.config == config.account and snapshot.position is None
            and not snapshot.reservations and snapshot.timestamp <= config.timestamp
            and config.costs.spread == 0 and config.timeframe is strategy.content.timeframe,
            "account_incompatible")
        n, d = config.quantity.as_integer_ratio()
        sn, sd = config.account.instrument.quantity_increment.as_integer_ratio()
        _require((n * sd) % (d * sn) == 0, "account_incompatible")
        try:
            _, requests = _validate_strategy(strategy, config.account.instrument)
        except ValueError:
            raise _Denied("unsupported_strategy") from None
        _require(all(side is None or side.exit is None
            for side in (strategy.content.long, strategy.content.short)), "unsupported_strategy")
        _require(len(requests) <= 32 and len(strategy.content.parameters) <= 32, "unsupported_feature_delivery")
        _require(all((r.implementation_id or r.feature_id) in ("open", "high", "low", "close")
            and DEFAULT_REGISTRY.get(r.implementation_id or r.feature_id).kind.value == "raw"
            for r in requests.values()), "unsupported_feature_delivery")
        _verify_evidence(strategy, config, policy, eligibility, evidence)
        reason = "admitted"
    except _Denied as exc:
        reason = exc.reason
    except (ValueError, TypeError, AttributeError, IndexError):
        reason = "invalid_contract"
    return record(AdmissionRecord, **{k: getattr(config, k) for k in (
        "strategy_id", "strategy_version", "strategy_digest")},
        session_id=config.session_id, account_id=config.account.account_id,
        config_digest=config_digest, timestamp=config.timestamp,
        policy_digest=policy_digest, causation_id=causation, evidence=refs,
        admitted=reason == "admitted", reason=reason)
