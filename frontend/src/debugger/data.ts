import type { SessionRecord, PaperReport } from '../api/client';
export type Point = { time: number; exact: string; value: number; sourceId: string };
export type DebugEvent = { id: string; time: string; kind: string; sequence: number; price?: string; quantity?: string; record: unknown; sessionRecord: SessionRecord };
export type DebugData = { bid: Point[]; ask: Point[]; close: Point[]; entries: Point[]; exits: Point[]; observations: { time: string; exact: string; sourceId: string; kind: string }[]; events: DebugEvent[]; omitted: number };

/** Number conversions are exclusively for chart pixels, never accounting.
 * Non-finite/underflow values are omitted and exact source strings stay visible. */
export function chartPoint(exact: unknown, timestamp: string, sourceId: string): Point | undefined {
  if (typeof exact !== 'string' || !/^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(exact)) return;
  const value = Number(exact), time = Date.parse(timestamp) / 1000;
  if (!Number.isFinite(value) || !Number.isFinite(time) || value === 0 && /[1-9]/.test(exact.split(/[eE]/)[0])) return;
  return { time, value, exact, sourceId };
}
export function debugData(report: PaperReport): DebugData {
  const data: DebugData = { bid: [], ask: [], close: [], entries: [], exits: [], observations: [], events: [], omitted: 0 };
  function point(series: Point[], exact: unknown, timestamp: string, sourceId: string) {
    const item = chartPoint(exact, timestamp, sourceId);
    if (item) series.push(item); else data.omitted++;
  }
  for (const record of report.snapshot.records) {
    data.events.push({ id: record.record_id, time: record.timestamp, kind: record.reason, sequence: record.sequence, record, sessionRecord: record });
    const market = record.market_event;
    const observation = market?.observation;
    if (observation && 'quote' in observation) {
      for (const kind of ['bid', 'ask'] as const) {
        const exact = observation.quote[kind];
        if (typeof exact === 'string') data.observations.push({ time: observation.quote.timestamp, exact, sourceId: market!.event_id, kind });
      }
      point(data.bid, observation.quote.bid, observation.quote.timestamp, market!.event_id);
      point(data.ask, observation.quote.ask, observation.quote.timestamp, market!.event_id);
    } else if (observation && 'bar' in observation) {
      if (typeof observation.bar.close === 'string') data.observations.push({ time: observation.bar.end_time, exact: observation.bar.close, sourceId: market!.event_id, kind: `bar close (${observation.bar.price_type})` });
      point(data.close, observation.bar.close, observation.bar.end_time, market!.event_id);
    }
    if (record.decision) data.events.push({ id: record.decision.record_id, time: record.decision.timestamp, kind: `decision: ${record.decision.reason}`, sequence: record.sequence, record: record.decision, sessionRecord: record });
    for (const order of record.orders) {
      const event: DebugEvent = { id: order.event_id, time: order.timestamp, kind: order.kind === 'transition' ? `order: ${order.state}` : order.kind, sequence: record.sequence, record: order, sessionRecord: record };
      if ('execution' in order) {
        event.price = typeof order.execution.execution_price === 'string' ? order.execution.execution_price : undefined;
        event.quantity = typeof order.execution.quantity === 'string' ? order.execution.quantity : undefined;
        event.time = order.execution.execution_time;
        event.kind = order.execution.action;
        point(order.execution.action.startsWith('enter') ? data.entries : data.exits, event.price, event.time, event.id);
      }
      data.events.push(event);
    }
    for (const event of record.oco_events ?? []) data.events.push({ id: event.record_id, time: event.group.timestamp, kind: 'oco revision', sequence: record.sequence, record: event, sessionRecord: record });
  }
  data.observations.sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  // Chart libraries require strictly increasing times. Preserve all events in the
  // table; suppress duplicate chart times instead of inventing sub-timestamps.
  for (const series of [data.bid, data.ask, data.close, data.entries, data.exits]) {
    series.sort((a, b) => a.time - b.time);
    for (let i = series.length - 1; i > 0; i--) if (series[i].time === series[i - 1].time) { series.splice(i, 1); data.omitted++; }
  }
  return data;
}
