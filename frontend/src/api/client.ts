import type { components } from './generated';

export type Models = components['schemas'];
export type Capabilities = Models['Capabilities'];
export type Operation = Models['OperationStatus'];
export type Submission = Models['OperationSubmission'];
export type PaperReport = Models['PaperReport'];
export type Portfolio = Models['PortfolioSnapshot'];
// Defaults are optional in request bodies even though Phase 21's shared
// serialization schema marks these fields required. The backend expands them.
export type JournalQuery = Partial<Models['JournalQuery']> & { limit: number };
export type TradePage = Models['TradeHistoryPage'];
export type ResearchPage = Models['ResearchHistoryPage'];
export type SessionRecord = Models['SessionRecord'];
export type Validation = Models['StrategyValidationResult'];
export type Result = Models['BacktestResultResponse'] | Models['HoldoutResultResponse'] |
  Models['WalkForwardResultResponse'] | Models['RobustnessResultResponse'] |
  Models['DatasetResultResponse'] | Models['TrainingResultResponse'] |
  Models['PredictionResultResponse'] | Models['PredictionFeaturesResultResponse'] | Models['PerformanceResultResponse'];
export type Role = 'reader' | 'operator';
export type AIStatus = Models['AIStatus'];
export type AIResult = Models['AIResult'];
export type AIConfiguration = Models['AIConfiguration'];

const messages: Record<string, string> = {
  cloud_disabled: 'Cloud inference is disabled. Configure it on the server.',
  unsupported_provider: 'The selected provider is unsupported or disallowed.',
  free_policy_rejected: 'Current free-tier eligibility and disabled billing must be verified on the server.',
  credentials_missing: 'Gemini credentials are missing on the server.',
  credentials_invalid: 'The provider rejected the server credentials.',
  provider_unavailable: 'The provider or its optional SDK is unavailable.',
  provider_timeout: 'The provider request timed out. Inspect this job before submitting another.',
  rate_limited: 'The provider rate limit was reached. No automatic retry occurred.',
  quota_exhausted: 'The provider quota is exhausted. No fallback occurred.',
  budget_exhausted: 'The process-session cloud request budget is exhausted.',
  cloud_busy: 'A cloud request is already running.',
  cloud_input_rejected: 'The selected cloud input exceeds policy or contains protected content.',
  invalid_model_response: 'The model output failed complete structured validation.',
  research_validation_rejected: 'Deterministic validation rejected the proposal or approved research binding.',
  unauthenticated: 'Credential rejected. Disconnect and enter a valid token.',
  unauthorized: 'Credential rejected. Disconnect and enter a valid token.',
  forbidden: 'This action requires an operator credential.',
  service_busy: 'The service lane is busy. Refresh status before retrying.',
  recovery_required: 'Local operator recovery is required. HTTP cannot clear this gate.',
  service_unavailable: 'This reporting service is not configured.',
  not_found: 'This identity is unavailable in the current API process.',
  result_unavailable: 'A completed result is not available yet.',
  request_invalid: 'The request violates the API contract.',
  invalid_request: 'The request violates the API contract.',
  query_rejected: 'The query or cursor was rejected. Reset filters and retry.',
};
export class APIError extends Error {
  constructor(public status: number, public code: string, public requestId?: string) {
    super(messages[code] ?? `Request failed (${status}). Check the contract and service state.`);
  }
}

/** Financial decimals must be strings. Requests reject unsafe integers; response
 * views preserve arbitrary-size provenance integers as exact text, never Number. */
export function parseExactJSON(text: string, options: { preserveLargeIntegers?: boolean } = {}): unknown {
  let quoted = false, escape = false;
  let exactText = '', copied = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) { if (escape) escape = false; else if (c === '\\') escape = true; else if (c === '"') quoted = false; continue; }
    if (c === '"') { quoted = true; continue; }
    if (c === '-' || /[0-9]/.test(c)) {
      const token = text.slice(i).match(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/)?.[0];
      if (!token || !/^-?(0|[1-9]\d*)$/.test(token))
        throw new Error('Use decimal strings for financial values; numeric JSON tokens must be safe integers.');
      if (!Number.isSafeInteger(Number(token))) {
        if (!options.preserveLargeIntegers) throw new Error('Use decimal strings for financial values; numeric JSON tokens must be safe integers.');
        exactText += text.slice(copied, i) + JSON.stringify(token);
        copied = i + token.length;
      }
      i += token.length - 1;
    }
  }
  let value: unknown;
  try { value = JSON.parse(copied ? exactText + text.slice(copied) : text); } catch { throw new Error('Invalid JSON. Check object syntax, quoted decimal values and commas.'); }
  // Reject duplicate keys instead of silently repairing a strict backend request.
  const scopes: (Set<string> | null)[] = [];
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '{' || c === '[') { scopes.push(c === '{' ? new Set() : null); if (scopes.length > 64) throw new Error('JSON nesting exceeds the supported depth.'); }
    else if (c === '}' || c === ']') scopes.pop();
    else if (c === '"') {
      const start = i; let escaped = false;
      for (i++; i < text.length; i++) { if (escaped) escaped = false; else if (text[i] === '\\') escaped = true; else if (text[i] === '"') break; }
      let next = i + 1; while (/\s/.test(text[next] ?? '') && next < text.length) next++;
      if (text[next] === ':') {
        const key = JSON.parse(text.slice(start, i + 1)); const keys = scopes.at(-1);
        if (keys?.has(key)) throw new Error('Duplicate JSON object keys are not allowed.');
        keys?.add(key);
      }
    }
  }
  return value;
}
export function parseObject(text: string): Record<string, unknown> {
  const value = parseExactJSON(text);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('A JSON object is required.');
  return value as Record<string, unknown>;
}

