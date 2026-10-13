import { APIClient, APIError, parseExactJSON, type Capabilities, type JournalQuery, type Models, type Operation, type PaperReport, type Portfolio, type ResearchPage, type Result, type Submission, type TradePage, type Validation } from '../api/client';

type Fixture = {
  source: string; fixture_version: number; capabilities: Capabilities;
  paper: Record<string, PaperReport>; portfolio: Portfolio;
  summaries: Record<string, Models['SessionSummary']>; operations: Operation[];
  results: Record<string, Result>; trades: TradePage['records']; research: ResearchPage['records'];
  strategy_content: Models['StrategyValidationRequest']['content']; strategy_validation: Validation;
  example_request: Record<string, unknown>;
};
const copy = <T,>(value: T): T => structuredClone(value);
type FixtureWire = Omit<Fixture, 'trades' | 'results'> & {
  results: Record<string, Omit<Result, 'result'>>;
  journal_sessions: Record<string, Models['JournalSession']>;
  trades: (Pick<TradePage['records'][number], 'schema_version' | 'realized_pnl_delta' | 'fees_delta' | 'net_realized_pnl_delta'> & { session_id: string; source_id: string })[];
};
function read<T>(value: T | undefined, signal?: AbortSignal): T {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  if (value === undefined) throw new Error('This identity is not in the synthetic fixture. Exit demo and connect the API to inspect real records.');
  return copy(value);
}
function immutable(): never { throw new Error('Synthetic demo is read-only. It cannot submit, execute, cancel or send API requests.'); }
function stable(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => JSON.stringify(key) + ':' + stable(item)).join(',') + '}';
  return JSON.stringify(value);
}

/** A separate read-only fixture adapter. Even inherited/future request paths are
 * blocked. No credential, fetch call, financial calculation or stateful job engine. */
