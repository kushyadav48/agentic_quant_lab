# Portfolio management and multi-strategy foundation (Phase 19)

`quantlab.portfolio` consolidates independent bounded paper execution sessions.
It is a synchronous trusted local Python reporting owner, with fixed allocation
and risk-budget admission. It never writes to an account or session, creates a
strategy approval, starts/resumes a session, submits an order or grants execution
authority. Phases 1–18 retain their existing contracts and publication boundaries.

## Public contracts

| Contract | Responsibility |
| --- | --- |
| `PortfolioConfig` | Portfolio identity, denomination, declared total capital, absolute risk limits, initial logical time, maximum valuation age, member/operation bounds. |
| `RiskBudget` | Nonnegative encumbered-capital and unsigned gross-exposure limits in the declared denomination. Both must be explicit, including zero. |
| `PortfolioMember` | Fixed member identity, exact Phase 18 replay configuration, approved `StrategySpecification`, admitted `AdmissionRecord` and owned risk budget. Capital is exactly the account's initial capital. |
| `EnrollMember` | Reserve fixed ownership allocations before adding an independently owned account/session. |
| `ObserveSession` | Consume the next retained `SessionRecord` in that member's chain, preserving account economics and session provenance. |
| `ValueMember` | Explicit `MarketDelivery` and configured `FeedProvenance` for a portfolio reporting mark on an observed open position. |
| `RefreshPortfolio` | Advance reporting time and reassess freshness without fabricating market data. |
| `MemberView` | Approved membership, reconciled account snapshot, session state/feed/record identity/time, optional independent reporting quote, retained explicit quote head and current valuation metrics. |
| `PortfolioSnapshot` | Sorted current membership, exact consolidated account partitions, last-reported amounts, current liquidation equity/P&L/exposure or explicit unavailable values, and budget breaches. |
| `PortfolioEvent` | Content-addressed accepted operation, predecessor identity and deterministic candidate snapshot digest. |
| `Portfolio` | Serialized processing, immutable current snapshot, consumer-requested event journal and verified fresh-owner replay. |

All inputs inherit the existing strict, frozen, extra-forbidden and deeply
revalidated domain contracts. Public collections are tuples. Invalid model copies
are revalidated at the boundary. Canonical identities reuse Phase 18's serializer
and its 4096-digit Decimal/4 MiB record bounds. No dependency is added.

## Fixed ownership and admission

Each membership owns one unique account and session within the portfolio. The
same exact strategy may have independent execution instances, but a strategy ID
cannot silently denote different versions, content or approval attribution in one
portfolio. An instrument ID cannot denote conflicting metadata. Different
supported instruments are allowed only in independent accounts with the same
denomination. AccountConfig continues to require linear EQUITY research units,
quote denomination, multiplier one and no base currency. Existing v1/v2/v3 replay
configurations and separately approved advanced-entry authorizations are retained.

Enrollment requires admitted exact strategy/config/account/session bindings,
nonempty evidence references, policy/eligibility causation and formal approval
before activation. These are retained trusted producer attestations; membership
does not independently authenticate reviewers or rerun research admission. A
producer obtains them from an actual admitted paper session. Portfolio enrollment
grants no permission to create another trading owner.

Admission enforces:

- Sum of member initial capital <= declared portfolio capital.
- Each encumbrance budget <= that member's initial capital.
- Sum of each kind of member risk budget <= the corresponding portfolio limit.
- Portfolio encumbrance limit <= total capital; maximum 32 members by default.
- Unique member/account/session ownership, compatible currency and activation time.

These are static ownership allocations, not cash transfers. The unallocated
remainder is a declared reporting reserve. The trusted caller must ensure that
declared capital represents disjoint funding and that account ownership is not
duplicated outside this owner. There is no process-wide account registry. Already
committed strategy operations are observed faithfully even if they exceed a
member's budget: risk breaches are reported, not hidden by dropping financial
facts. Portfolio budgets do not intercept execution; each paper session retains
its Phase 11/18 risk gates. There is no coordinated multi-session trade admission.

## Financial definitions

All arithmetic uses Phase 18's fresh 4096-digit exact context with Inexact trapped.
Caller precision, rounding, flags and traps cannot affect results. For members i,
initial capital C_i, balance B_i, realized gross P&L R_i, fees F_i, reservation H_i,
entry-basis collateral K_i and reporting unrealized P&L U_i:

```text
unallocated = total_capital - sum(C_i)
balance = unallocated + sum(B_i)
available_funds = unallocated + sum(B_i - H_i - K_i)
reserved_funds = sum(H_i)
position_collateral = sum(K_i)
encumbered = reserved_funds + position_collateral
realized_pnl = sum(R_i)
fees_paid = sum(F_i)
equity = balance + sum(U_i)
net_pnl = sum(R_i) - sum(F_i) + sum(U_i) = equity - total_capital
```

Collateral and holds partition balance; neither is added to equity. Fees are
already deducted in each account balance and are not charged again during
aggregation. Short proceeds do not create funding. Available funds are an
informational total: a profitable member cannot fund another member's operation,
and unallocated capital is not spendable by a paper owner.

