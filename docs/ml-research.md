# Phase 12 — offline ML research

Phase 12 answers “what does this fitted model predict?” It constructs supervised
research datasets, fits one deterministic ridge linear regression baseline, and
emits timestamped OOS predictions as canonical FeatureObservation values. It never
constructs a strategy, approval, Fill, Position or ClosedTrade. Strategy approval,
backtesting, execution costs, analytics, chronological validation and deterministic
risk remain the existing stack's responsibilities. Historical predictions do not
establish future profitability or production readiness.

## Contracts and explicit datasets

Import the public models and functions from quantlab.ml. All models inherit the
existing strict, frozen, extra-forbidden, instance-revalidating Pydantic contract.
Collections are tuples; values are finite Decimal; timestamps are aware UTC. Public
operations revalidate nested inputs, including unchecked model_copy/model_construct
instances. Python float/string coercion is not accepted for Decimal values.

MLFeatureSchema.features is an ordered tuple of FeatureRequest declarations. Each
specifies an alias, implementation (defaults to alias), integer parameters and an
optional matching timeframe. feature_ids exposes the exact column order. Duplicate
aliases are rejected. The complete schema, including declaration metadata, must
match at inference; merely having the same set of names is insufficient. Equal-time
input observation ordering cannot change the matrix or digest. No feature discovery,
raw market columns, sorting, filling or imputation occurs.

build_dataset requires canonical bars, canonical features, explicit Instrument,
feature_schema and a Phase 10 ValidationWindow. Windows are half-open indices into
the supplied chronological observed-bar sequence. Gaps count as gaps, not invented
bars or exchange sessions. A dataset covers one instrument/timeframe/price basis.
The full input collections are validated before selecting the window. Duplicate,
unsorted, mixed-series, undeclared and mismatched implementation/parameter inputs
fail closed. Supplied history must cover feature input_start; contributing delayed
bars cannot be hidden behind false feature availability.

Each MLDatasetRow records instrument, timeframe, price basis, decision timestamp,
available_at, input_start, ordered feature_values, input_digest and an optional
MLLabel. The input digest hashes the ordered complete FeatureObservation records.
The dataset retains schema, optional target, requested window, temporal bounds,
rows and mutually exclusive omission counts. dataset_digest hashes this content.

For a decision at T, each required feature must have timestamp exactly T and
available_at <= T. FeatureObservation already requires available_at >= timestamp,
so an included observation is available exactly at T. The ending bar must also be
available at its close. Missing or delayed inputs omit the row. There is no nearest
match, forward/backward fill, interpolation or zero replacement. Feature values are
caller-supplied causal observations: timestamps/provenance are checked, but a false
causality claim in externally constructed feature values cannot be detected from
metadata alone.

## Targets and information cutoff

ForwardReturnTarget(horizon=H) requires a strict positive integer. At original bar
index i, its continuous target is close[i+H] / close[i] - 1. It counts observed bars,
not retained feature rows. The calculation uses isolated 34-digit ROUND_HALF_EVEN
Decimal division followed by subtraction. No binary direction target is included.

MLLabel is separate from feature_values and includes target_timestamp, available_at
and a source digest of the complete current and target bars. Availability is the
maximum of those two bars' available_at values. Future target prices are permitted
only here; no target price is injected into the input matrix.

Training labels cannot cross the requested window end. The final H window bars are
omitted even if later bars were supplied. A label published after the final window
close is also omitted. Omission precedence is label boundary, missing features,
unavailable features/ending bar, then unavailable label. The four counters account
for every omitted decision exactly once. Inference uses target=None and does not
read any forward prices.

The artifact's information_cutoff is the final close of the requested training
window. This is conservative: it is at least the latest publication needed for every
retained label, even when the final retained decision is earlier. train_start and
train_end separately record retained decision bounds. OOS decision P must be
strictly greater than information_cutoff. The entire evaluation window must start
at or after the cutoff; an empty/omitted prefix cannot hide an overlapping request.
A bar that begins before the cutoff and ends after it is conservatively rejected.

## Baseline and numerics

MLModelConfig supports only model_type="ridge" and a positive finite Decimal alpha
(default 1). The objective is sum((y - intercept - X*coefficients)**2) plus
alpha * sum(coefficients**2). The intercept is not penalized. At least k+1 labeled
rows are required for k features; otherwise training raises MLCompatibilityError.

Training centers X and y using exact fractions.Fraction arithmetic, forms the ridge
normal equations, and solves the positive-definite system by deterministic Gaussian
elimination and back substitution. Positive alpha gives a unique slope solution for
constant or collinear columns. Centering here is algebra for the intercept, not a
persisted feature scaler. There is no normalization, feature selection, shuffle,
random split, seed, stochastic optimizer, parallel solver or model selection.

Neither NumPy nor scikit-learn was installed; Pydantic is the only declared runtime
dependency. The small exact-rational baseline needs no added package or download.
This deliberately favors auditability and reproducibility over large-matrix speed.
Rational numerator/denominator growth and the quadratic matrix/cubic solve make it
unsuitable for large or very wide datasets. Feature units affect ridge regularization;
there is no automatic scaling or alpha tuning.

Slopes and intercept are converted from exact fractions to Decimal using a fresh
34-digit ROUND_HALF_EVEN context. Inference uses those published Decimal parameters
in the declared order: start with intercept, then add coefficient[j] * feature[j].
Thus fitting parameters and inference are inspectable, with a defined rounding
boundary; repeating-decimal coefficients are not claimed to be mathematically exact.
All numerical operations are independent of caller Decimal precision, rounding,
traps and flags. Overflow/non-finite results fail as MLInputError. No float64 is used,
no Decimal(binary_float) conversion occurs, and execution/accounting contracts are
unchanged. Artifacts are immutable; inference has no mutable estimator state.

