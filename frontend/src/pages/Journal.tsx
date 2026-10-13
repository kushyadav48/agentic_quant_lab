import { useState } from 'react';
import type { APIClient, Capabilities, JournalQuery, TradePage, ResearchPage, Models } from '../api/client';
import { Badge, Empty, ErrorNotice, Facts, JSONDetails, LoadState, Panel, useResource } from '../components';
import { decimal, utc } from '../format';

type Filter = 'session_id' | 'account_id' | 'strategy_id' | 'strategy_digest' | 'instrument_id' | 'order_id' | 'run_id' | 'start' | 'end';
export function Journal({ api, capabilities, inspectOperation }: { api: APIClient; capabilities: Capabilities; inspectOperation: (id: string) => void }) {
  const [kind, setKind] = useState<'trades' | 'research'>('trades');
  const [filters, setFilters] = useState<Partial<Record<Filter, string>>>({});
  const [version, setVersion] = useState('');
  const [query, setQuery] = useState<JournalQuery>({ limit: 10 });
  const [limit, setLimit] = useState(10);
  const [error, setError] = useState<unknown>();
  const [queryRevision, setQueryRevision] = useState(0);
  function apply() {
    setError(undefined);
    try {
      const next: JournalQuery = { limit };
      for (const [field, text] of Object.entries(filters)) if (text?.trim()) {
        const key = field as Filter;
        const value = key === 'start' || key === 'end' ? new Date(text + 'Z').toISOString() : text.trim();
        Object.assign(next, { [key]: value });
      }
      if (version) { const number = Number(version); if (!Number.isSafeInteger(number) || number < 1) throw new Error('Strategy version must be a positive integer.'); next.strategy_version = number; }
      if (next.start && next.end && next.start >= next.end) throw new Error('UTC start must precede the exclusive end.');
      setQuery(next); setQueryRevision(value => value + 1);
    } catch (failure) { setError(failure); }
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">Traceable history</span><h1>Journal workspace</h1><p>Read-only trade deltas and imported research records.</p></div><Badge>READ ONLY</Badge></div>
    {api.source === 'demo' && <div className="notice warning">Synthetic fixture history. Filters and paging operate locally on fixed Python-generated records; no API journal is queried.</div>}
    {!capabilities.journal ? <Panel title="Journal"><Empty>The journal service is not configured. Start the API with a trusted journal path to query retained records.</Empty></Panel> : <>
      <Panel title="History filters"><div className="filters"><label>History<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="trades">Trade history</option><option value="research">Research history</option></select></label>
        {(['session_id', 'account_id', 'strategy_id', 'strategy_digest', 'instrument_id', 'order_id', 'run_id', 'start', 'end'] as Filter[]).map(field => <label key={field}>{field === 'start' ? 'Start · UTC inclusive' : field === 'end' ? 'End · UTC exclusive' : field.replaceAll('_', ' ')}<input type={field === 'start' || field === 'end' ? 'datetime-local' : 'text'} value={filters[field] ?? ''} onChange={e => setFilters({ ...filters, [field]: e.target.value })} maxLength={128} /></label>)}
        <label>Strategy version<input type="number" min={1} value={version} onChange={e => setVersion(e.target.value)} /></label>
        <label>Page size<select value={limit} onChange={e => setLimit(Number(e.target.value))}>{[5, 10, 20].map(n => <option key={n}>{n}</option>)}</select></label></div>
        <div className="page-actions"><button className="primary" onClick={apply}>Apply filters</button><button onClick={() => { setFilters({}); setVersion(''); setLimit(10); setQuery({ limit: 10 }); setQueryRevision(v => v + 1); setError(undefined); }}>Reset filters</button></div><ErrorNotice error={error} />
      </Panel><History key={`${kind}-${queryRevision}`} api={api} kind={kind} query={query} inspectOperation={inspectOperation} />
      <Panel title="Position completeness"><Summary api={api} /></Panel>
      <p className="muted">Notes and research links have no HTTP query route. The UI displays references present in returned records. Note editing, journal imports and mutations remain in trusted local services.</p>
    </>}
  </>;
}
function History({ api, kind, query, inspectOperation }: { api: APIClient; kind: 'trades' | 'research'; query: JournalQuery; inspectOperation: (id: string) => void }) {
  const [cursors, setCursors] = useState<(Models['HistoryCursor'] | null)[]>([null]);
  const [page, setPage] = useState(0);
  const resource = useResource<TradePage | ResearchPage>(signal => kind === 'trades' ? api.trades({ ...query, after: cursors[page] }, signal) : api.research({ ...query, after: cursors[page] }, signal), [api, kind, query, page]);
  return <Panel title={kind === 'trades' ? 'Committed trade activity' : 'Imported research history'} action={<button onClick={resource.refresh} disabled={resource.loading}>Refresh page</button>}><LoadState {...resource}>
    {resource.data && <>{!resource.data.records.length ? <Empty>No records match these filters.</Empty> : <div className="table-scroll"><table><thead><tr><th>UTC time</th><th>Source</th><th>{kind === 'trades' ? 'Net realized delta' : 'State'}</th><th>Details</th></tr></thead><tbody>
      {kind === 'trades' ? (resource.data as TradePage).records.map(record => <tr key={record.source.record_id}><td>{utc(record.source.timestamp)}</td><td>{record.source.reason}<small>{record.source.session_id} · sequence {record.source.sequence}</small></td><td>{decimal(record.net_realized_pnl_delta)}<small>Fees: {decimal(record.fees_delta)}</small></td><td><JSONDetails value={record} label="trade, risk, fills and provenance" /></td></tr>) : (resource.data as ResearchPage).records.map(record => <tr key={record.record_id}><td>{utc(record.timestamp)}</td><td>{record.origin_namespace}<small>{record.run_id}</small></td><td><Badge>{record.operation?.state ?? record.evidence?.status}</Badge></td><td>{record.operation && <button onClick={() => inspectOperation(record.operation!.operation_id)}>Inspect operation</button>}<JSONDetails value={record} label="research reference and provenance" /></td></tr>)}
    </tbody></table></div>}<div className="pagination"><button disabled={page === 0 || resource.loading} onClick={() => setPage(page - 1)}>Previous page</button><span>Page {page + 1} · at most {query.limit} records</span><button disabled={!resource.data.next_cursor || resource.loading || page >= 99} onClick={() => { setCursors([...cursors.slice(0, page + 1), resource.data!.next_cursor ?? null]); setPage(page + 1); }}>Next page</button></div>
      {page >= 99 && <small>Navigation is bounded to 100 pages. Narrow filters to continue.</small>}
    </>}
  </LoadState></Panel>;
}
function Summary({ api }: { api: APIClient }) {
  const [id, setId] = useState('');
  const [value, setValue] = useState<Models['SessionSummary']>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  async function load() { setPending(true); setError(undefined); setValue(undefined); try { setValue(await api.summary(id)); } catch (failure) { setError(failure); } finally { setPending(false); } }
  return <><div className="form-row"><label>Session identity<input value={id} disabled={pending} onChange={e => { setId(e.target.value); setValue(undefined); }} maxLength={128} /></label><button disabled={!id || pending} onClick={load}>Load summary</button></div><ErrorNotice error={error} />
    {value && <><Badge tone={value.lifecycle === 'open' ? 'warning' : ''}>{value.lifecycle === 'open' ? 'OPEN / INCOMPLETE POSITION' : value.lifecycle}</Badge><Facts value={{ net_realized_pnl: decimal(value.net_realized_pnl), closed_outcome: value.closed_outcome, fill_count: value.fill_count, record_count: value.record_count, source_record_id: value.source_record_id }} /><JSONDetails value={value} label="session summary" /></>}
  </>;
}
