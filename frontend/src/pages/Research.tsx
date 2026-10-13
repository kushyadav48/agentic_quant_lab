import { useEffect, useState } from 'react';
import { parseExactJSON, parseObject, type APIClient, type Capabilities, type Operation, type Submission, type Result } from '../api/client';
import { Badge, Empty, ErrorNotice, Facts, JSONDetails, Panel } from '../components';
import { utc } from '../format';
import { RecordChart, type RecordPoint } from '../RecordChart';
import { chartPoint } from '../debugger/data';

export function Research({ api, capabilities, operations, retain, selected, select, showResult, initialInput }: {
  api: APIClient; capabilities: Capabilities; operations: Operation[]; retain: (operation: Operation) => void;
  selected?: Operation; select: (id: string) => void; showResult: () => void;
  initialInput?: Record<string, unknown>;
}) {
  const [kind, setKind] = useState(capabilities.research_kinds[0]);
  const [input, setInput] = useState(initialInput ? JSON.stringify(initialInput, null, 2) : '{}');
  const [key, setKey] = useState('');
  const [lookup, setLookup] = useState('');
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  async function action(work: () => Promise<Operation>) {
    setError(undefined); setPending(true);
    try { const value = await work(); retain(value); select(value.operation_id); }
    catch (failure) { setError(failure); }
    finally { setPending(false); }
  }
  function submit() {
    return action(async () => {
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(key)) throw new Error('Idempotency key must be 1–64 letters, digits, underscores or hyphens, starting with a letter or digit.');
      const body = parseObject(input);
      if ('kind' in body) throw new Error('The operation kind is supplied by the selector. Remove kind from the input object.');
      return api.submit({ idempotency_key: key, operation: { ...body, kind } } as Submission);
    });
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">Deterministic operations</span><h1>Research workbench</h1><p>One request, a traceable identity, an explicit execution step.</p></div><Badge>{api.role.toUpperCase()}</Badge></div>
    <div className="notice warning">{api.source === 'demo' ? 'Synthetic recorded artifacts only. Submission, execution and cancellation are disabled. Select a completed fixture to inspect its original digest and results; no research runs in this demo.' : <>Process-local operations · maximum {capabilities.maximum_operations} identities. Submission queues metadata only. Execute runs synchronously in the service lane. No automatic worker or durable job store. Running work cannot be cancelled.</>}</div>
    <div className="workspace-stats"><span><b>{capabilities.research_kinds.length}</b> supported research kinds</span><span><b>{operations.length}</b> inspected identities</span><span><b>{operations.filter(operation => operation.state === 'completed').length}</b> completed artifacts</span><span><b>{api.source === 'demo' ? 'FIXED' : 'EXPLICIT'}</b> execution policy</span></div>
    <div className="two-col research-layout"><Panel title={api.source === 'demo' ? 'Recorded example request · read only execution' : 'Submit operation'}><label>Operation<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}>{capabilities.research_kinds.map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
      <label>Idempotency key<input value={key} maxLength={64} onChange={e => setKey(e.target.value)} placeholder="experiment-001" autoComplete="off" /></label>
      <label>Strict operation inputs · JSON<textarea className="code-editor" maxLength={1048576} value={input} onChange={e => setInput(e.target.value)} spellCheck={false} /></label>
      <p className="muted">Paste the existing typed request fields, excluding kind. Decimal values must be strings and timestamps UTC. The backend owns approval gates, quantitative calculations and bounds. Same key and same inputs identify the same operation.</p>
      <button className="primary" disabled={pending || api.role !== 'operator' || api.source === 'demo'} onClick={submit}>Submit only</button>{api.role === 'reader' && <small>{api.source === 'demo' ? 'Read-only demo. These inputs are a bundled backtest example, not a new operation.' : 'Operator credential required for submission and execution.'}</small>}
    </Panel><Panel title="Inspect an operation"><label>Operation SHA-256 identity<input value={lookup} onChange={e => setLookup(e.target.value)} maxLength={64} /></label><button disabled={pending || !lookup} onClick={() => action(() => api.operation(lookup))}>Load status</button>
      <p className="muted">{api.source === 'demo' ? 'Nine Python-generated fixtures are bundled for inspection. Their recorded identities are separate from every API process.' : <>There is no list-operations endpoint. This workspace retains up to {capabilities.maximum_operations} inspected identities in memory. Journal research history can locate imported runs.</>}</p>
      {!operations.length ? <Empty>No inspected operations.</Empty> : <ul className="operation-list">{operations.map(operation => <li key={operation.operation_id}><button aria-pressed={selected?.operation_id === operation.operation_id} onClick={() => select(operation.operation_id)}><span>{operation.kind.replaceAll('_', ' ')}</span><Badge tone={operation.state === 'completed' ? 'positive' : ''}>{operation.state}</Badge><small>{operation.operation_id}</small></button></li>)}</ul>}
    </Panel></div><ErrorNotice error={error} />
    {selected && <Panel title="Selected operation" action={<Badge>{selected.state}</Badge>}><Facts value={{ operation_id: selected.operation_id, request_digest: selected.request_digest, result_digest: selected.result_digest, failure: selected.failure }} />
      <div className="page-actions"><button disabled={pending} onClick={() => action(() => api.operation(selected.operation_id))}>Refresh status</button><button className="primary" disabled={pending || api.role !== 'operator' || selected.state !== 'queued'} onClick={() => action(() => api.execute(selected.operation_id))}>{pending ? 'Request in progress…' : 'Execute queued operation'}</button><button disabled={pending || api.role !== 'operator' || selected.state !== 'queued'} onClick={() => action(() => api.cancel(selected.operation_id))}>Cancel queued operation</button><button disabled={selected.state !== 'completed'} onClick={showResult}>Explore result</button></div>
      <JSONDetails value={{ strategies: selected.strategies, workflow: selected.workflow, audit: selected.audit }} label="strategy bindings and causal audit" />
      <ol className="timeline">{selected.audit.map(event => <li key={event.sequence}><Badge>{event.state}</Badge><span>{utc(event.timestamp)}</span></li>)}</ol>
    </Panel>}
  </>;
}
export function Results({ api, selected, openResearch }: { api: APIClient; selected?: Operation; openResearch?: () => void }) {
  const [result, setResult] = useState<Omit<Result, 'result'> & { result: unknown }>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [verified, setVerified] = useState(false);
  useEffect(() => {
    let active = true;
    setResult(undefined); setError(undefined); setVerified(false);
    if (!selected || selected.state !== 'completed') return;
    setPending(true);
    api.result(selected.operation_id).then(async value => {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value.result_json));
      const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      if (hash !== value.result_digest || value.operation_id !== selected.operation_id || value.result_digest !== selected.result_digest || value.kind !== selected.kind) throw new Error('Result provenance verification failed.');
      // Bind every displayed metric to the bytes whose digest was verified.
      const canonical = parseExactJSON(value.result_json, { preserveLargeIntegers: true });
      if (active) { setResult({ ...value, result: canonical }); setVerified(true); }
    }).catch(failure => { if (active) setError(failure); }).finally(() => { if (active) setPending(false); });
    return () => { active = false; };
  }, [api, selected?.operation_id, selected?.result_digest, selected?.state]);
  return <><div className="page-heading"><div><span className="eyebrow">Retained research artifacts</span><h1>Results explorer</h1><p>Server metrics and risk outcomes, without browser recomputation.</p></div></div><ErrorNotice error={error} />
    <Panel title="Result & provenance">{!selected || selected.state !== 'completed' ? <><Empty>Select a completed operation in the research workbench.</Empty><p className="muted">Artifacts appear only after explicit backend execution completes. No profitability or placeholder result is inferred from a queued or failed operation.</p>{openResearch && <button className="primary" onClick={openResearch}>Open research workbench</button>}</> : pending ? <Empty>Loading and verifying retained result…</Empty> : result && <><Badge tone="positive">{verified ? 'SHA-256 verified' : 'Unverified'}</Badge><Facts value={{ operation_id: result.operation_id, kind: result.kind, result_digest: result.result_digest }} />
      <p className="muted">Service success does not imply profitability, valid research, or risk approval. Inspect the backend's success, issues and value fields.</p><ArtifactChart value={result.result} /><ResultMetrics value={result.result} /><JSONDetails value={result.result} label="metrics, decisions and available results" />
      <details><summary>Exact canonical result JSON</summary><pre tabIndex={0}>{result.result_json.slice(0, 24000)}{result.result_json.length > 24000 ? '\n… bounded preview' : ''}</pre></details>
      <button onClick={() => { const url = URL.createObjectURL(new Blob([result.result_json], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `${result.operation_id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>Download canonical result</button>
    </>}</Panel></>;
}
function ResultMetrics({ value }: { value: unknown }) {
  // Bounded scalar projection; labels and values come only from server fields.
  const rows: [string, string][] = [];
  function visit(item: unknown, path: string, depth: number) {
    if (rows.length >= 48 || depth > 4) return;
    if (Array.isArray(item)) { rows.push([path + '.record_count', String(item.length)]); return; }
    if (item && typeof item === 'object') for (const [key, child] of Object.entries(item)) visit(child, path ? `${path}.${key}` : key, depth + 1);
    else if (item !== null && item !== undefined) rows.push([path, String(item)]);
  }
  visit(value, '', 0);
  return <div className="artifact-metrics"><span className="eyebrow">RECORDED ARTIFACT FIELDS</span><Facts value={Object.fromEntries(rows)} /></div>;
}
function ArtifactChart({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object') return null;
  const payload = (value as { value?: unknown }).value;
  const curve = payload && typeof payload === 'object' && 'equity_curve' in payload ? payload.equity_curve : undefined;
  const points: RecordPoint[] = Array.isArray(curve) ? curve.flatMap((record, index) => {
    if (!record || typeof record !== 'object' || typeof record.timestamp !== 'string') return [];
    const point = chartPoint(record.equity, record.timestamp, `artifact-mark-${index}`);
    return point ? [{ ...point, timestamp: record.timestamp }] : [];
  }) : [];
  if (points.length) return <div className="artifact-chart"><RecordChart points={points} label="Retained research equity" /></div>;
  if (Array.isArray(payload) && payload.length) return <div className="table-scroll artifact-table"><table><thead><tr><th>Record</th><th>Instrument / feature</th><th>UTC timestamp</th><th>Exact value</th></tr></thead><tbody>{payload.slice(0, 20).map((record, index) => <tr key={index}><td>#{index + 1}</td><td>{String(record.instrument_id ?? record.feature_id ?? 'Recorded artifact')}<small>{String(record.feature_id ?? record.model_digest ?? '')}</small></td><td>{typeof record.timestamp === 'string' ? utc(record.timestamp) : 'Not recorded'}</td><td>{String(record.value ?? 'Inspect original record')}</td></tr>)}</tbody></table><small>Bounded preview of {payload.length} recorded outputs. Full provenance remains in the canonical artifact.</small></div>;
  return <div className="artifact-empty">This result kind has no retained equity series. Inspect its recorded metrics, model or validation fields below.</div>;
}
