# Deterministic execution costs (Phase 8)

Phase 8 extends the Phase 7 execution boundary without changing strategy evaluation,
next-open timing, fixed quantity, or the single-position lifecycle. This is an offline
deterministic research execution simulator, not a broker-grade microstructure simulator.
No dependencies, network calls, randomness, timestamps or environment-derived IDs are added.

## Configuration and public records

~~~python
from decimal import Decimal
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig, run_backtest

config = BacktestConfig(
    initial_capital=Decimal("1000"), quantity=Decimal("2"),
    execution_costs=ExecutionCostConfig(
        spread=Decimal("2"), slippage=Decimal("0.5"),
        commission_per_unit=Decimal("0.25"), fixed_fee_per_fill=Decimal("1"),
    ),
)
# Nonzero spread requires MID, BID or ASK bars, never TRADE bars.
result = run_backtest(approved_strategy, bars, features,
                      instrument=instrument, config=config)
~~~

ExecutionCostConfig is strict, frozen, extra-forbidden and revalidated at the engine
boundary. All four fields default to Decimal zero and require finite nonnegative
Decimal values under Python construction. Floats, integers, booleans and numeric strings
are rejected; typed JSON wire representations round-trip using the existing domain policy.
Config and result retain execution_costs, including for runs without fills.

spread is the full synthetic bid/ask spread in absolute price units. slippage is an
absolute adverse price-unit amount per fill. Neither is a percentage, pip count, basis
point value, volatility estimate or spread inferred from OHLC. Explicit commission and
fees use the same research accounting/P&L unit as Phase 7. Quantity is the existing
research quantity, constrained by Instrument.quantity_increment. No contract multiplier,
FX conversion or account currency valuation is introduced.

Fill retains execution_price and adds reference_price, spread_adjustment,
slippage_adjustment and costs. Adjustments are nonnegative price-unit magnitudes;
costs are quantity-scaled accounting amounts. Position retains entry_price and adds
entry_reference_price and entry_costs. ClosedTrade adds exit_reference_price,
exit_costs, reference_gross_pnl and net_pnl. These reference fields are required when
constructing events directly; existing run_backtest callers need no changes.

CostBreakdown stores spread_cost, slippage_cost, commission and fees as finite
nonnegative Decimals. Its explicit_cost property returns commission + fees and its
total_cost property returns spread_cost + slippage_cost + commission + fees.
ClosedTrade.costs combines entry_costs and exit_costs component by component.
These derived properties are computed on demand, not redundant serialized fields;
JSON records contain all components needed to reconstruct them.

## Execution formulas

A decision at bar N close produces a pending Signal. Only the following supplied
bar's open can produce a Fill. Let R = that open, Q = quantity, S = configured full
spread, and L = configured slippage.

BUY actions are ENTER_LONG and EXIT_SHORT. SELL actions are ENTER_SHORT and EXIT_LONG.
The nonnegative spread adjustment A is:

| Reference basis | BUY A | SELL A |
| --- | ---: | ---: |
| MID | S / 2 | S / 2 |
| BID | S | 0 |
| ASK | 0 | S |
| TRADE | 0, only when S = 0 | 0, only when S = 0 |

After spread, BUY price = R + A; SELL price = R - A.
After adverse slippage, BUY execution_price = R + A + L;
SELL execution_price = R - A - L. Effects are applied in that order.
Zero adjustments skip arithmetic to preserve the original Phase 7 open exactly,
even when it contains more than 34 significant digits.

Per actual fill:

- spread_cost = A * Q.
- slippage_cost = L * Q.
- commission = commission_per_unit * Q.
- fees = fixed_fee_per_fill.
- total_cost = spread_cost + slippage_cost + commission + fees.

A BID sell or ASK buy incurs no synthetic spread adjustment. This avoids applying
half a spread indiscriminately to a side-specific price. This audit measures cost
relative to the declared reference series, not an inferred mid-price benchmark.
Nonzero synthetic spread on TRADE bars raises BacktestCompatibilityError before
replay, including when there would be no fills. Zero spread with TRADE bars supports
slippage, commissions and fees. Nonpositive execution prices raise BacktestInputError.

## Trade P&L and accounting identities

Let E and X be actual execution entry/exit prices, and RE and RX their causal
reference opens. Combine both fill cost breakdowns into C.

- LONG gross_pnl = (X - E) * Q.
- SHORT gross_pnl = (E - X) * Q.
- LONG reference_gross_pnl = (RX - RE) * Q.
- SHORT reference_gross_pnl = (RE - RX) * Q.
- net_pnl = gross_pnl - C.commission - C.fees.
- net_pnl = reference_gross_pnl - C.spread_cost - C.slippage_cost - C.commission - C.fees.

