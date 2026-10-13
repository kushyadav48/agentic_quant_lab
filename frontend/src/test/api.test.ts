import { describe, expect, it, vi } from 'vitest';
import { APIClient, APIError, parseExactJSON, parseObject, type Submission } from '../api/client';
import { decimal, utc } from '../format';
import { fixtures, mockFetch, response, TOKEN } from './helpers';
import schema from '../../contracts/openapi.json';

describe('exact financial transport and formatting', () => {
  it.each(['123456789123456789123456789.000100', '-0.01000', '0', '1E-38'])('preserves %s', value => {
    expect(decimal(value).replaceAll(',', '')).toBe(value);
  });
  it('never coerces a financial numeric field', () => expect(decimal(999)).toBe('Invalid decimal contract'));
  it('retains the actual ML model-digest integer in a result response without numeric rounding', async () => {
    // quantlab.ml.prediction encodes model_digest as a 256-bit FeatureParameter integer.
    const exact = '51501420275153747938262960649683394130781683753947652330100658696055273308280';
    mockFetch(() => new Response(`{"result":{"value":[{"parameters":[{"name":"model_digest","value":${exact}}]}]}}`));
    const result = await new APIClient(TOKEN, 'reader').result(fixtures.operation.operation_id);
    expect((result.result as { value: { parameters: { value: unknown }[] }[] }).value[0].parameters[0].value).toBe(exact);
  });
  it('distinguishes missing values from zero', () => { expect(decimal(null)).toBe('Unavailable'); expect(decimal('0')).toBe('0'); });
  it.each(['{"price":1.1}', '{"n":9007199254740993}', '{"n":1e3}', '{"n":NaN}', '{"n":Infinity}'])('rejects lossy JSON %s', input => expect(() => parseExactJSON(input)).toThrow());
  it('accepts decimal strings, escaped quotes and strict integer counters', () => expect(parseObject('{"price":"9007199254740993.0001","n":10,"text":"\\\" 1.2"}')).toEqual({ price: '9007199254740993.0001', n: 10, text: '" 1.2' }));
  it('requires request objects', () => expect(() => parseObject('[]')).toThrow('JSON object'));
  it('preserves large response integers without weakening Decimal or strict request validation', () => {
    const exact = '900719925474099300001';
    expect(parseExactJSON(`{"value":${exact},"negative":-${exact},"quoted":"${exact}","count":1}`, { preserveLargeIntegers: true }))
      .toEqual({ value: exact, negative: '-' + exact, quoted: exact, count: 1 });
    expect(() => parseObject(`{"value":${exact}}`)).toThrow('safe integers');
    for (const text of ['{"price":1.1}', '{"price":1e3}', '{"value":9007199254740993,"value":0}'])
      expect(() => parseExactJSON(text, { preserveLargeIntegers: true })).toThrow();
  });
  it.each(['{"a":1,"a":2}', '{"nested":{"price":"1","price":"2"}}', '{"x":1,"\\u0078":2}'])('rejects duplicate keys %s', input => expect(() => parseExactJSON(input)).toThrow('Duplicate'));
  it('allows the same key in different objects', () => expect(parseExactJSON('[{"a":1},{"a":2}]')).toEqual([{ a: 1 }, { a: 2 }]));
  it('shows UTC independently of host locale', () => expect(utc('2026-01-01T05:30:00+05:30')).toBe('2026-01-01 00:00:00.000 UTC'));
  it('preserves sub-millisecond source precision in timestamp labels', () => expect(utc('2026-01-01T00:00:00.123456+00:00')).toBe('2026-01-01 00:00:00.123456 UTC'));
});
describe('actual Phase 21 API contracts', () => {
  it('contains every dashboard route and excludes unsupported mutations', () => {
    const paths = Object.keys(schema.paths);
    expect(paths).toContain('/api/v1/journal/trades/query');
    expect(paths).toContain('/api/v1/research/operations/{operation_id}/execute');
    expect(paths.some(path => /approve|upload|submit_order|journal\/notes/.test(path))).toBe(false);
  });
  it('uses same-origin auth, explicit methods, encoded identities, and omits browser credentials', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    mockFetch((path, init) => { calls.push([path, init]); return response(path.includes('snapshot') ? fixtures.paper : fixtures.capabilities); });
    const api = new APIClient(TOKEN, 'reader');
    expect((await api.capabilities()).execution).toBe('explicit_process_local');
    await api.paper('session:a');
    expect(calls[1][0]).toBe('/api/v1/paper/sessions/session%3Aa/snapshot');
    expect(calls[0][1]).toMatchObject({ credentials: 'omit', redirect: 'error', cache: 'no-store', method: 'GET' });
    expect(calls[0][1]?.headers).toEqual({ Authorization: `Bearer ${TOKEN}` });
  });
  it('reader clients reject submission/execution/cancel before any network call', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); const api = new APIClient(TOKEN, 'reader');
    for (const work of [() => api.submit({} as Submission), () => api.execute('a'.repeat(64)), () => api.cancel('a'.repeat(64))]) expect(work).toThrow(APIError);
    expect(fetch).not.toHaveBeenCalled();
  });
  it('operator submit never starts execution', async () => {
    mockFetch(() => response({ ...fixtures.operation, state: 'queued' }, 201));
    const api = new APIClient(TOKEN, 'operator');
    await api.submit({ idempotency_key: 'test', operation: { kind: 'performance' } } as Submission);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/v1/research/operations');
  });
  it('execute and cancel use bodyless POST contracts', async () => {
    mockFetch(() => response(fixtures.operation)); const api = new APIClient(TOKEN, 'operator');
    await api.execute(fixtures.operation.operation_id); await api.cancel(fixtures.operation.operation_id);
    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect(init?.method).toBe('POST'); expect(init?.body).toBeUndefined(); expect(init?.headers).toEqual({ Authorization: `Bearer ${TOKEN}` });
    }
  });
  it('bounds UTF-8 bodies and validates operation digests locally', async () => {
    vi.stubGlobal('fetch', vi.fn()); const api = new APIClient(TOKEN, 'operator', 30);
    await expect(api.request('/strategies/validate', { content: 'x'.repeat(40) })).rejects.toThrow('byte limit');
    expect(() => api.operation('../../private')).toThrow('SHA-256');
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([[401, 'unauthorized'], [403, 'forbidden'], [503, 'recovery_required'], [503, 'service_busy'], [422, 'query_rejected']])('sanitizes %s %s', async (status, code) => {
    mockFetch(() => response({ code, message: 'private-payload-token', request_id: 'test-correlation' }, status as number));
    try { await new APIClient(TOKEN, 'reader').capabilities(); throw new Error('Expected failure'); }
    catch (error) { expect(error).toBeInstanceOf(APIError); expect((error as Error).message).not.toContain('private-payload'); expect((error as APIError).requestId).toBe('test-correlation'); }
  });
  it('keeps tokens out of browser storage and logs', async () => {
    const local = vi.spyOn(Storage.prototype, 'setItem'); const log = vi.spyOn(console, 'log');
    mockFetch(() => response(fixtures.capabilities)); await new APIClient(TOKEN, 'reader').capabilities();
    expect(local).not.toHaveBeenCalled(); expect(log).not.toHaveBeenCalled();
  });
  it.each([['unauthenticated', 401, 'Credential rejected'], ['invalid_request', 422, 'violates the API contract']])('explains the actual backend %s error', async (code, status, message) => {
    mockFetch(() => response({ code, message: 'private-server-detail' }, status as number));
    await expect(new APIClient(TOKEN, 'reader').capabilities()).rejects.toThrow(message as string);
  });
});
