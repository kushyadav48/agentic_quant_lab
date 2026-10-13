import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { chartPoint, debugData } from '../debugger/data';
import { fixtures } from './helpers';
import { Visualization } from '../pages/Debugger';
vi.mock('../debugger/Chart', () => ({ Chart: () => <div aria-label="Mock chart surface" /> }));

describe('authoritative debugger provenance', () => {
  it('plots exactly recorded quote sides, bar closes and execution records', () => {
    const data = debugData(fixtures.paper);
    const observations = fixtures.paper.snapshot.records.flatMap(record => record.market_event?.observation ? [record.market_event.observation] : []);
    const quotes = observations.filter(observation => 'quote' in observation);
    expect(data.bid).toHaveLength(new Set(quotes.map(observation => observation.quote.timestamp)).size);
    data.bid.forEach(point => {
      expect(quotes.some(observation => observation.quote.bid === point.exact && Date.parse(observation.quote.timestamp) / 1000 === point.time)).toBe(true);
    });
    const fills = fixtures.paper.snapshot.records.flatMap(record => record.orders.filter(order => 'execution' in order));
    expect(data.entries.length + data.exits.length).toBe(fills.length);
    expect(data.entries[0].exact).toBe(fills[0].execution.execution_price);
    expect(data.entries[0].sourceId).toBe(fills[0].event_id);
  });
  it('does not invent execution prices for decisions or order transitions', () => {
    const events = debugData(fixtures.paper).events;
    expect(events.filter(event => event.kind.startsWith('decision:')).every(event => event.price === undefined)).toBe(true);
    expect(events.filter(event => event.kind.startsWith('order:')).every(event => event.price === undefined)).toBe(true);
  });
  it('has no fabricated prices or fills when records are absent', () => {
    const report = structuredClone(fixtures.paper); report.snapshot.records = [];
    expect(debugData(report)).toEqual({ bid: [], ask: [], close: [], entries: [], exits: [], observations: [], events: [], omitted: 0 });
  });
  it('omits duplicate chart times without shifting actual timestamps', () => {
    const report = structuredClone(fixtures.paper);
    const quoteRecord = report.snapshot.records.find(record => record.market_event?.observation && 'quote' in record.market_event.observation)!;
    report.snapshot.records.push({ ...quoteRecord, record_id: 'test-only-duplicate' });
    const data = debugData(report);
    expect(data.omitted).toBeGreaterThan(0);
    expect(data.bid.map(point => point.time).length).toBe(new Set(data.bid.map(point => point.time)).size);
    expect(data.events.some(event => event.id === 'test-only-duplicate')).toBe(true);
    expect(data.observations.filter(point => point.sourceId === quoteRecord.market_event?.event_id)).toHaveLength(4);
  });
  it.each(['1e9999', '1e-9999'])('omits unsupported chart range %s', price => expect(chartPoint(price, '2026-01-01T00:00:00Z', 'source')).toBeUndefined());
  it('retains high-precision exact values alongside approximate chart coordinates', () => {
    const point = chartPoint('100.123456789123456789', '2026-01-01T00:00:00Z', 'record');
    expect(point?.exact).toBe('100.123456789123456789'); expect(point?.sourceId).toBe('record');
  });
  it('supports accessible event selection and displays causal context', async () => {
    render(<Visualization report={fixtures.paper} />);
    const event = debugData(fixtures.paper).events.find(value => value.kind.startsWith('decision:'))!;
    await userEvent.click(screen.getByRole('button', { name: new RegExp(`#${event.sequence} · decision:`) }));
    expect(screen.getByText(event.id)).toBeInTheDocument();
    expect(screen.getAllByText('Not recorded for this event')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Inspect selected causal event' })).toBeInTheDocument();
  });
  it('offers bounded tables rather than rendering the entire history', () => {
    const report = structuredClone(fixtures.paper); const record = report.snapshot.records[0];
    report.snapshot.records = Array.from({ length: 100 }, (_, index) => ({ ...record, record_id: `record-${index}`, sequence: index + 1 }));
    render(<Visualization report={report} />);
    expect(screen.getByRole('button', { name: 'Next events' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /#100/ })).not.toBeInTheDocument();
  });
});
