# Deterministic risk engine (Phase 11)

`quantlab.risk` enforces configured hard limits on new single-instrument exposure.
Strategy rules propose entries; a pure risk function decides whether the complete
fixed quantity is allowed; execution then calculates prices and costs. Every
entry in `run_backtest` passes this boundary. There is no override flag, callback,
advisory mode, AI judgment or alternate risk-disabled entry path.

These controls enforce the declared research limits. They do not prevent losses
or guarantee a maximum future loss, brokerage solvency or live execution behavior.

## Configuration

```python
from decimal import Decimal
from quantlab.backtesting import BacktestConfig, run_backtest
from quantlab.risk import RiskConfig

config = BacktestConfig(
    initial_capital=Decimal("10000"), quantity=Decimal("20"),
    risk=RiskConfig(
        max_position_quantity=Decimal("25"),
        max_notional_exposure=Decimal("3000"),
        max_equity_fraction=Decimal("0.25"),
        minimum_equity=Decimal("1000"),
        max_drawdown_fraction=Decimal("0.10"),
    ),
)
result = run_backtest(approved_strategy, bars, features,
                      instrument=instrument, config=config)
decisions = result.risk_decisions
```

Every limit defaults to `None`, meaning disabled. `RiskConfig()` adds no
restrictions, even at nonpositive research equity, and preserves previous signals,
fills, trades, open positions, P&L, equity and analytics. Audit fields are additive.

| Field | Valid configured value | Rejection condition |
| --- | --- | --- |
| max_position_quantity | Finite positive Decimal | requested quantity > cap |
| max_notional_exposure | Finite positive Decimal | reference open * requested quantity > cap |
| max_equity_fraction | Decimal in (0, 1] | reference open * requested quantity > current equity * fraction |
| minimum_equity | Finite nonnegative Decimal | current equity <= minimum |
| max_drawdown_fraction | Decimal in (0, 1] | current equity / running peak - 1 <= -fraction |

Equality is allowed at quantity/notional/equity-exposure caps. Minimum equity and
drawdown block at equality. Zero is meaningful only for the minimum-equity guard;
zero is invalid for the other limits. Python inputs must use Decimal, never floats,
integers, numeric strings or booleans. Fractions are fractions, not percentages.
An enabled equity-fraction cap rejects positive exposure at nonpositive equity.

V1 supports `RiskAction.ALLOW` and `RiskAction.REJECT`. Allowed quantity is exactly
`BacktestConfig.quantity`; rejected quantity is zero. No reduction, flooring or
rounding is performed. The existing exact `Instrument.quantity_increment` check
still runs before replay; malformed requested quantities are input errors even
when a risk cap would reject them. Phase 9 requires fixed quantities throughout
each result, so dynamic resizing is deferred.

## Price, equity and evaluation timing

A strategy signal at bar N close proposes execution at the following supplied
bar's open. When that open is known under the existing simulation assumption,
the engine constructs `RiskContext` and calls `evaluate_entry_risk(context, config)`
**before** `_next_open_fill`. Reference price is that next bar's unadjusted open,
with its declared TRADE/MID/BID/ASK basis. Gaps use the next supplied open. High,
low, close, volume and features of the execution bar do not enter this decision.
The existing assumption that complete-bar publication does not gate the open
fill is unchanged; later publication still gates close-time strategy evaluation.

Notional is positive reference price times positive requested quantity for both
long and short. It is unsigned and uses existing research quantity/P&L units,
without contract multipliers, Forex lot conversion, account-currency conversion,
spread or slippage. This reference-notional cap does not cap adjusted fill notional.

Current equity is initial capital + cumulative realized account P&L immediately
before entry. Only flat accounts can propose new exposure, so unrealized P&L is
zero then. Realized account P&L includes costs of earlier fills; prospective entry
costs have not yet been applied. No cash debit, leverage or margin is inferred.

## Runtime drawdown peak

The backtester owns a separate local running peak; analytics is never runtime
state. Each run initializes it to initial capital. At each bar close, update the
peak with that equity observation only when the complete bar is available by its
close (`available_at <= end_time`). Before an entry evaluation, also include the
current known flat equity. The positive peak therefore always includes current
pre-entry equity. Prior on-time unrealized gains participate in the peak even if
they were never realized. A 1000 peak and 900 current equity is exactly -0.10
drawdown and blocks a configured 0.10 limit.

