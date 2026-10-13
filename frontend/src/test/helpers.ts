import { vi } from 'vitest';
import fixture from './fixtures.json';
import type { Capabilities, Operation, PaperReport, Portfolio, Result, TradePage, ResearchPage, Models } from '../api/client';
export const fixtures = fixture as unknown as { capabilities: Capabilities; operation: Operation; paper: PaperReport; portfolio: Portfolio;
  result: Result; trades: TradePage; research: ResearchPage; summary: Models['SessionSummary']; source: string };
export function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
export function mockFetch(handler: (path: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => handler(input, init)));
}
export const TOKEN = 'frontend-test-reader-credential-'.padEnd(50, 'r');