export class DemoClient extends APIClient {
  override readonly source = 'demo' as const;
  constructor(private fixture: Fixture) { super('', 'reader'); }
  get recordedOperations() { return copy(this.fixture.operations); }
  get exampleStrategy() { return copy(this.fixture.strategy_content); }
  get exampleRequest() { return copy(this.fixture.example_request); }
  override async request<T>(_path: string, _body?: unknown, _signal?: AbortSignal, _method?: 'GET' | 'POST'): Promise<T> { return immutable(); }
  override async capabilities(signal?: AbortSignal) { return read(this.fixture.capabilities, signal); }
  override async paper(id: string, signal?: AbortSignal) { return read(this.fixture.paper[id], signal); }
  override async portfolio(id: string, signal?: AbortSignal) { return read(id === this.fixture.portfolio.config.portfolio_id ? this.fixture.portfolio : undefined, signal); }
  override async summary(id: string, signal?: AbortSignal) { return read(this.fixture.summaries[id], signal); }
  override async operation(id: string) { return read(this.fixture.operations.find(value => value.operation_id === id)); }
  override async result(id: string) { return read(this.fixture.results[id]); }
  override async submit(_body: Submission): Promise<Operation> { return immutable(); }
  override async execute(_id: string): Promise<Operation> { return immutable(); }
  override async cancel(_id: string): Promise<Operation> { return immutable(); }
  override async validate(content: Models['StrategyValidationRequest']['content']) {
    if (stable(content) !== stable(this.fixture.strategy_content)) throw new Error('Only the bundled example has recorded validation in this demo. Connect the API to validate edited content.');
    return read(this.fixture.strategy_validation);
  }
  override async trades(query: JournalQuery, signal?: AbortSignal): Promise<TradePage> {
    if (query.run_id) throw new APIError(422, 'query_rejected');
    const records = this.fixture.trades.filter(record => {
      const cfg = record.session.config.strategy;
      return (!query.session_id || query.session_id === record.source.session_id)
        && (!query.account_id || query.account_id === cfg.account.account_id)
        && (!query.instrument_id || query.instrument_id === cfg.account.instrument.instrument_id)
        && (!query.order_id || record.source.orders.some(order => order.order_id === query.order_id))
        && (!query.strategy_id || query.strategy_id === cfg.strategy_id)
        && (!query.strategy_version || query.strategy_version === cfg.strategy_version)
        && (!query.strategy_digest || query.strategy_digest === cfg.strategy_digest)
        && this.inRange(record.source.timestamp, query);
    });
    return this.page(records, query, 'trades', record => ({ timestamp: record.source.timestamp, owner_id: record.source.session_id, sequence: record.source.sequence, record_id: record.source.record_id }), signal);
  }
  override async research(query: JournalQuery, signal?: AbortSignal): Promise<ResearchPage> {
    if (query.session_id || query.account_id || query.instrument_id || query.order_id) throw new APIError(422, 'query_rejected');
    const records = this.fixture.research.filter(record => (!query.run_id || query.run_id === record.run_id)
      && this.inRange(record.timestamp, query)
      && ((!query.strategy_id && !query.strategy_version && !query.strategy_digest) || record.operation?.strategies.some(binding =>
        (!query.strategy_id || query.strategy_id === binding.strategy_id)
        && (!query.strategy_version || query.strategy_version === binding.version)
        && (!query.strategy_digest || query.strategy_digest === binding.content_digest))));
    return this.page(records, query, 'research', record => ({ timestamp: record.timestamp, owner_id: record.run_id, sequence: record.revision, record_id: record.record_id }), signal);
  }
  private inRange(timestamp: string, query: JournalQuery) {
    const time = Date.parse(timestamp);
    return (!query.start || time >= Date.parse(query.start)) && (!query.end || time < Date.parse(query.end));
  }
  private async page<T>(records: T[], query: JournalQuery, kind: string, identity: (record: T) => Omit<Models['HistoryCursor'], 'schema_version' | 'scope_digest'>, signal?: AbortSignal) {
    if (!Number.isSafeInteger(query.limit) || query.limit < 1 || query.limit > 20
      || query.start && !Number.isFinite(Date.parse(query.start)) || query.end && !Number.isFinite(Date.parse(query.end))) throw new APIError(422, 'query_rejected');
    const scope = stable(Object.fromEntries(Object.entries(query).filter(([key, value]) => !['limit', 'after'].includes(key) && value != null)));
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`synthetic-demo-v1:${kind}:${scope}`));
    const digest = Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
    const index = query.after ? records.findIndex(record => identity(record).record_id === query.after!.record_id) : -1;
    if (query.after && (query.after.scope_digest !== digest || index < 0)) throw new APIError(422, 'query_rejected');
    const selected = records.slice(index + 1, index + 1 + query.limit);
    const more = index + 1 + query.limit < records.length;
    return read({ records: selected, next_cursor: more ? { schema_version: 1 as const, scope_digest: digest, ...identity(selected[selected.length - 1]) } : null }, signal);
  }
}
export async function createDemoWorkspace() {
  const { default: raw } = await import('./fixture.json?raw');
  const wire = parseExactJSON(raw, { preserveLargeIntegers: true }) as FixtureWire;
  if (wire.fixture_version !== 2) throw new Error('Unsupported synthetic fixture version.');
  // Restore exact immutable config references removed only for bundle size.
  for (const paper of Object.values(wire.paper)) paper.snapshot.records = paper.snapshot.records.map(record =>
    ({ ...record, account: { ...record.account, config: paper.snapshot.account.config } }));
  const trades = wire.trades.map(row => {
    const source = wire.paper[row.session_id]?.snapshot.records.find(record => record.record_id === row.source_id);
    const session = wire.journal_sessions[row.session_id];
    if (!source || !session) throw new Error('Synthetic journal source is missing.');
    return { schema_version: row.schema_version, session, source, realized_pnl_delta: row.realized_pnl_delta, fees_delta: row.fees_delta, net_realized_pnl_delta: row.net_realized_pnl_delta };
  });
  const results = Object.fromEntries(Object.entries(wire.results).map(([id, retained]) => [id,
    { ...retained, result: parseExactJSON(retained.result_json, { preserveLargeIntegers: true }) } as Result]));
  const research = wire.research.map(record => record.operation ? { ...record, operation: { ...record.operation,
    result_json: results[record.operation.operation_id]?.result_json ?? record.operation.result_json } } : record);
  return new DemoClient({ ...wire, trades, results, research });
}