Delayed retrospective close marks remain in the unchanged reporting equity curve
but **never** enter the runtime peak, including after their later publication.
V1 does not replay delayed observations or use retrospective accounting marks as
if they had been known at the original close. Consequently runtime risk drawdown
can differ from Phase 9's retrospective drawdown on datasets with delayed bars.
No intrabar high/low peak or continuous account observation is invented. This is
an observation-based gate, not an intrabar liquidation or maximum-loss guarantee.

Drawdown is recomputed from current context for each entry; it is not a permanently
latched circuit breaker. Risk rejection alone neither changes account state nor
closes an existing position. No cumulative realized-loss or daily/session limit
is included in V1.

## Decisions, signals, exits and costs

`RiskDecision` records signal_time, execution_time, LONG/SHORT side,
requested_quantity, approved_quantity, reference_price, current_equity,
running_peak_equity, action and an immutable reasons tuple. Reasons use stable
`RiskReason` enum values, in this order: max_position_quantity,
max_notional_exposure, max_equity_fraction, minimum_equity, max_drawdown.
All breached controls are recorded; ALLOW has no reasons and REJECT requires
at least one. Times normalize to UTC and execution cannot precede the signal.

`BacktestResult.risk` retains the policy, including runs with no entries;
`risk_decisions` retains every evaluated entry in execution order and defaults
to an empty tuple for direct result construction. A final-bar signal has no
following open, so no risk evaluation or fill is fabricated.

Rejected intent is consumed and its original Signal stays in `signals`. No Fill,
position, trade, commission, fee, spread/slippage cost or P&L change is created.
Because the account remains flat, the strategy may propose another entry at a
later close, evaluated independently at its following open. Rejected intent is
not retried automatically at an unrelated open.

Allowed entries use the unchanged Phase 8 quantity and cost formulas. Current
pre-fill equity may satisfy a guard even if the new fill's costs subsequently
put it below that guard. Exits never pass entry risk gating: an exit proceeds
under existing execution rules even after a limit is breached. Risk adds no
forced liquidation, pyramiding, simultaneous positions or same-open reversal.
Existing execution-price compatibility and accounting validation still apply.

## Phase 10 propagation, determinism and errors

Nesting the policy in BacktestConfig lets holdout, walk-forward and robustness
reuse it without changing their orchestration. Reports retain that configuration,
and each segment/fold/candidate result retains its policy and decisions. Every
independent replay starts flat with its initial capital and a fresh peak; risk
state and rejected intent cannot carry across a boundary.

Risk models reuse Pydantic v2 strict, frozen, extra-forbidden, always-revalidate
contracts. Financial fields remain finite Decimal values, timestamps are aware
UTC and collections are tuples. Typed JSON round trips preserve the models.
Public evaluation revalidates even unchecked copies or constructions, and never
mutates inputs. Malformed direct construction raises Pydantic ValidationError;
`evaluate_entry_risk` raises RiskInputError under RiskError (ValueError). Invalid
nested risk config at the backtest boundary becomes BacktestInputError; Phase 10
uses its existing ResearchValidationInputError boundary. Valid rejection is an
audited decision, not an exception.

Hard-limit comparisons use exact rational integer ratios of Decimal inputs,
including products and the drawdown inequality. They do not round an excess
away at precision 34 or perform division to decide a threshold. No financial
input/output is converted to binary float. Existing account and cost arithmetic
still uses fresh precision-34 ROUND_HALF_EVEN Decimal contexts, independent of
caller precision, rounding, traps and flags. Decisions can be reconstructed from
their exact operands and the retained policy. Same typed inputs produce equal
results and identical JSON. No network, random value, wall-clock state, new
dependency or caller Decimal setting enters risk evaluation.

## Explicit deferrals

Stop-loss/take-profit execution remains unsupported. Capital-at-risk needs an
executable loss boundary, so `max_capital_at_risk_per_trade` is not an accepted
configuration field. Notional is not renamed as capital-at-risk. Unsupported
controls are rejected by the strict schema rather than silently ignored.

Daily/session loss, cumulative realized-loss controls, dynamic sizing, liquidation,
portfolio/multi-asset risk, margin/leverage models, currency valuation, stale-feed
rules, statistical VaR/CVaR, Kelly, risk parity, ML, LLMs, agents, live/paper trading,
APIs and frontend are outside Phase 11. Existing private pricing helpers are
implementation details, not authorized execution entry points. Future services
must call the mandatory risk-enforcing execution path and cannot accept caller
overrides of its decision or account context.