## Artifacts, digests and predictions

MLModelArtifact retains phase12-v1, model config, ordered feature schema, target,
instrument/timeframe/price basis, training window and decision bounds, earliest
training feature input_start, information_cutoff, row count, training_data_digest,
coefficients and intercept. No raw training rows, opaque pickle, current timestamp,
filesystem path, object ID or machine state is stored in the artifact.

model_digest is SHA-256 of canonical complete artifact content. Canonical JSON sorts
object keys, preserves tuple/column order, uses aware UTC times, and strips insignificant
Decimal trailing zeroes without ambient-context normalization. The training-data
digest includes retained row values, observation provenance, label source digests,
window and omission metadata. These properties are recomputed rather than serialized
as duplicate fields, and are identical after JSON round trip. Digests identify content;
they are not signatures proving that a caller-supplied artifact was honestly trained.

predict_oos accepts an artifact and an explicit unlabeled MLDataset. It rejects
schema/series mismatches, labeled inference datasets and invalid chronology. It returns
MLPrediction tuples containing model_digest, instrument/timeframe/price basis,
decision timestamp, information_cutoff, input_start, input_digest and Decimal value.
available_at equals the decision close, never earlier than input availability or
training cutoff. input_start conservatively includes both training feature history
and current prediction feature history. When canonical consumers check that entire
span for delayed contributing bars, a delay in an otherwise unused intervening bar
can conservatively reject the feature; this does not grant earlier availability.

## Strategy, execution and validation integration

prediction_feature_reference(artifact, "forecast") returns a Phase 5 FeatureReference
with ML_SIGNAL type, implementation_id="ml_forward_return_v1", matching timeframe
and exactly one integer parameter named model_digest. Its value is int(SHA256, 16),
a lossless encoding of all 256 bits compatible with Phase 6 integer parameters.

predictions_to_features(predictions, artifact=artifact, feature_id="forecast") checks
model/series/cutoff provenance and chronology, then returns canonical Decimal
FeatureObservation objects with the exact same declaration metadata. A strategy's
approval digest therefore binds to the fitted model. Changing the model requires
an updated feature declaration and exact-version approval. The feature validator
supports only this bounded ML declaration; arbitrary external implementations and
parameters remain rejected. compute_features still only computes registered bar
indicators; it cannot synthesize a fitted ML prediction.

An already reviewed and approved strategy can compare FeatureOperand(feature_id="forecast")
with a Decimal threshold. Pass its ML observations into run_backtest normally.
The existing engine alone creates next-open fills, applies Phase 8 spread/slippage,
commission/fees, and invokes mandatory Phase 11 ALLOW/REJECT risk gating before entry.
A rejected ML-driven signal incurs no fill or cost. analyze_performance consumes the
ordinary BacktestResult without ML-specific accounting.

Phase 10 ValidationWindow is reused directly; no research-validation engine rewrite
or ML holdout/walk-forward runner is added. For a caller-defined chronological split:

~~~python
from quantlab.features import FeatureRequest, compute_features
from quantlab.ml import (
    ForwardReturnTarget, MLFeatureSchema, build_dataset, train_model,
    predict_oos, prediction_feature_reference, predictions_to_features,
)
from quantlab.validation import HoldoutConfig, ValidationWindow

# bars and instrument are supplied canonical historical inputs, with >= 150 bars.
schema = MLFeatureSchema(features=(FeatureRequest(feature_id="close"),))
observations = compute_features(bars, schema.features, instrument=instrument)
split = HoldoutConfig(train=ValidationWindow(start=0, end=100),
                      test=ValidationWindow(start=100, end=150))
training = build_dataset(bars, observations, instrument=instrument,
    feature_schema=schema, window=split.train, target=ForwardReturnTarget(horizon=3))
artifact = train_model(training)
evaluation = build_dataset(bars, observations, instrument=instrument,
    feature_schema=schema, window=split.test)  # no targets
predictions = predict_oos(artifact, evaluation)
declaration = prediction_feature_reference(artifact, "forecast")
features = predictions_to_features(predictions, artifact=artifact, feature_id="forecast")
# Put declaration in reviewed strategy content and obtain its normal exact-version
# approval. Then run_backtest(approved_strategy, bars[100:150], features, ...),
# followed by analyze_performance. No ML function approves or executes a strategy.
~~~

The existing Phase 10 holdout runner can consume supplied OOS ML features with that
approved declaration. Training periods have no predictions from this OOS-only API;
the integration test explicitly verifies no in-sample ML signals. For walk-forward
research, callers may build/train separately per Phase 10 fold and obtain each
model's corresponding strategy approval. Automated fold fitting/approval orchestration
is deferred; never fit on full history and reuse that model in earlier folds. OOS
results must not feed selection for earlier periods or repeated final-holdout tuning.

## Errors and limits

MLResearchError derives from ValueError. MLInputError covers malformed inputs and
unsupported arithmetic; MLCompatibilityError covers insufficient labeled rows,
window/schema/series incompatibility and invalid OOS chronology. Model construction
itself uses ordinary Pydantic ValidationError. Missing or unavailable observations
are the explicit omission policy, not silent numeric substitutions.

V1 includes no training metrics, classifier, scaler, persistence service, AutoML,
hyperparameter search/ranking, online/live learning, neural networks, model downloads,
network calls, cloud API, live/paper execution, portfolio engine, UI, LLM provider or
Phase 13 work. Test data are synthetic and offline. Tests cover leakage independence,
inside-window label sensitivity, exact hand calculations, immutable JSON artifacts,
hostile Decimal settings, strict unchecked-input revalidation, network isolation,
approval binding, costs, analytics and hard-risk rejection. No claim of predictive
quality or future returns follows from passing these engineering tests.
