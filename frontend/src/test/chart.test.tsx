import { expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { debugData } from '../debugger/data';
import { fixtures } from './helpers';
const fake = vi.hoisted(() => {
  const setData = vi.fn(); const setVisibleLogicalRange = vi.fn(); const setVisibleRange = vi.fn(); const fitContent = vi.fn(); const remove = vi.fn(); const subscribeClick = vi.fn();
  const timeScale = { getVisibleLogicalRange: () => ({ from: 0, to: 10 }), setVisibleLogicalRange, setVisibleRange, fitContent };
  const chart = { addSeries: vi.fn(() => ({ setData, priceToCoordinate: (price: number) => price })), timeScale: () => timeScale, subscribeClick, remove };
  return { chart, setData, setVisibleLogicalRange, setVisibleRange, fitContent, remove, subscribeClick };
});
vi.mock('lightweight-charts', () => ({ ColorType: { Solid: 'solid' }, LineSeries: 'line', createChart: () => fake.chart }));
import { Chart } from '../debugger/Chart';
beforeEach(() => vi.clearAllMocks());
it('provides keyboard-accessible zoom, pan and reset with cleanup', async () => {
  const { unmount } = render(<Chart data={debugData(fixtures.paper)} choose={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
  expect(fake.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 1.5, to: 8.5 });
  await userEvent.click(screen.getByRole('button', { name: 'Pan earlier' }));
  expect(fake.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -3, to: 7 });
  await userEvent.click(screen.getByRole('button', { name: 'Fit all' }));
  expect(fake.fitContent).toHaveBeenCalledTimes(2);
  unmount(); expect(fake.remove).toHaveBeenCalledTimes(1);
});
it('cannot select fabricated events at an unrecorded chart time', () => {
  const choose = vi.fn(); const data = debugData(fixtures.paper);
  render(<Chart data={data} choose={choose} />);
  const click = fake.subscribeClick.mock.calls[0][0];
  const point = data.bid[0];
  click({ time: point.time - 500, point: { y: point.value } }); expect(choose).not.toHaveBeenCalled();
  click({ time: point.time, point: { y: point.value + 1000 } }); expect(choose).not.toHaveBeenCalled();
  click({ time: point.time, point: { y: point.value } });
  expect(choose).toHaveBeenCalledWith(data.events.find(event => event.id === event.sessionRecord.record_id && event.sessionRecord.market_event?.event_id === point.sourceId));
});
it('centers selected event time without constructing a price marker', () => {
  const data = debugData(fixtures.paper); const selected = data.events[0];
  render(<Chart data={data} selected={selected} choose={vi.fn()} />);
  const time = Date.parse(selected.time) / 1000;
  expect(fake.setVisibleRange).toHaveBeenCalledWith({ from: time - 90, to: time + 90 });
  expect(fake.chart.addSeries).toHaveBeenCalledTimes(5);
  expect(fake.setData.mock.calls.flatMap(call => call[0])).toHaveLength(data.bid.length + data.ask.length + data.close.length + data.entries.length + data.exits.length);
});
it('selects the actual fill identity when events share a chart timestamp', () => {
  const data = debugData(fixtures.paper); const fill = data.entries[0]; const choose = vi.fn();
  render(<Chart data={data} choose={choose} />);
  fake.subscribeClick.mock.calls[0][0]({ time: fill.time, point: { x: 100, y: fill.value } });
  expect(choose).toHaveBeenCalledWith(data.events.find(event => event.id === fill.sourceId));
});
