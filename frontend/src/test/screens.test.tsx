import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { APIClient } from '../api/client';
import { App } from '../App';
import { PaperView, PortfolioView } from '../pages/Reports';
import { Research, Results } from '../pages/Research';
import { Strategy } from '../pages/Strategy';
import { Journal } from '../pages/Journal';
import { fixtures, mockFetch, response, TOKEN } from './helpers';
vi.mock('../debugger/Chart', () => ({ Chart: () => <div aria-label="Mock chart surface" /> }));
const reader = () => new APIClient(TOKEN, 'reader');
const operator = () => new APIClient(TOKEN, 'operator');

describe('authentication and accessible navigation', () => {
  it('starts disconnected without fake metrics or mutation controls', () => {
    render(<App />);
    expect(screen.getByText('API disconnected')).toBeInTheDocument();
    expect(screen.getByLabelText('Bearer token')).toBeInTheDocument();
    expect(screen.queryByText('Current equity')).not.toBeInTheDocument();
    for (const name of ['Approve strategy', 'Submit order', 'Save journal note', 'Resume session']) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
  });
  it('connects, clears credential input, follows hash navigation, and disconnects', async () => {
    mockFetch(() => response({ ...fixtures.capabilities, portfolios: [], paper_sessions: [] }));
    render(<App />);
    await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN);
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText('Local API connected')).toBeInTheDocument();
    expect(screen.queryByLabelText('Bearer token')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: /Strategy studio/ }));
    expect(await screen.findByRole('heading', { name: 'Strategy studio' })).toBeInTheDocument();
    expect(location.hash).toBe('#/strategy');
    expect(screen.getByRole('link', { name: /Strategy studio/ })).toHaveAttribute('aria-current', 'page');
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(screen.getByLabelText('Bearer token')).toHaveValue('');
    expect(screen.getByText('API disconnected')).toBeInTheDocument();
  });
  it('shows sanitized authentication failures', async () => {
    mockFetch(() => response({ code: 'unauthorized', message: 'secret', request_id: 'auth-test' }, 401));
    render(<App />); await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN); await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Credential rejected'); expect(screen.getByText('API disconnected')).toBeInTheDocument();
  });
  it('retains drafts across navigation and clears them on disconnect', async () => {
    mockFetch(() => response({ ...fixtures.capabilities, portfolios: [], paper_sessions: [] }));
    render(<App />); await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN); await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByText('Local API connected');
    await userEvent.click(screen.getByRole('link', { name: /Strategy studio/ }));
    await userEvent.clear(screen.getByLabelText('Local strategy label')); await userEvent.type(screen.getByLabelText('Local strategy label'), 'retained-hypothesis');
    await userEvent.click(screen.getByRole('link', { name: /Journal workspace/ })); await userEvent.click(screen.getByRole('link', { name: /Strategy studio/ }));
    expect(screen.getByLabelText('Local strategy label')).toHaveValue('retained-hypothesis');
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' })); expect(screen.queryByLabelText('Local strategy label')).not.toBeInTheDocument();
  });
  it('shows loading and absent catalogue states', async () => {
    let resolve!: (response: Response) => void; mockFetch(() => new Promise<Response>(done => { resolve = done; }));
    render(<App />); await userEvent.type(screen.getByLabelText('Bearer token'), TOKEN); await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(screen.getByRole('button', { name: 'Connecting…' })).toBeDisabled();
    resolve(response({ ...fixtures.capabilities, portfolios: [] }));
    expect(await screen.findByText(/No portfolios registered/)).toBeInTheDocument();
  });
  it('allows the offline demo without a credential and labels it', async () => {
    vi.stubGlobal('fetch', vi.fn()); render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /Explore the offline visual debugger/ }));
    await userEvent.selectOptions(await screen.findByLabelText('Chart source'), '__offline_fixture__');
    expect(await screen.findByText('OFFLINE DEMO', { selector: '.badge' })).toBeInTheDocument();
    expect(await screen.findByText(/Fixed synthetic fixture/)).toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
  });
});
describe('paper and portfolio reporting', () => {
  it.each(['stale', 'missing'] as const)('suppresses current valuations when %s while retaining committed balance', status => {
    const value = structuredClone(fixtures.portfolio); value.members[0].valuation_status = status;
    render(<PortfolioView value={value} />);
    expect(screen.getByText('Current equity').parentElement).toHaveTextContent('Unavailable');
    expect(screen.getByText('Gross exposure').parentElement).toHaveTextContent('Unavailable');
    expect(screen.getByText(/Missing or stale valuation/)).toBeInTheDocument();
  });
  it('distinguishes a historical paper mark and shows recovery gates', () => {
    const value = structuredClone(fixtures.paper); value.operator_required = true; value.snapshot.feed.reason = 'stale';
    render(<PaperView value={value} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Operator recovery required');
    expect(screen.getByText(/not a current valuation/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resume|submit order/i })).not.toBeInTheDocument();
  });
});
describe('research lifecycle and strategy validation', () => {
  const researchProps = { capabilities: fixtures.capabilities, operations: [fixtures.operation], selected: fixtures.operation, retain: vi.fn(), select: vi.fn(), showResult: vi.fn() };
  it('keeps reader actions and terminal execute/cancel disabled', () => {
    render(<Research {...researchProps} api={reader()} />);
    expect(screen.getByRole('button', { name: 'Submit only' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Execute queued operation' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel queued operation' })).toBeDisabled();
  });
  it('executes queued operations only through the explicit button', async () => {
    mockFetch(() => response(fixtures.operation)); const retain = vi.fn();
    render(<Research {...researchProps} api={operator()} selected={{ ...fixtures.operation, state: 'queued' }} retain={retain} />);
    expect(fetch).not.toHaveBeenCalled(); await userEvent.click(screen.getByRole('button', { name: 'Execute queued operation' }));
    await waitFor(() => expect(retain).toHaveBeenCalledWith(fixtures.operation));
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain('/execute');
  });
  it('reports failed and cancelled states without exposing execution', () => {
    render(<Research {...researchProps} api={operator()} selected={{ ...fixtures.operation, state: 'failed', failure: 'domain_rejection' }} />);
    expect(screen.getByText('domain_rejection')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Explore result' })).toBeDisabled();
  });
  it('invalidates validation on local edits and cannot approve', async () => {
    mockFetch(() => response({ valid: true, content_digest: 'a'.repeat(64), issues: [] }));
    render(<Strategy api={reader()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Validate draft' }));
    expect(await screen.findByText('VALIDATED')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Local version label'), '2');
    expect(screen.getByText('DRAFT')).toBeInTheDocument(); expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
  });
  it('shows validation issues from the supported API', async () => {
    mockFetch(() => response({ valid: false, content_digest: null, issues: [{ location: ['entry'], code: 'missing', message: 'Input violates the strategy contract.' }] }));
    render(<Strategy api={reader()} />); await userEvent.click(screen.getByRole('button', { name: 'Validate draft' }));
    expect(await screen.findByText('Input violates the strategy contract.')).toBeInTheDocument();
  });
  it('verifies canonical result digests before display', async () => {
    mockFetch(() => response(fixtures.result)); render(<Results api={reader()} selected={fixtures.operation} />);
    expect(await screen.findByText('SHA-256 verified')).toBeInTheDocument();
  });
  it('fails closed on a mismatched result digest', async () => {
    mockFetch(() => response({ ...fixtures.result, result_digest: '0'.repeat(64) })); render(<Results api={reader()} selected={fixtures.operation} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('provenance verification failed'); expect(screen.queryByText('SHA-256 verified')).not.toBeInTheDocument();
  });
  it('displays metrics from the verified canonical bytes rather than an unbound result projection', async () => {
    mockFetch(() => response({ ...fixtures.result, result: { success: true, value: { unexpected_metric: 'UNBOUND_FINANCIAL_METRIC' }, issues: [] } }));
    render(<Results api={reader()} selected={fixtures.operation} />);
    await screen.findByText('SHA-256 verified');
    expect(screen.queryByText('UNBOUND_FINANCIAL_METRIC')).not.toBeInTheDocument();
  });
  it('verifies canonical results containing exact large provenance integers', async () => {
    const exact = '51501420275153747938262960649683394130781683753947652330100658696055273308280';
    const markup = '<img src=x onerror="globalThis.researchInjected=true">';
    const canonical = `{"success":true,"value":{"model_digest":${exact},"note":${JSON.stringify(markup)}},"issues":[]}`;
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical))), byte => byte.toString(16).padStart(2, '0')).join('');
    mockFetch(() => response({ ...fixtures.result, result_digest: digest, result_json: canonical }));
    render(<Results api={reader()} selected={{ ...fixtures.operation, result_digest: digest }} />);
    await screen.findByText('SHA-256 verified');
    expect(screen.getByText(exact, { selector: 'dd' })).toBeInTheDocument();
    expect(screen.getByText(markup, { selector: 'dd' })).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
  });
});
describe('journal query and pagination', () => {
  it('sends server filters, preserves cursors, and resets pagination on new filters', async () => {
    const bodies: Record<string, unknown>[] = [];
    mockFetch((_path, init) => { const body = JSON.parse(init!.body as string); bodies.push(body); return response(body.after ? { records: [], next_cursor: null } : fixtures.trades); });
    render(<Journal api={reader()} capabilities={fixtures.capabilities} inspectOperation={vi.fn()} />);
    await screen.findByRole('button', { name: 'Next page' });
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(bodies[1].after).toEqual(fixtures.trades.next_cursor));
    await userEvent.type(screen.getByLabelText('strategy id'), 'a'); await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() => expect(bodies.at(-1)).toMatchObject({ strategy_id: 'a', after: null }));
    expect(screen.getByText(/Page 1 ·/)).toBeInTheDocument();
  });
  it('bounds page size and shows incomplete-position summaries', async () => {
    mockFetch(path => response(path.includes('summary') ? fixtures.summary : { records: [], next_cursor: null }));
    render(<Journal api={reader()} capabilities={fixtures.capabilities} inspectOperation={vi.fn()} />);
    const size = screen.getByLabelText('Page size'); expect(within(size).getAllByRole('option')).toHaveLength(3);
    await userEvent.type(screen.getByLabelText('Session identity'), 'session:a'); await userEvent.click(screen.getByRole('button', { name: 'Load summary' }));
    expect(await screen.findByText('OPEN / INCOMPLETE POSITION')).toBeInTheDocument();
  });
  it('does not query absent journals', () => {
    vi.stubGlobal('fetch', vi.fn()); render(<Journal api={reader()} capabilities={{ ...fixtures.capabilities, journal: false }} inspectOperation={vi.fn()} />);
    expect(screen.getByText(/journal service is not configured/)).toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled();
  });
});