Valuation reuses the Phase 18 liquidation formulas: long quantity * (bid - entry
basis); short quantity * (entry basis - ask). Gross exposure is the sum of quantity
* liquidation price, without offsetting opposite directions. The position,
direction, instrument and quantity remain visible per account; no fungible
cross-account position or collateral pool is created. Spread affects liquidation
P&L through bid/ask; future exit fees are not forecast. These are research units,
not brokerage cash or margin semantics.

## Valuation and causal ordering

Each accepted operation has a unique caller-supplied ID, strictly increasing
portfolio sequence and nondecreasing UTC logical time. Equal times are ordered by
sequence. Session chains retain their independent sequences and must be consumed
from the initial state without missing records. Session inputs/digests, financial
event links, account heads and decision attribution are verified. Observations
cannot be ahead of portfolio reporting time.

A member without a session record is `missing`; its initial account amounts remain
reported but are not current consolidated valuations. A member is `stale` when
its latest session record exceeds the declared age, or when an active position's
quote observation exceeds that age. The exact age boundary is fresh. Flat accounts
need recent session progress but no invented quote. Heartbeats refresh session
status, never position marks. Stop does not imply liquidation.

An explicit portfolio quote must bind the member's instrument and declared
dataset/source/version provenance, be delivered/available by reporting time, and
not precede entry or its prior mark. Explicit quote sequences form a per-member
portfolio valuation domain; they are not compared to account/kernel sequences.
The most recent explicit quote is retained as `valuation_head` even after an
account mark supersedes it. Subsequent explicit marks must advance that domain's
sequence and preserve its observation/availability/delivery/processing chronology.
Account/kernel sequences remain separate. Incompatible or
future inputs fail without publication. A valid but stale quote may be retained
and stays unavailable for current metrics.

Without an explicit mark, account-position valuation is used. Reporting marks
never mutate the account. New financial observations retain an explicit mark for
the same position, unless the position closes/changes or the account supplies a
changed valuation with an equal/newer observation timestamp; the newly observed
account mark then takes precedence. An unchanged account observation, including a
heartbeat or quote with no settlement/mark, preserves the explicit reporting mark.
This tie policy follows accepted portfolio event order, with no history rewrite or
retrospective execution. Clearing an active reporting mark never clears its
retained explicit quote head or relaxes subsequent chronology checks.

If any member is missing/stale, consolidated `equity`, `unrealized_pnl`, `net_pnl`
and `gross_exposure` are None. `reported_equity` and `reported_unrealized_pnl`
remain sums of the last accepted account amounts, including the unallocated
reserve in reported equity. They are labeled separately from current reporting
values. Balance, fees, realized P&L, collateral and reservations remain reported
financial facts. Risk breaches distinguish member/portfolio encumbrance and gross
limits and unavailable valuation; unavailable exposure never appears as zero.

There is no FX conversion. Timestamped currency valuations are supported only
when every account/quote has the portfolio's exact denomination.

## Publication, replay and recovery

Preparation validates the complete new input, bounded current member views,
aggregate economics, event encoding and retry/history candidates before one root
assignment publishes them together. Append-only `History` and persistent
`RetainedMap` are reused from Phase 18. Unexpected failures, including an injected
exception after assignment, restore the prior root. Reentrancy is rejected. Exact
retries return the retained event before new clock/capacity checks; changed content
under an existing operation ID raises `PortfolioIdentityConflict`.

`Portfolio.replay(config, tuple_of_events)` verifies ordering, event content,
aggregate digests and deterministic results into a new independent owner. It
rejects duplicate/missing/reordered/conflicting journal records. The candidate
snapshot digest uses its predecessor event identity; the published snapshot then
contains the new event identity, avoiding a circular hash.

Portfolio state is in memory only. Caller-retained journals are not durable
storage, transactional exports, authenticated attestations or crash guarantees.
Existing durable paper sessions retain their SQLite recovery guarantees and
operator pause/resume requirements. Their recovered records can reconstruct the
same portfolio view; portfolio replay cannot resume those sessions. No atomic
transaction spans multiple sessions, portfolios, storage or processes.

## Bounds and verification

Default bounds are 32 members and 20,000 accepted portfolio operations, with a
maximum configurable operation bound of 100,000. Current processing depends on
bounded membership/configuration width and the new supplied record, not retained
portfolio history. A retry-index update copies 64 trie nodes of at most 16 children;
history append does not copy the retained prefix. Current snapshots exclude event
history. Each member retains at most one explicit quote head in addition to the
active reporting mark; this adds bounded current state, never a quote-history copy.
Consumer-requested journals and replay intentionally traverse history;
retention memory grows with accepted input sizes. Legacy session records carrying
full execution snapshots cost their supplied size to revalidate; normal Phase 18
records use bounded execution heads.

The deterministic suite covers allocation conservation, competing risk budgets,
approval/strategy conflicts, member isolation, exact long/short costs and P&L,
reservations/collateral, valuations/provenance, freshness/clock boundaries,
immutable snapshots, hostile Decimal contexts, duplicate operations, ordered
replay, v1/v2/v3 sessions, durable-session recovery integration, publication failure
injection and growing-history structural checks. See [phase19-report.md](phase19-report.md)
for exact results and measured performance.

Dynamic reallocation, membership removal, funding transfers, historical portfolio
return/drawdown/Sharpe series, portfolio persistence, distributed coordination,
shared collateral, cross-currency valuation and trade routing remain deferred.
No live trading, broker connectivity, dashboard/API or MCP permissions are added.
