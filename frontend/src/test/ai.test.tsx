import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { APIClient } from '../api/client';
import { AIResearch } from '../pages/AIResearch';
import { mockFetch, response, TOKEN } from './helpers';
import { DemoClient } from '../demo/client';
const id = 'a'.repeat(64);
const config = { enabled: true, provider_id: 'gemini', model_id: 'gemini-test', state: 'ready',
  free_only: true, requests_reserved: 0, maximum_requests: 20, maximum_input_tokens: 32768,
  maximum_output_tokens: 4096, maximum_concurrency: 1, active: false, timeout_ms: 20000, maximum_jobs: 32 };
const job = { job_id: id, request_digest: 'b'.repeat(64), provider_id: 'gemini', model_id: 'gemini-test', schema_version: 'interpretation-v1', state: 'queued', created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z', result_digest: null, failure: null };

describe('cloud research UI and authenticated routes', () => {
  it('requires explicit configuration, input selection, consent and execution', async () => {
    const calls: string[] = [];
    mockFetch((path, init) => { calls.push(path); if (path.endsWith('/configuration')) return response(config);
      expect(init?.headers).toMatchObject({ Authorization: `Bearer ${TOKEN}` });
      if (path.endsWith('/execute')) return response({ ...job, state: 'completed' });
      expect(JSON.parse(init!.body as string)).toMatchObject({ external_transmission_approved: true, input: { strategy_text: 'Non-sensitive strategy instructions' } });
      return response(job);
    });
    render(<AIResearch api={new APIClient(TOKEN, 'operator')} useJob={vi.fn()} />);
    expect(calls).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Submit AI interpretation' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Check cloud configuration' }));
    await userEvent.type(screen.getByLabelText('AI idempotency key'), 'selected-1');
    await userEvent.type(screen.getByLabelText('Selected research text'), 'Non-sensitive strategy instructions');
    await userEvent.click(screen.getByLabelText(/I approve transmitting/));
    await userEvent.click(screen.getByRole('button', { name: 'Submit AI interpretation' }));
    expect(await screen.findByText(id)).toBeInTheDocument();
    expect(calls).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: 'Execute AI interpretation' }));
    expect(await screen.findByText('completed')).toBeInTheDocument();
    expect(calls.at(-1)).toBe(`/api/v1/ai/interpretations/${id}/execute`);
  });
  it.each(['cloud_disabled', 'credentials_missing', 'free_policy_rejected', 'provider_unavailable', 'budget_exhausted'])('shows %s and disables submission', async state => {
    mockFetch(() => response({ ...config, state }));
    render(<AIResearch api={new APIClient(TOKEN, 'operator')} useJob={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Check cloud configuration' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit AI interpretation' })).toBeDisabled();
  });
  it.each(['rate_limited', 'quota_exhausted', 'invalid_model_response', 'research_validation_rejected', 'provider_timeout'])('shows terminal %s without another request', async failure => {
    const calls: string[] = [];
    mockFetch(path => { calls.push(path); return response({ ...job, state: 'failed', failure }); });
    render(<AIResearch api={new APIClient(TOKEN, 'reader')} useJob={vi.fn()} />);
    await userEvent.type(screen.getByLabelText('Existing AI job ID'), id);
    await userEvent.click(screen.getByRole('button', { name: 'Look up AI job' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Execute AI interpretation' })).toBeDisabled();
    expect(calls).toHaveLength(1);
  });
  it('rejects reader actions and demo inherited cloud methods before fetch', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const reader = new APIClient(TOKEN, 'reader');
    expect(() => reader.aiExecute(id)).toThrow(/operator/);
    const demo = new DemoClient({} as never);
    for (const action of [() => demo.aiConfiguration(), () => demo.aiStatus(id), () => demo.aiExecute(id), () => demo.aiResearch(id, {} as never)]) expect(action).toThrow(/Synthetic Demo/);
    expect(fetch).not.toHaveBeenCalled();
  });
  it('checks the canonical AI artifact digest', async () => {
    mockFetch(() => response({ job_id: id, result_digest: id, result_json: '{}', result: {} }));
    await expect(new APIClient(TOKEN, 'reader').aiResult(id)).rejects.toThrow(/digest verification/);
  });
  it('presents the verified canonical proposal and binds it only by explicit action', async () => {
    const artifact = { interpretation: { status: 'READY' }, proposal: { state: 'draft', approval: null } };
    const wire = JSON.stringify(artifact);
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(wire));
    const hex = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, '0')).join('');
    mockFetch(path => path.endsWith('/result') ? response({ job_id: id, result_digest: hex, result_json: wire, result: { proposal: null } }) : response({ ...job, state: 'completed' }));
    const bind = vi.fn();
    render(<AIResearch api={new APIClient(TOKEN, 'operator')} useJob={bind} />);
    await userEvent.type(screen.getByLabelText('Existing AI job ID'), id);
    await userEvent.click(screen.getByRole('button', { name: 'Look up AI job' }));
    await screen.findByText('completed');
    await userEvent.click(screen.getByRole('button', { name: 'Inspect AI result' }));
    expect(await screen.findByText(/Deterministically validated draft/)).toBeInTheDocument();
    expect(bind).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Bind reviewed research to this AI job' }));
    expect(bind).toHaveBeenCalledWith(id);
  });
});
