import { useRef, useState } from 'react';
import { parseObject, type APIClient, type Models, type Validation } from '../api/client';
import { Badge, ErrorNotice, Facts, Panel } from '../components';

export function Strategy({ api, initialContent }: { api: APIClient; initialContent?: Models['StrategyValidationRequest']['content'] }) {
  const [draft, setDraft] = useState(initialContent ? JSON.stringify(initialContent, null, 2) : '{}');
  const [identity, setIdentity] = useState('local-draft');
  const [version, setVersion] = useState('1');
  const [validation, setValidation] = useState<Validation>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [notes, setNotes] = useState('');
  const revision = useRef(0);
  async function validate() {
    const current = revision.current;
    setPending(true); setError(undefined); setValidation(undefined);
    try {
      const content = parseObject(draft) as Models['StrategyValidationRequest']['content'];
      const response = await api.validate(content);
      if (current === revision.current) setValidation(response);
    } catch (failure) { if (current === revision.current) setError(failure); }
    finally { setPending(false); }
  }
  function invalidate() { revision.current++; setValidation(undefined); setError(undefined); }
  return <>
    <div className="page-heading"><div><span className="eyebrow">Author · inspect · validate</span><h1>Strategy studio</h1><p>Local drafts with deterministic API validation.</p></div><Badge tone={validation?.valid ? 'positive' : ''}>{validation?.valid ? 'VALIDATED' : 'DRAFT'}</Badge></div>
    {api.source === 'demo' && <div className="notice warning">Recorded fixture validation only. The bundled example is editable; changed content requires the connected API for validation. No strategy is approved here.</div>}
    <div className="two-col editor-layout"><Panel title="Draft content" action={<Badge>STRATEGY CONTENT / JSON</Badge>}><div className="form-row"><label>Local strategy label<input value={identity} onChange={e => { setIdentity(e.target.value); invalidate(); }} maxLength={128} /></label><label>Local version label<input type="number" min={1} max={999999} value={version} onChange={e => { setVersion(e.target.value); invalidate(); }} /></label></div>
      <label>StrategyContent JSON<textarea className="code-editor" spellCheck={false} maxLength={1048576} value={draft} onChange={e => { setDraft(e.target.value); invalidate(); }} /></label>
      <div className="page-actions"><button className="primary" onClick={validate} disabled={pending}>{pending ? 'Validating…' : 'Validate draft'}</button>{initialContent && <button onClick={() => { setDraft(JSON.stringify(initialContent, null, 2)); invalidate(); }}>Restore fixture example</button>}</div><ErrorNotice error={error} />
    </Panel><Panel title="Review status"><Facts value={{ local_label: identity, local_version: version, state: validation?.valid ? 'Validated content' : 'Draft', content_digest: validation?.content_digest, approval: 'No HTTP approval contract' }} />
      <p className="muted">Local labels are not API specification identities. Validation checks StrategyContent and returns its digest; it does not create or approve a strategy version. Approved specifications must come from the existing trusted review workflow.</p>
      <div className="review-steps"><div><b>01</b><span>Author content<small>Explicit rules, instruments and timeframes</small></span></div><div><b>02</b><span>Validate the contract<small>Inspect issues and the returned content digest</small></span></div><div><b>03</b><span>Review in trusted Python<small>Human approval remains outside this interface</small></span></div></div>
      {validation && !validation.valid && <ul className="issues">{validation.issues.map((issue, index) => <li key={index}><strong>{issue.location.join('.') || 'content'}</strong> · {issue.code}<p>{issue.message}</p></li>)}</ul>}
    </Panel></div>
    <Panel title="Text research input · local only"><label>Research notes<textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Describe a hypothesis or paste supporting text…" maxLength={32768} /></label><p className="muted">Session-only editor. No upload, provider inference, approval persistence, or saved notes.</p></Panel>
  </>;
}
