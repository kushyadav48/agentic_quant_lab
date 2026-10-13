import { useCallback, useMemo, useState } from 'react';
import type { APIClient, Capabilities, PaperReport } from '../api/client';
import { Badge, Empty, Facts, JSONDetails, LoadState, Panel, useResource } from '../components';
import { debugData, type DebugEvent } from '../debugger/data';
import { Chart } from '../debugger/Chart';
import { decimal, utc } from '../format';

export default function Debugger({ api, capabilities }: { api?: APIClient; capabilities?: Capabilities }) {
  const [source, setSource] = useState(api?.source === 'demo' ? capabilities?.paper_sessions[0] ?? '' : '');
  return <><div className="page-heading"><div><span className="eyebrow">Observe · trace · explain</span><h1>Visual strategy debugger</h1><p>Historical observations and causal records, side by side.</p></div><Badge tone="warning">SIMULATED / HISTORICAL</Badge></div>
    <Panel title="Recorded data source"><label>Chart source<select value={source} onChange={e => setSource(e.target.value)}><option value="">Choose a source</option>{capabilities?.paper_sessions.map(id => <option value={id} key={id}>{api?.source === 'demo' ? 'SYNTHETIC DEMO' : 'API paper snapshot'} · {id}</option>)}<option value="__offline_fixture__">OFFLINE DEMO · deterministic test-service fixture</option></select></label>
      <p className="muted">Paper snapshots expose recorded quotes, complete bar closes, decisions and order events. No standalone market-data or backtest-input history route exists. Unrecorded candles, intrabar paths, execution prices and timestamps cannot be reconstructed.</p>
    </Panel>{!source ? <Empty>Select an API snapshot or the explicitly labeled offline fixture.</Empty> : <Source key={source} source={source} api={api} />}
  </>;
}
function Source({ source, api }: { source: string; api?: APIClient }) {
  const offline = source === '__offline_fixture__';
  const demo = api?.source === 'demo';
  const resource = useResource<PaperReport>(async signal => {
    if (offline) return (await import('../debugger/offline.json')).paper as unknown as PaperReport;
    if (!api) throw new Error('Connect an API credential to inspect this source.');
    return api.paper(source, signal);
  }, [api, source]);
  return <><div className={`notice ${offline || demo ? 'warning' : ''}`}><Badge tone="warning">{offline ? 'OFFLINE DEMO' : demo ? 'SYNTHETIC DEMO SNAPSHOT' : 'API PAPER SNAPSHOT'}</Badge>{offline || demo ? 'Fixed synthetic fixture produced by deterministic Python test services. Never live data. It demonstrates only the records present in that fixture.' : 'Historical simulated records from the registered Python owner. Manual refresh only.'}</div>
    {!offline && <button onClick={resource.refresh} disabled={resource.loading}>Refresh source</button>}
    <LoadState {...resource}>{resource.data && <Visualization report={resource.data} />}</LoadState></>;
}
export function Visualization({ report }: { report: PaperReport }) {
  const data = useMemo(() => debugData(report), [report]);
  const [selected, setSelected] = useState<DebugEvent>();
  const [page, setPage] = useState(0);
  const choose = useCallback((event: DebugEvent) => setSelected(event), []);
  const cfg = report.snapshot.config;
  const prices = data.observations;
  return <>
    <Panel title="Historical price & execution"><Facts value={{ session_id: cfg.strategy.session_id, strategy_id: cfg.strategy.strategy_id, strategy_version: cfg.strategy.strategy_version, strategy_digest: cfg.strategy.strategy_digest, as_of: utc(report.snapshot.clock.timestamp), records: report.snapshot.records.length }} />
      {prices.length || data.entries.length || data.exits.length ? <Chart data={data} selected={selected} choose={choose} /> : <Empty>No recorded price observations. Events remain available below.</Empty>}
      <p className="muted">Chart coordinates use approximate JavaScript numbers; exact decimal strings are retained in details and tables. Connecting lines are visual guides between recorded observations. No intrabar path is asserted. Events without execution prices appear only in the event timeline.</p>
      {!!data.omitted && <div className="notice warning">{data.omitted} chart points omitted for unsupported numeric range or duplicate series timestamps. All source records remain inspectable.</div>}
      <JSONDetails value={cfg.sources} label="dataset and source provenance" />
    </Panel><div className="two-col debugger-details"><Panel title="Causal event timeline"><div className="event-list" aria-label="Historical events">{data.events.slice(page * 20, page * 20 + 20).map(event => <button key={event.id} aria-pressed={selected?.id === event.id} onClick={() => choose(event)}><span>#{event.sequence} · {event.kind}</span><small>{utc(event.time)}</small>{event.price !== undefined && <small>Fill {decimal(event.price)} · quantity {decimal(event.quantity)}</small>}</button>)}</div>
      {!data.events.length && <Empty>No recorded events.</Empty>}<div className="pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>Previous events</button><span>Page {page + 1}</span><button disabled={(page + 1) * 20 >= data.events.length} onClick={() => setPage(page + 1)}>Next events</button></div>
    </Panel><Panel title="Selected event">{selected ? <><Facts value={{ event_id: selected.id, kind: selected.kind, logical_sequence: selected.sequence, causal_timestamp: utc(selected.time), actual_execution_price: selected.price === undefined ? 'Not recorded for this event' : decimal(selected.price), quantity: selected.quantity === undefined ? 'Not recorded for this event' : decimal(selected.quantity) }} />
      <JSONDetails value={selected.record} label="selected causal event" /><JSONDetails value={selected.sessionRecord} label="session context, risks, partial fills and protective orders" />
    </> : <Empty>Select a timeline event or a recorded chart point.</Empty>}</Panel></div>
    <Panel title="Exact observation table"><PriceTable prices={prices} /></Panel>
  </>;
}
function PriceTable({ prices }: { prices: { time: string; exact: string; sourceId: string; kind: string }[] }) {
  const [page, setPage] = useState(0);
  return <><div className="table-scroll"><table><thead><tr><th>Observation · UTC</th><th>Type</th><th>Exact recorded price</th><th>Source event</th></tr></thead><tbody>{prices.slice(page * 20, page * 20 + 20).map((price, index) => <tr key={index}><td>{utc(price.time)}</td><td>{price.kind}</td><td>{decimal(price.exact)}</td><td>{price.sourceId}</td></tr>)}</tbody></table></div><div className="pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>Previous observations</button><span>Page {page + 1} · {prices.length} recorded points</span><button disabled={(page + 1) * 20 >= prices.length} onClick={() => setPage(page + 1)}>Next observations</button></div></>;
}
