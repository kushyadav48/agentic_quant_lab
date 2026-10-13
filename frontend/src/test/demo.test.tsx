import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createDemoWorkspace } from '../demo/client';
import { App } from '../App';
import { APIClient, parseExactJSON, type Submission } from '../api/client';
import { RecordChart, accountPoints } from '../RecordChart';
import { Results } from '../pages/Research';
import { fixtures, mockFetch, response, TOKEN } from './helpers';
vi.mock('../debugger/Chart', () => ({ Chart: () => <div aria-label="Mock chart surface" /> }));

describe('synthetic workspace isolation', () => {
  it('uses only bundled Python records and never reaches an API or storage', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    const demo = await createDemoWorkspace();
    const caps = await demo.capabilities();
    expect(demo.source).toBe('demo'); expect(caps.paper_sessions).toHaveLength(3);
    expect(caps.portfolios).toEqual(['portfolio']);
    const portfolio = await demo.portfolio('portfolio');
    expect(portfolio.members).toHaveLength(3); expect(portfolio.config.total_capital).toBe('100000');
    for (const id of caps.paper_sessions) {
      const paper = await demo.paper(id); expect(paper.snapshot.records).toHaveLength(28);
      expect(paper.snapshot.account.equity).toEqual(expect.any(String));
      for (const record of paper.snapshot.records) expect(record.account.config).toEqual(paper.snapshot.account.config);
      expect((await demo.summary(id)).lifecycle).toBe('open');
    }
    expect(fetch).not.toHaveBeenCalled(); expect(storage).not.toHaveBeenCalled();
  });
  it('cannot execute, mutate or fall through to inherited requests even if role is changed', async () => {
    vi.stubGlobal('fetch', vi.fn()); const demo = await createDemoWorkspace(); demo.role = 'operator';
    for (const work of [() => demo.submit({} as Submission), () => demo.execute('a'.repeat(64)), () => demo.cancel('a'.repeat(64)), () => demo.request('/capabilities')])
      await expect(work()).rejects.toThrow('read-only');
    expect(fetch).not.toHaveBeenCalled();
  });
  it('verifies all nine original canonical artifacts including large provenance integers', async () => {
    const demo = await createDemoWorkspace(); expect(demo.recordedOperations).toHaveLength(9);
    for (const operation of demo.recordedOperations) {
      const result = await demo.result(operation.operation_id);
      const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(result.result_json));
      const digest = Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
      expect(digest).toBe(operation.result_digest); expect(result.result_digest).toBe(digest);
      expect(parseExactJSON(result.result_json, { preserveLargeIntegers: true })).toEqual(result.result);
    }
    const feature = demo.recordedOperations.find(value => value.kind === 'ml_prediction_features')!;
    expect((await demo.result(feature.operation_id)).result_json).toContain('51501420275153747938262960649683394130781683753947652330100658696055273308280');
  });
  it('renders every recorded result kind through canonical verification', async () => {
    const demo = await createDemoWorkspace();
    for (const selected of demo.recordedOperations) {
      const view = render(<Results api={demo} selected={selected} />);
      expect(await within(view.container).findByText('SHA-256 verified')).toBeInTheDocument();
      expect(within(view.container).queryByRole('alert')).not.toBeInTheDocument();
      view.unmount();
    }
  });
  it('keeps returned objects independent and refuses unknown API identities', async () => {
    const demo = await createDemoWorkspace(); const first = await demo.portfolio('portfolio'); first.balance = '0';
    expect((await demo.portfolio('portfolio')).balance).not.toBe('0');
    await expect(demo.paper('session:a')).rejects.toThrow('not in the synthetic fixture');
    const signal = AbortSignal.abort(); await expect(demo.paper('session:threshold_long', signal)).rejects.toThrow('Aborted');
  });
  it('pages the complete fixed history without duplicates and binds cursors to fixture filters', async () => {
    const demo = await createDemoWorkspace(); const records: string[] = []; let cursor;
    do {
      const page = await demo.trades({ limit: 10, after: cursor });
      records.push(...page.records.map(record => record.source.record_id)); cursor = page.next_cursor;
    } while (cursor);
    expect(records).toHaveLength(84); expect(new Set(records).size).toBe(84);
    const page = await demo.trades({ limit: 5 });
    await expect(demo.trades({ limit: 5, session_id: 'session:threshold_long', after: page.next_cursor })).rejects.toThrow('cursor');
    expect((await demo.trades({ limit: 20, strategy_id: 'unknown' })).records).toEqual([]);
  });
  it('filters recorded trade/research attribution and respects half-open UTC bounds', async () => {
    const demo = await createDemoWorkspace(); const first = await demo.trades({ limit: 1, session_id: 'session:threshold_long' });
    const time = first.records[0].source.timestamp;
    expect((await demo.trades({ limit: 20, session_id: 'session:threshold_long', end: time })).records).toEqual([]);
    expect((await demo.trades({ limit: 20, session_id: 'session:threshold_long', start: time })).records).toHaveLength(20);
    const strategy = demo.recordedOperations[0].strategies[0];
    expect((await demo.research({ limit: 20, strategy_id: strategy.strategy_id })).records.length).toBeGreaterThan(0);
    await expect(demo.research({ limit: 10, account_id: 'account:a' })).rejects.toThrow('query');
    await expect(demo.trades({ limit: 21 })).rejects.toThrow('query');
  });
  it('replays recorded validation only for the bundled content', async () => {
    const demo = await createDemoWorkspace(); expect((await demo.validate(demo.exampleStrategy)).valid).toBe(true);
    await expect(demo.validate({})).rejects.toThrow('recorded validation');
  });
});