Spread/slippage already worsen execution prices. Subtracting them again from
gross_pnl would double count. Public Fill and Position validators reconcile
price effects with quantity-scaled costs. ClosedTrade validates both gross formulas
and both net identities. Derived totals cannot disagree with stored components.

For a MID long at reference entry 100 and reference exit 90, quantity 2, spread 2,
slippage 0.5, per-unit commission 0.25 and fee 1:
entry execution = 101.5, exit execution = 88.5; reference gross = -20,
gross = -26; combined spread cost = 4, slippage cost = 2, commission = 1,
fees = 2; net = -29. Capital 1000 finishes at 971. The symmetric short with
reference entry 100 and reference exit 110 has executions 98.5 and 111.5 and
identical P&L/cost amounts.

## Entry costs, open positions and equity

realized_pnl means cumulative realized account P&L, including costs already paid:

- At entry: subtract entry commission and fee immediately.
- While open: LONG unrealized = (current close mark - actual entry price) * Q;
  SHORT unrealized = (actual entry price - current close mark) * Q.
- At exit: add gross execution-price P&L, subtract exit commission and fee.
  Entry explicit costs were already recognized and are not deducted again.
- equity = initial_capital + realized_pnl + unrealized_pnl.

Equivalently, realized account P&L includes net P&L of all closed trades and
subtracts the entry explicit costs of any current open position. Flat unrealized
is zero. Close marks remain retrospective Phase 7 research reporting, including
marks on delayed bars; they do not become strategy decision inputs or hypothetical exit prices.
Phase 11 separately uses on-time close observations for a causal runtime risk peak;
delayed retrospective marks never enter that peak. These marks do not determine
entry price or strategy sizing. An equity point is recorded at each bar end,
after any actual open fill.
Capital is not debited on entry, and negative equity has no invented liquidation policy.

End-of-data positions stay open. Only their actual entry spread/slippage and cash
costs apply; no exit spread, slippage, commission or fee is estimated or fabricated.
A final-bar entry or exit Signal has no following bar, hence no Fill or associated
cost. Any earlier actual fills still retain their incurred costs.

## Causality and determinism

RuleEvaluator remains separate from execution.py; accounting remains in engine.py.
Execution reads only the allowed next open, start time, price basis, quantity and
immutable configuration. High, low, close, volume and feature values never determine
spread/slippage. Complete-bar available_at does not gate the assumed next-open fill;
it continues to gate that bar's close decision. Exact feature timestamps, all
availability/dependency checks, conflict detection and causal prefixes remain intact.
No same-open reversal, pyramiding, partial fill or end-of-data forced close is added.

Default zero costs preserve Phase 7 signals, timing, execution prices, economic
positions/trades, gross/realized/unrealized P&L and equity; metadata is additive.
Identical strategy, instrument, bars, features and config produce equal results and
identical JSON. Inputs are never mutated.

All financial arithmetic and model arithmetic validators/properties use fresh isolated
Decimal contexts: precision 34, ROUND_HALF_EVEN, independent of caller precision,
rounding, traps and flags. Quantity divisibility still uses exact integer ratios.
Operations round at the declared precision. Extreme scales that make fill price
effects or the two trade net identities fail to reconcile are rejected explicitly
as BacktestInputError during replay; exponent overflow during execution is also
an input error. This includes positive price adjustments too small to be represented
against the reference price. No adjustment is silently charged while disappearing
from execution price. Direct malformed model construction raises Pydantic ValidationError.

## Limitations

Costs are constant configured assumptions; there is no live bid/ask inference,
order book, stochastic slippage, market impact, liquidity/volume participation,
partial fills, limit/stop orders, latency simulation, stops/targets, financing,
overnight swaps, borrow costs, margin, leverage, currency conversion or portfolio.
Prices are not rounded to Instrument.tick_size; tick_size and contract_multiplier
retain their existing metadata role. Research equity is not a brokerage cash ledger.
Phase 9 analytics consumes completed BacktestResult in a separate layer; see
[net performance formulas and open-position policy](performance-analytics.md).
Walk-forward validation is provided by [Phase 10 research validation](research-validation.md).
[Phase 11 risk](risk-engine.md) evaluates each proposed entry before pricing and costs,
using the unadjusted reference open and current pre-fill equity. Rejected entries
create no fill and incur none of these costs. Allowed entries retain the complete
fixed quantity and unchanged formulas; exits never pass entry risk gating. A
reference-notional cap does not cap spread/slippage-adjusted fill notional.
ML, agents, paper trading and APIs remain planned.
Fixed execution costs alone do not establish live realism.