export class APIClient {
  readonly source: 'api' | 'demo' = 'api';
  constructor(private token: string, public role: Role, public maximumBytes = 1_048_576) {}
  async request<T>(path: string, body?: unknown, signal?: AbortSignal, method?: 'GET' | 'POST'): Promise<T> {
    const json = body === undefined ? undefined : JSON.stringify(body);
    if (json && new TextEncoder().encode(json).byteLength > this.maximumBytes) throw new Error('Request exceeds the API byte limit.');
    let response: Response;
    try {
      response = await fetch('/api/v1' + path, {
        method: method ?? (json === undefined ? 'GET' : 'POST'), signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
        headers: { Authorization: `Bearer ${this.token}`, ...(json ? { 'Content-Type': 'application/json' } : {}) }, body: json,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error('API unreachable. Start the local backend and development proxy. Execution may continue after a disconnect; refresh its identity before retrying.');
    }
    if (!response.ok) {
      let code = response.status === 401 ? 'unauthorized' : response.status === 403 ? 'forbidden' : 'request_invalid';
      let requestId: string | undefined;
      try {
        const value = JSON.parse(await response.text());
        if (typeof value.code === 'string' && /^[a-z_]{1,64}$/.test(value.code)) code = value.code;
        if (typeof value.request_id === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value.request_id)) requestId = value.request_id;
      } catch { /* Never display raw server text or exceptions. */ }
      throw new APIError(response.status, code, requestId);
    }
    return parseExactJSON(await response.text(), { preserveLargeIntegers: true }) as T;
  }
  capabilities(signal?: AbortSignal) { return this.request<Capabilities>('/capabilities', undefined, signal); }
  aiConfiguration() { this.live(); return this.request<AIConfiguration>('/ai/configuration'); }
  aiSubmit(body: Models['AIInput']) { this.live(); this.operator(); return this.request<AIStatus>('/ai/interpretations', body); }
  aiStatus(id: string) { this.live(); return this.request<AIStatus>('/ai/interpretations/' + this.digest(id)); }
  aiExecute(id: string) { this.live(); this.operator(); return this.request<AIStatus>('/ai/interpretations/' + this.digest(id) + '/execute', undefined, undefined, 'POST'); }
  aiCancel(id: string) { this.live(); this.operator(); return this.request<AIStatus>('/ai/interpretations/' + this.digest(id) + '/cancel', undefined, undefined, 'POST'); }
  async aiResult(id: string) {
    this.live(); const result = await this.request<AIResult>('/ai/interpretations/' + this.digest(id) + '/result');
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(result.result_json));
    if (Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('') !== result.result_digest)
      throw new Error('AI result digest verification failed.');
    return { ...result, result: parseExactJSON(result.result_json, { preserveLargeIntegers: true }) as AIResult['result'] };
  }
  aiResearch(id: string, submission: Submission) { this.live(); this.operator(); return this.request<Operation>('/ai/research/operations', { interpretation_job_id: this.digest(id), submission }); }
  validate(content: Models['StrategyValidationRequest']['content']) { return this.request<Validation>('/strategies/validate', { content }); }
  submit(body: Submission) { this.operator(); return this.request<Operation>('/research/operations', body); }
  operation(id: string) { return this.request<Operation>('/research/operations/' + this.digest(id)); }
  execute(id: string) { this.operator(); return this.request<Operation>('/research/operations/' + this.digest(id) + '/execute', undefined, undefined, 'POST'); }
  cancel(id: string) { this.operator(); return this.request<Operation>('/research/operations/' + this.digest(id) + '/cancel', undefined, undefined, 'POST'); }
  result(id: string) { return this.request<Result>('/research/operations/' + this.digest(id) + '/result'); }
  paper(id: string, signal?: AbortSignal) { return this.request<PaperReport>('/paper/sessions/' + encodeURIComponent(id) + '/snapshot', undefined, signal); }
  portfolio(id: string, signal?: AbortSignal) { return this.request<Portfolio>('/portfolios/' + encodeURIComponent(id) + '/snapshot', undefined, signal); }
  trades(query: JournalQuery, signal?: AbortSignal) { return this.request<TradePage>('/journal/trades/query', query, signal); }
  research(query: JournalQuery, signal?: AbortSignal) { return this.request<ResearchPage>('/journal/research/query', query, signal); }
  summary(id: string, signal?: AbortSignal) { return this.request<Models['SessionSummary']>('/journal/sessions/' + encodeURIComponent(id) + '/summary', undefined, signal); }
  private operator() { if (this.role !== 'operator') throw new APIError(403, 'forbidden'); }
  private live() { if (this.source !== 'api') throw new Error('Cloud inference is unavailable in Synthetic Demo.'); }
  private digest(id: string) { if (!/^[a-f0-9]{64}$/.test(id)) throw new Error('Operation identity must be a 64-character SHA-256 digest.'); return id; }
}
