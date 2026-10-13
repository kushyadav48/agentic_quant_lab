import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { APIError } from './api/client';
import { errorMessage } from './format';

export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <section className="panel"><div className="panel-heading"><h2>{title}</h2>{action}</div>{children}</section>;
}
export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="notice error" role="alert">{errorMessage(error)}{error instanceof APIError && error.requestId && <small>Correlation: {error.requestId}</small>}</div>;
}
export function Facts({ value }: { value: Record<string, unknown> }) {
  return <dl className="facts">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{item === null || item === undefined ? 'Unavailable' : String(item)}</dd></div>)}</dl>;
}
export function JSONDetails({ value, label = 'Authoritative record' }: { value: unknown; label?: string }) {
  const text = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const [expanded, setExpanded] = useState(false);
  return <div className="record"><button className="subtle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Hide' : 'Inspect'} {label}</button>{expanded && <pre tabIndex={0}>{text.length > 24000 ? text.slice(0, 24000) + '\n… display truncated; download full record below.' : text}</pre>}
    <button className="subtle" onClick={() => { const url = URL.createObjectURL(new Blob([text], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'quantlab-record.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>Download exact JSON</button></div>;
}
export function Metrics({ values }: { values: Record<string, string> }) {
  return <div className="metrics">{Object.entries(values).map(([name, value]) => <div className="metric" key={name}><span>{name}</span><strong>{value}</strong></div>)}</div>;
}
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; error?: unknown; loading: boolean }>({ loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    load(controller.signal).then(data => { if (!controller.signal.aborted) setState({ data, loading: false }); }, error => { if (!controller.signal.aborted) setState({ error, loading: false }); });
    return () => controller.abort();
  }, [...deps, revision]);
  return { ...state, refresh: () => setRevision(value => value + 1) };
}
export function LoadState({ loading, error, children }: { loading: boolean; error?: unknown; children: ReactNode }) {
  return loading ? <div className="empty" role="status">Loading authoritative snapshot…</div> : error ? <ErrorNotice error={error} /> : <>{children}</>;
}