describe('workstation navigation and source boundaries', () => {
  it('exercises all seven pages in explicit demo mode without API calls', async () => {
    vi.stubGlobal('fetch', vi.fn()); render(<App />);
    expect(screen.queryByText('100,000')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Explore synthetic demo' }));
    await screen.findByText('Synthetic demo active');
    expect(await screen.findByRole('img', { name: /Selected paper account equity/ })).toBeInTheDocument();
    for (const title of ['Research workbench', 'Strategy studio', 'Results explorer', 'Paper sessions', 'Journal workspace', 'Visual debugger', 'Portfolio overview']) {
      await userEvent.click(screen.getByRole('link', { name: title }));
      expect(await screen.findByRole('heading', { name: title === 'Visual debugger' ? 'Visual strategy debugger' : title })).toBeVisible();
      expect(screen.getByText('Illustrative records, never real financial results.')).toBeVisible();
      if (title === 'Research workbench') {
        expect(screen.getByRole('button', { name: 'Submit only' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Execute queued operation' })).toBeDisabled();
      }
      if (title === 'Results explorer') expect(await screen.findByText('SHA-256 verified')).toBeInTheDocument();
      if (title === 'Visual debugger') expect(await screen.findByText('SYNTHETIC DEMO SNAPSHOT')).toBeInTheDocument();
    }
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Exit demo' }));
    expect(screen.getByLabelText('Bearer token')).toHaveValue('');
    expect(screen.queryByText('100,000')).not.toBeInTheDocument();
  });
  it('keeps a connected empty catalogue empty until the user explicitly switches environments', async () => {
    mockFetch(() => response({ ...fixtures.capabilities, portfolios: [], paper_sessions: [] }));
    render(<App />); await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN); await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText(/No portfolios registered/)).toBeInTheDocument();
    expect(screen.queryByText('100,000')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Switch to synthetic demo' }));
    expect(await screen.findByText('Synthetic demo active')).toBeInTheDocument();
    await screen.findAllByText('100,000'); expect(fetch).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Exit demo' }));
    expect(screen.getByText('API disconnected')).toBeInTheDocument();
  });
  it('ignores a late API connection response after entering demo', async () => {
    let resolve!: (response: Response) => void; mockFetch(() => new Promise<Response>(done => { resolve = done; }));
    render(<App />); await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN); await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await userEvent.click(screen.getByRole('button', { name: 'Explore synthetic demo' }));
    await screen.findByText('Synthetic demo active'); resolve(response(fixtures.capabilities));
    await waitFor(() => expect(screen.queryByText('Local API connected')).not.toBeInTheDocument());
    expect(screen.getByText('Synthetic demo active')).toBeVisible();
  });
});

describe('recorded account chart', () => {
  it('plots actual marks and offers keyboard-accessible windows and exact selection', async () => {
    const demo = await createDemoWorkspace(); const paper = await demo.paper('session:threshold_long');
    const points = accountPoints(paper); expect(points).toHaveLength(paper.snapshot.records.length);
    render(<RecordChart points={points} label="Recorded equity" unit="USD" />);
    expect(screen.getByRole('img', { name: /28 recorded marks/ })).toBeInTheDocument();
    screen.getByRole('button', { name: 'Last 10' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('img', { name: /10 recorded marks/ })).toBeInTheDocument();
    const selector = screen.getByLabelText('Recorded equity exact mark');
    expect(within(selector).getAllByRole('option')).toHaveLength(10);
    await userEvent.selectOptions(selector, '0'); expect(selector).toHaveValue('0');
    expect(screen.getByText(/recorded mark$/)).toHaveTextContent('UTC');
  });
  it('retains exact Decimal and causal timestamp precision in readouts', () => {
    const exact = '100.1234567890123456789';
    render(<RecordChart points={[{ time: 1767225600.123, timestamp: '2026-01-01T00:00:00.123456+00:00', value: Number(exact), exact, sourceId: 'record' }]} label="Precision fixture" />);
    expect(screen.getByText(exact, { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText(/recorded mark$/)).toHaveTextContent('00:00:00.123456 UTC');
  });
  it('does not invent marks for absent or unsupported history', () => {
    render(<RecordChart points={[]} label="Absent history" />);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText(/No recorded equity history/)).toBeInTheDocument();
    const api = new APIClient(TOKEN, 'reader'); expect(api.source).toBe('api');
  });
});
