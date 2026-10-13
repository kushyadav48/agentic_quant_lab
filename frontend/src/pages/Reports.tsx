import { useState, type ReactNode } from 'react';
import type { APIClient, Capabilities, Portfolio, PaperReport } from '../api/client';
import { Badge, Empty, Facts, JSONDetails, LoadState, Metrics, Panel, useResource } from '../components';
import { decimal, utc } from '../format';
import { RecordChart, accountPoints } from '../RecordChart';

export function PortfolioView({ value, history }: { value: Portfolio; history?: ReactNode }) {
  const unavailable = value.members.some(member => member.valuation_status !== 'fresh');
  return <>
    <Metrics values={{ Balance: decimal(value.balance), 'Current equity': unavailable ? 'Unavailable' : decimal(value.equity),
      'Gross exposure': unavailable ? 'Unavailable' : decimal(value.gross_exposure), 'Realized P&L': decimal(value.realized_pnl) }} />
    <div className="metric-caption">{value.config.denomination} denomination · backend snapshot as of {utc(value.timestamp)} · no browser accounting</div>
    {history}
    <Panel title="Capital & risk"><Facts value={{ denomination: value.config.denomination, as_of: utc(value.timestamp), available_funds: decimal(value.available_funds),
      allocated_capital: decimal(value.allocated_capital), unallocated_capital: decimal(value.unallocated_capital),
      reservations: decimal(value.reserved_funds), collateral: decimal(value.position_collateral), fees_paid: decimal(value.fees_paid),
      net_pnl: unavailable ? 'Unavailable' : decimal(value.net_pnl), last_event_id: value.last_event_id }} />
      {unavailable && <div className="notice warning">Missing or stale valuation. Current equity, P&L and exposure are unavailable. Recorded balance and realized facts remain visible.</div>}
      {value.risk_breaches.length ? <div className="notice warning">{value.risk_breaches.join(' · ')}</div> : <Badge tone="positive">No reported budget breach</Badge>}
    </Panel>
    <Panel title="Portfolio membership · static allocations" action={<Badge>{value.members.length} MEMBERS</Badge>}><div className="table-scroll"><table><thead><tr><th>Member / strategy</th><th>Instrument</th><th>Capital</th><th>Balance</th><th>Equity</th><th>Exposure</th><th>Realized P&L</th><th>Valuation</th></tr></thead>
      <tbody>{value.members.map(view => <tr key={view.member.member_id}><td><span className="member-name"><span className="member-avatar">{view.member.member_id.slice(0, 2).toUpperCase()}</span><span>{view.member.member_id}<small>{view.member.strategy.strategy_id} · v{view.member.strategy.version} · {view.member.strategy.content.direction}</small></span></span></td><td>{view.account.config.instrument.symbol}<small>{view.account.config.instrument.asset_class}</small></td>
        <td>{decimal(view.member.config.strategy.account.starting_capital)}</td><td>{decimal(view.account.balance)}</td><td>{view.valuation_status === 'fresh' ? decimal(view.equity) : 'Unavailable'}</td><td>{view.valuation_status === 'fresh' ? decimal(view.gross_exposure) : 'Unavailable'}</td><td className={view.account.realized_pnl.startsWith('-') ? 'negative-value' : ''}>{decimal(view.account.realized_pnl)}</td><td><Badge tone={view.valuation_status === 'fresh' ? 'positive' : 'warning'}>{view.valuation_status}</Badge></td></tr>)}</tbody></table></div>
      {!value.members.length && <Empty>No enrolled members.</Empty>}<JSONDetails value={value} label="portfolio provenance and member budgets" />
    </Panel>
  </>;
}
export function PaperView({ value }: { value: PaperReport }) {
  const snapshot = value.snapshot;
  const stale = snapshot.feed.reason !== 'fresh';
  return <>
    <div className="notice"><Badge tone="warning">SIMULATED / PAPER</Badge> Recorded state as of {utc(snapshot.clock.timestamp)}. Feed: {snapshot.feed.reason}. Refresh is manual.</div>
    {value.operator_required && <div className="notice warning" role="alert">Operator recovery required. Use the existing trusted local recovery workflow; HTTP cannot resume this session.</div>}
    <Metrics values={{ Balance: decimal(snapshot.account.balance), 'Recorded equity': decimal(snapshot.account.equity), 'Realized P&L': decimal(snapshot.account.realized_pnl), 'Fees paid': decimal(snapshot.account.fees_paid) }} />
    {stale && <div className="notice warning">Feed is {snapshot.feed.reason}. Recorded equity is a historical account mark, not a current valuation.</div>}
    <Panel title="Recorded session equity" action={<Badge>ACCOUNT MARKS · UTC</Badge>}><RecordChart points={accountPoints(value)} label="Paper account equity" unit={snapshot.account.config.denomination} /></Panel>
    <Panel title="Account & reservations"><Facts value={{ state: snapshot.state, account: snapshot.account.config.account_id, denomination: snapshot.account.config.denomination,
      available_funds: decimal(snapshot.account.available_funds), reserved_funds: decimal(snapshot.account.reserved_funds), collateral: decimal(snapshot.account.position_collateral),
      strategy: snapshot.config.strategy.strategy_id, strategy_version: snapshot.config.strategy.strategy_version, strategy_digest: snapshot.config.strategy.strategy_digest }} />
      <JSONDetails value={snapshot.account.reservations} label="fund reservations" />
    </Panel>
    <div className="two-col"><Panel title="Position">{snapshot.account.position ? <><div className="position-heading"><span className="member-avatar">{snapshot.account.config.instrument.symbol.slice(0, 2)}</span><strong>{snapshot.account.config.instrument.symbol}</strong><Badge>{snapshot.account.position.direction.toUpperCase()}</Badge></div><Facts value={{ quantity: decimal(snapshot.account.position.quantity), entry_basis: decimal(snapshot.account.position.entry_basis), unrealized_pnl: decimal(snapshot.account.position.unrealized_pnl), entry_time: utc(snapshot.account.position.entry_time) }} /><JSONDetails value={snapshot.account.position} label="recorded position and liquidation quote" /></> : <Empty>No open position. Future recorded fills appear here; this interface does not place orders.</Empty>}</Panel>
    <Panel title="Orders & protective exits"><div className="order-status"><span className="eyebrow">RECORDED EXECUTION</span><strong>{snapshot.execution.kernel.state?.replaceAll('_', ' ') ?? 'Not recorded'}</strong><p className="muted">Protective exits and OCO appear only when recorded by the Python owner.</p></div><JSONDetails value={{ execution: snapshot.execution, exit_order: snapshot.exit_order, oco: snapshot.oco }} label="order lifecycle, stops and take-profits" /></Panel></div>
    <Panel title="Strategy, risk & feed"><JSONDetails value={{ runtime: snapshot.runtime, feed: snapshot.feed, config: snapshot.config }} label="admission and risk evidence" /></Panel>
    <Panel title="Recorded activity"><RecordTable value={value} /></Panel>
  </>;
}
function RecordTable({ value }: { value: PaperReport }) {
  const [page, setPage] = useState(0);
  const records = value.snapshot.records;
  const shown = records.slice(page * 20, page * 20 + 20);
  return <><div className="table-scroll"><table><thead><tr><th>Sequence</th><th>Causal time · UTC</th><th>Reason</th><th>Orders</th><th>Details</th></tr></thead><tbody>
    {shown.map(record => <tr key={record.record_id}><td>{record.sequence}</td><td>{utc(record.timestamp)}</td><td>{record.reason}</td><td>{record.orders.length}</td><td><JSONDetails value={record} label={`event ${record.sequence}`} /></td></tr>)}
  </tbody></table></div>{!records.length && <Empty>No committed activity.</Empty>}<div className="pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>{records.length ? page * 20 + 1 : 0}–{Math.min(page * 20 + 20, records.length)} of {records.length}</span><button disabled={(page + 1) * 20 >= records.length} onClick={() => setPage(page + 1)}>Next</button></div></>;
}
export function Reports({ api, capabilities, kind, onDemo }: { api: APIClient; capabilities: Capabilities; kind: 'portfolio' | 'paper'; onDemo?: () => void }) {
  const identities = kind === 'portfolio' ? capabilities.portfolios : capabilities.paper_sessions;
  const [id, setId] = useState(identities[0] ?? '');
  return <>
    <div className="page-heading"><div><span className="eyebrow">{kind === 'portfolio' ? 'CAPITAL / EXPOSURE / RISK' : 'ACCOUNTS / EXECUTION / EVIDENCE'}</span><h1>{kind === 'portfolio' ? 'Portfolio overview' : 'Paper sessions'}</h1><p>{api.source === 'demo' ? 'Fixed synthetic records. Explore the layout without an API or real account.' : 'Authoritative snapshots, exact values, visible provenance.'}</p></div><Badge tone="warning">{api.source === 'demo' ? 'SYNTHETIC DEMO' : 'SIMULATED / PAPER'}</Badge></div>
    {!identities.length ? <Panel title="Service catalogue"><div className="catalogue-empty"><span className="empty-orbit" aria-hidden="true">◈</span><Empty>No {kind === 'portfolio' ? 'portfolios' : 'paper sessions'} registered in this API process. Owners must be injected by the trusted Python application.</Empty><p className="muted">Your API is connected. Account data appears after the trusted application registers its existing owners. Missing balances and history are never replaced with demo figures.</p>{onDemo && <button className="primary" onClick={onDemo}>Switch to synthetic demo</button>}<small>Switching clears the API credential and opens a separate read-only fixture workspace.</small></div></Panel> : <><div className="report-selector"><label className="inline-field">{kind === 'portfolio' ? 'Portfolio' : 'Session'}<select value={id} onChange={event => setId(event.target.value)}>{identities.map(identity => <option key={identity}>{identity}</option>)}</select></label><Badge>{api.source === 'demo' ? 'FIXED PYTHON FIXTURE' : 'REGISTERED PYTHON OWNER'}</Badge></div>
      <ReportResource key={id} api={api} id={id} kind={kind} capabilities={capabilities} /></>}
  </>;
}
function ReportResource({ api, id, kind, capabilities }: { api: APIClient; id: string; kind: 'portfolio' | 'paper'; capabilities: Capabilities }) {
  const resource = useResource<Portfolio | PaperReport>(signal => kind === 'portfolio' ? api.portfolio(id, signal) : api.paper(id, signal), [api, id, kind]);
  return <><div className="page-actions"><button onClick={resource.refresh} disabled={resource.loading}>Refresh snapshot</button></div>
    <LoadState {...resource}>{resource.data && (kind === 'portfolio' ? <PortfolioView value={resource.data as Portfolio} history={<PortfolioHistory api={api} value={resource.data as Portfolio} capabilities={capabilities} />} /> : <PaperView value={resource.data as PaperReport} />)}</LoadState></>;
}
function PortfolioHistory({ api, value, capabilities }: { api: APIClient; value: Portfolio; capabilities: Capabilities }) {
  const sessions = value.members.map(member => member.member.config.strategy.session_id).filter(id => capabilities.paper_sessions.includes(id));
  const [id, setId] = useState(sessions[0] ?? '');
  return <div className="terminal-main-grid"><Panel title="Recorded session equity" action={<Badge>{api.source === 'demo' ? 'SYNTHETIC HISTORY' : 'PAPER HISTORY'}</Badge>}>
    {sessions.length ? <><label className="chart-source">Account history<select value={id} onChange={event => setId(event.target.value)}>{sessions.map(session => <option key={session}>{session}</option>)}</select></label><SessionHistory key={id} api={api} id={id} /></> : <Empty>No linked paper history is registered. API v1 exposes the current portfolio snapshot; it has no aggregate portfolio equity-history endpoint.</Empty>}
  </Panel><div className="terminal-side"><Panel title="Capital allocation" action={<span className="eyebrow">{value.config.denomination}</span>}><div className="allocation-total"><span>Portfolio capital</span><strong>{decimal(value.config.total_capital)}</strong></div>
    {value.members.map((member, index) => { const amount = member.member.config.strategy.account.starting_capital;
      // Ratio changes bar pixels only; monetary labels remain exact backend fields.
      const ratio = Number(amount) / Number(value.config.total_capital);
      const width = Number.isFinite(ratio) ? Math.max(0, Math.min(100, ratio * 100)) : 0;
      return <div className="allocation-row" key={member.member.member_id}><div><span><i className={`allocation-dot color-${index % 3}`} />{member.member.member_id}</span><b>{decimal(amount)}</b></div><div className="allocation-track"><span className={`color-${index % 3}`} style={{ width: `${width}%` }} /></div></div>; })}
    <div className="allocation-row"><div><span>Unallocated</span><b>{decimal(value.unallocated_capital)}</b></div></div><small>Static allocation bars are approximate. Values are exact.</small></Panel>
    <Panel title="Valuation & risk"><div className="health-row"><span>Fresh member marks</span><strong>{value.members.filter(member => member.valuation_status === 'fresh').length} / {value.members.length}</strong></div><div className="health-row"><span>Recorded budget breaches</span><strong className={value.risk_breaches.length ? 'negative-value' : 'positive-value'}>{value.risk_breaches.length}</strong></div><div className="health-row"><span>Refresh policy</span><Badge>MANUAL</Badge></div><p className="muted">Stale or missing member marks suppress current valuation metrics.</p></Panel></div></div>;
}
function SessionHistory({ api, id }: { api: APIClient; id: string }) {
  const resource = useResource(signal => api.paper(id, signal), [api, id]);
  return <LoadState {...resource}>{resource.data && <><RecordChart points={accountPoints(resource.data)} label="Selected paper account equity" unit={resource.data.snapshot.account.config.denomination} />
    <div className="recent-feed"><span className="eyebrow">LATEST RECORDED ACTIVITY</span>{resource.data.snapshot.records.slice(-3).reverse().map(record => <div key={record.record_id}><span className="activity-dot" /><strong>{record.reason.replaceAll('_', ' ')}</strong><small>#{record.sequence} · {utc(record.timestamp)}</small></div>)}</div><p className="chart-disclaimer">This chart belongs to one selected paper account; it is not aggregate portfolio equity.</p></>}</LoadState>;
}
