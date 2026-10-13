import { useEffect, useRef, useState } from 'react';
import { APIError, type APIClient, type AIConfiguration, type AIResult, type AIStatus } from '../api/client';
import { ErrorNotice, Facts, JSONDetails, Panel } from '../components';

export function AIResearch({ api, useJob }: { api: APIClient; useJob: (id: string) => void }) {
  const [config, setConfig] = useState<AIConfiguration>();
  const [text, setText] = useState('');
  const [strategy, setStrategy] = useState('research-draft');
  const [version, setVersion] = useState('1');
  const [key, setKey] = useState('');
  const [consent, setConsent] = useState(false);
  const [job, setJob] = useState<AIStatus>();
  const [lookup, setLookup] = useState('');
  const [result, setResult] = useState<AIResult>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const generation = useRef(0);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, [api]);
  async function action(work: () => Promise<void>) {
    const current = generation.current;
    setPending(true); setError(undefined);
    try { await work(); } catch (failure) { if (current === generation.current) setError(failure); }
    finally { if (current === generation.current) setPending(false); }
  }
  async function status(work: () => Promise<AIStatus>) {
    const current = generation.current;
    const value = await work();
    if (current !== generation.current) return;
    setJob(value); setResult(undefined);
    if (value.failure) setError(new APIError(503, value.failure));
  }
  const operator = api.role === 'operator';
  return <Panel title="Cloud-assisted strategy research">
    <p>Only the text selected below is sent to Gemini. Free-tier inputs may be used for product improvement and human review. Use non-sensitive material. A proposal remains an unapproved draft.</p>
    <button disabled={pending} onClick={() => action(async () => {
      const current = generation.current; const value = await api.aiConfiguration();
      if (current === generation.current) setConfig(value);
    })}>Check cloud configuration</button>
    {config && <><Facts value={config} />{config.state !== 'ready' && <ErrorNotice error={new APIError(503, config.state)} />}</>}
    <div className="form-row"><label>AI strategy ID<input value={strategy} maxLength={128} onChange={e => setStrategy(e.target.value)} /></label><label>AI strategy version<input type="number" min={1} max={999999} value={version} onChange={e => setVersion(e.target.value)} /></label><label>AI idempotency key<input value={key} maxLength={64} onChange={e => setKey(e.target.value)} /></label></div>
    <label>Selected research text<textarea value={text} maxLength={16000} onChange={e => setText(e.target.value)} /></label>
    <label><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />I approve transmitting this selected text to the cloud provider.</label>
    <div className="page-actions">
      <button disabled={!operator || !consent || pending || config?.state !== 'ready'} onClick={() => action(() => status(() => api.aiSubmit({ idempotency_key: key, external_transmission_approved: true, input: { strategy_text: text, input_reference: 'dashboard-selected-text', strategy_id: strategy, version: Number(version) } })))}>Submit AI interpretation</button>
      <button disabled={!operator || pending || job?.state !== 'queued'} onClick={() => action(() => status(() => api.aiExecute(job!.job_id)))}>Execute AI interpretation</button>
      <button disabled={!operator || pending || job?.state !== 'queued'} onClick={() => action(() => status(() => api.aiCancel(job!.job_id)))}>Cancel queued AI job</button>
      <button disabled={!job || pending} onClick={() => action(() => status(() => api.aiStatus(job!.job_id)))}>Refresh AI status</button>
      <button disabled={job?.state !== 'completed' || pending} onClick={() => action(async () => {
        const current = generation.current; const value = await api.aiResult(job!.job_id);
        if (current === generation.current) setResult(value);
      })}>Inspect AI result</button>
    </div>
    <label>Existing AI job ID<input value={lookup} maxLength={64} onChange={e => setLookup(e.target.value)} /></label>
    <button disabled={pending || !lookup} onClick={() => action(() => status(() => api.aiStatus(lookup)))}>Look up AI job</button>
    {pending && <p role="status">AI request pending. A browser disconnect does not cancel running inference.</p>}
    <ErrorNotice error={error} />
    {job && <Facts value={job} />}
    {result && <><p>{result.result.interpretation.status === 'READY' ? 'Deterministically validated draft. Review and approve the exact version through trusted Python before research submission.' : 'Clarification required. Review the questions and submit revised text with a new key.'}</p><JSONDetails value={result} label="Validated AI artifact" />
      {result.result.proposal && <button onClick={() => useJob(result.job_id)}>Bind reviewed research to this AI job</button>}</>}
    <p className="muted">Jobs and budgets are process-local. Only queued jobs can be cancelled. No automatic execution, approval, paper mutation or provider fallback.</p>
  </Panel>;
}
