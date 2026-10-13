import { Component, Suspense, lazy, useEffect, useRef, useState, type ReactNode } from 'react';
import { APIClient, type Capabilities, type Models, type Operation, type Role } from './api/client';
import { Badge, Empty, ErrorNotice, Panel } from './components';
import { Reports } from './pages/Reports';
import { Strategy } from './pages/Strategy';
import { Research, Results } from './pages/Research';
import { Journal } from './pages/Journal';

const Debugger = lazy(() => import('./pages/Debugger'));
const pages = [['portfolio', 'Portfolio overview', 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'],
  ['research', 'Research workbench', 'M9 3h6 M10 3v6l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3 M8 14h8'],
  ['strategy', 'Strategy studio', 'M5 5h5v5H5z M14 14h5v5h-5z M14 5h5v5h-5z M10 7h4 M7 10v6h7'],
  ['results', 'Results explorer', 'M4 20V9h4v11 M10 20V4h4v16 M16 20v-7h4v7'],
  ['paper', 'Paper sessions', 'M12 3 2 8l10 5 10-5z M2 12l10 5 10-5 M2 16l10 5 10-5'],
  ['journal', 'Journal workspace', 'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5'],
  ['debugger', 'Visual debugger', 'M2 12h4l3-8 6 16 3-8h4']] as const;
type Page = typeof pages[number][0];
type Connection = { api: APIClient; capabilities: Capabilities; exampleStrategy?: Models['StrategyValidationRequest']['content']; exampleRequest?: Record<string, unknown> };
function route(): Page { const value = location.hash.slice(2); return pages.some(([id]) => id === value) ? value as Page : 'portfolio'; }
export function App() {
  const [page, setPage] = useState<Page>(route);
  const [connection, setConnection] = useState<Connection>();
  const [token, setToken] = useState('');
  const [role, setRole] = useState<Role>('reader');
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [demoPending, setDemoPending] = useState(false);
  const [session, setSession] = useState(0);
  const generation = useRef(0);
  const connecting = useRef<AbortController>(undefined);
  const [mobile, setMobile] = useState(false);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const selected = operations.find(operation => operation.operation_id === selectedId);
  const demo = connection?.api.source === 'demo';
  useEffect(() => { const change = () => { setPage(route()); setMobile(false); }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  function navigate(value: Page) { location.hash = '/' + value; setPage(value); setMobile(false); }
  async function connect(event: React.FormEvent) {
    event.preventDefault(); setError(undefined); setPending(true);
    connecting.current?.abort(); const controller = new AbortController(); connecting.current = controller;
    const current = ++generation.current;
    try {
      if (!/^[\x21-\x7e]{32,256}$/.test(token)) throw new Error('Enter a 32–256 character API bearer token without whitespace.');
      const api = new APIClient(token, role);
      const capabilities = await api.capabilities(controller.signal);
      if (generation.current !== current) return;
      if (capabilities.api_version !== 'v1' || capabilities.execution !== 'explicit_process_local' || !Array.isArray(capabilities.paper_sessions) || !Array.isArray(capabilities.portfolios) || !Array.isArray(capabilities.research_kinds)) throw new Error('API capabilities are incompatible with this dashboard.');
      api.maximumBytes = capabilities.maximum_request_bytes;
      setConnection({ api, capabilities }); setToken(''); setOperations([]); setSelectedId(''); setSession(value => value + 1);
    } catch (failure) { if (generation.current === current) setError(failure); }
    finally { if (generation.current === current) setPending(false); }
  }
  function disconnect() { generation.current++; connecting.current?.abort(); setConnection(undefined); setOperations([]); setSelectedId(''); setToken(''); setError(undefined); setPending(false); setDemoPending(false); setSession(v => v + 1); }
  async function enterDemo() {
    disconnect(); const current = generation.current; setDemoPending(true);
    try {
      const { createDemoWorkspace } = await import('./demo/client');
      const api = await createDemoWorkspace();
      const capabilities = await api.capabilities();
      if (generation.current !== current) return;
      const recorded = api.recordedOperations;
      setConnection({ api, capabilities, exampleStrategy: api.exampleStrategy, exampleRequest: api.exampleRequest });
      setOperations(recorded); setSelectedId(recorded[0]?.operation_id ?? ''); navigate('portfolio');
    } catch (failure) { if (generation.current === current) setError(failure); }
    finally { if (generation.current === current) setDemoPending(false); }
  }
  const currentGeneration = generation.current;
  function retain(operation: Operation) {
    if (generation.current !== currentGeneration) return;
    setOperations(current => [operation, ...current.filter(value => value.operation_id !== operation.operation_id)].slice(0, connection?.capabilities.maximum_operations ?? 32));
  }
  async function inspectOperation(id: string) {
    if (!connection) return;
    try { const operation = await connection.api.operation(id); if (generation.current !== currentGeneration) return; retain(operation); setSelectedId(id); navigate('research'); } catch (failure) { if (generation.current === currentGeneration) setError(failure); }
  }
  return <div className={`app-shell ${demo ? 'demo-workspace' : ''}`}><a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>Skip to content</a>
    <aside className={`sidebar ${mobile ? 'open' : ''}`}><div className="brand"><span className="brand-mark">Q<span /></span><div>QUANT<span className="brand-lab">LAB</span><small>RESEARCH TERMINAL</small></div></div>
      <div className="nav-label">WORKSPACE <span>01—07</span></div><nav aria-label="Main navigation">{pages.map(([id, title, path]) => <a key={id} href={`#/${id}`} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}><svg aria-hidden="true" viewBox="0 0 24 24"><path d={path} /></svg>{title}{page === id && <i />}</a>)}</nav>
      <div className="sidebar-context"><span className="eyebrow">DATA ENVIRONMENT</span><strong>{demo ? 'Synthetic fixture' : connection ? 'Local API / v1' : 'Awaiting connection'}</strong><span className="source-line"><span className={`status-dot ${connection ? 'connected' : ''}`} />{demo ? 'FIXED · READ ONLY' : connection ? 'AUTHENTICATED' : 'NO ACCOUNT DATA'}</span><p>{demo ? 'Python-generated TEST records. No API requests or execution.' : 'Exact records. Explicit research. Human-owned decisions.'}</p></div>
      <div className="sidebar-footer"><Badge tone="warning">NO LIVE TRADING</Badge><small>Phase 22 · UTC timestamps</small></div>
    </aside><div className="workspace"><header className="toolbar"><button className="menu-button" onClick={() => setMobile(!mobile)} aria-expanded={mobile} aria-label="Toggle navigation">☰</button><div className="toolbar-title"><span className="toolbar-secondary">WORKSTATION /</span><strong>{pages.find(([id]) => id === page)?.[1]}</strong></div><div className="toolbar-right"><span className={`status-dot ${connection ? 'connected' : ''}`} /><span className="connection-status">{demo ? 'Synthetic demo active' : connection ? 'Local API connected' : 'API disconnected'}</span><Badge tone={demo ? 'warning' : ''}>{demo ? 'DEMO' : connection ? connection.api.role.toUpperCase() : 'OFFLINE'}</Badge>
        {demo ? <button onClick={disconnect}>Exit demo</button> : <><button className="demo-button" disabled={demoPending} onClick={enterDemo}>{demoPending ? 'Loading demo…' : 'Explore synthetic demo'}</button>{connection && <button onClick={disconnect}>Disconnect</button>}</>}</div></header>
      <div className="environment-strip"><span><b>{demo ? 'SYNTHETIC' : 'API v1'}</b> <span className="strip-muted">{demo ? 'TEST dataset · fixed 01 JAN 2026' : 'Local research namespace'}</span></span><span>PORTFOLIOS <b>{connection?.capabilities.portfolios.length ?? '—'}</b></span><span>PAPER SESSIONS <b>{connection?.capabilities.paper_sessions.length ?? '—'}</b></span><span>JOURNAL <b>{connection ? connection.capabilities.journal ? 'AVAILABLE' : 'UNCONFIGURED' : '—'}</b></span><span className="strip-clock">UTC <b>HISTORICAL / PAPER</b></span></div>
      {demo && <div className="demo-banner" role="status"><Badge tone="warning">SYNTHETIC DEMO</Badge><strong>Illustrative records, never real financial results.</strong><span>Fixed Python-generated data · read-only · no API requests · exit demo to connect the API</span></div>}
      <main id="main-content" tabIndex={-1}><ErrorNotice error={error} />
        {!connection && <Panel title="Local API connection" action={<Badge>MEMORY-ONLY TOKEN</Badge>}><form className="connection-form" onSubmit={connect}><label>Bearer token<input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" maxLength={256} placeholder="Enter your local API credential" /></label><label>Requested interface role<select value={role} onChange={e => setRole(e.target.value as Role)}><option value="reader">Reader</option><option value="operator">Operator</option></select></label><button type="submit" className="primary" disabled={pending || demoPending}>{pending ? 'Connecting…' : 'Connect'}</button></form><p className="connection-hint">Credentials stay in memory and clear on disconnect/reload. The server enforces roles; choosing Operator grants no permission.</p></Panel>}
        {connection && <div key={`editors-${session}`}>
          <div hidden={page !== 'strategy'}><Boundary><Strategy api={connection.api} initialContent={connection.exampleStrategy} /></Boundary></div>
          <div hidden={page !== 'research'}><Boundary><Research api={connection.api} initialInput={connection.exampleRequest} capabilities={connection.capabilities} operations={operations} retain={retain} selected={selected} select={id => { if (generation.current === currentGeneration) setSelectedId(id); }} showResult={() => navigate('results')} /></Boundary></div>
        </div>}
        <Boundary key={`${session}-${page}`}><Suspense fallback={<Empty>Loading workspace…</Empty>}>
          {page === 'debugger' ? <Debugger api={connection?.api} capabilities={connection?.capabilities} /> : connection ? <>
            {page === 'portfolio' && <Reports api={connection.api} capabilities={connection.capabilities} kind="portfolio" onDemo={enterDemo} />}
            {page === 'paper' && <Reports api={connection.api} capabilities={connection.capabilities} kind="paper" onDemo={enterDemo} />}
            {page === 'results' && <Results api={connection.api} selected={selected} openResearch={() => navigate('research')} />}
            {page === 'journal' && <Journal api={connection.api} capabilities={connection.capabilities} inspectOperation={inspectOperation} />}
          </> : <div className="welcome-workspace"><div className="page-heading"><div><span className="eyebrow">RESEARCH / RISK / EVIDENCE</span><h1>{page === 'portfolio' ? 'Portfolio overview' : pages.find(([id]) => id === page)?.[1]}</h1><p>Your workstation is ready. Choose an explicit data environment to begin.</p></div><Badge>NO DATA CONNECTED</Badge></div>
            <div className="welcome-grid"><section className="welcome-terminal"><div className="terminal-grid" aria-hidden="true" /><div className="welcome-copy"><span className="eyebrow">AGENTIC QUANT RESEARCH & TRADING LAB</span><h2>Research with context.<br /><em>Decisions with evidence.</em></h2><p>Inspect portfolio marks, trace strategy decisions and explore reproducible research in one workspace.</p><button className="primary" disabled={demoPending} onClick={enterDemo}>Open synthetic workspace →</button><small>3 fixture sessions · 9 recorded research kinds · no credential needed</small></div></section>
              <Panel title="Choose your environment"><div className="environment-option"><span className="option-number">01</span><div><h3>Connected API</h3><p>Authenticated reports from registered Python owners. Use the connection above.</p><Badge>AUTHORITATIVE RECORDS</Badge></div></div><div className="environment-option"><span className="option-number">02</span><div><h3>Synthetic demo</h3><p>A separate, fixed dataset for exploring the workstation. No real account or market results.</p><button className="subtle" disabled={demoPending} onClick={enterDemo}>Explore fixture data ↗</button></div></div></Panel></div>
            <div className="readiness-grid">{[['Portfolio & risk', 'Balances, member allocations and valuation quality.'], ['Research & provenance', 'Explicit operations and verified canonical artifacts.'], ['Strategy & execution', 'Recorded observations, decisions and paper fills.']].map(([title, text]) => <Panel key={title} title={title}><span className="missing-value">—</span><p className="muted">{text}</p><span className="empty-caption">Connect API or choose synthetic demo</span></Panel>)}</div>
            <button className="subtle" onClick={() => navigate('debugger')}>Explore the offline visual debugger →</button>
          </div>}
        </Suspense></Boundary>
        <footer className="main-footer"><span><span className="status-dot" />{demo ? 'SYNTHETIC FIXTURE · READ ONLY' : 'LOCAL RESEARCH ENVIRONMENT'}</span><span>Exact Decimal values · UTC · No live trading authority</span></footer>
      </main></div></div>;
}
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div className="notice error" role="alert">This screen could not render the returned contract. Refresh the connection and verify API v1 compatibility.</div> : this.props.children; }
}
