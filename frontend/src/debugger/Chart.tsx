import { useEffect, useRef } from 'react';
import { ColorType, createChart, LineSeries, type IChartApi, type UTCTimestamp, type Time } from 'lightweight-charts';
import type { DebugData, DebugEvent } from './data';

export function Chart({ data, selected, choose }: { data: DebugData; selected?: DebugEvent; choose: (event: DebugEvent) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>(undefined);
  useEffect(() => {
    const instance = createChart(container.current!, {
      autoSize: true, height: 420,
      layout: { background: { type: ColorType.Solid, color: '#111a27' }, textColor: '#a3b5ce', attributionLogo: true },
      grid: { vertLines: { color: '#1b2935' }, horzLines: { color: '#1b2935' } },
      timeScale: { timeVisible: true, secondsVisible: true },
      localization: { timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().replace('T', ' ').replace('Z', ' UTC') },
    });
    chart.current = instance;
    const sources = [{ points: data.bid, title: 'Observed bid', color: '#51c8b5' }, { points: data.ask, title: 'Observed ask', color: '#728af8' },
      { points: data.close, title: 'Recorded bar close', color: '#98a9b7' }, { points: data.entries, title: 'Entry fills', color: '#7bdf99' }, { points: data.exits, title: 'Exit fills', color: '#f2b66b' }];
    const plotted = sources.map((source, index) => {
      const series = instance.addSeries(LineSeries, { color: source.color, title: source.title, lineWidth: 2, lineVisible: index < 3, pointMarkersVisible: true, pointMarkersRadius: index >= 3 ? 6 : 3, priceLineVisible: false, lastValueVisible: false });
      series.setData(source.points.map(point => ({ time: point.time as UTCTimestamp, value: point.value })));
      return { series, points: source.points };
    });
    instance.timeScale().fitContent();
    instance.subscribeClick(params => {
      if (typeof params.time !== 'number' || !params.point) return;
      // Hit-test recorded points, preserving their source identity. Coincident
      // fill markers take precedence over price observations, never over time alone.
      let nearest: DebugEvent | undefined; let distance = 12;
      for (const source of [...plotted].reverse()) {
        const point = source.points.find(point => point.time === params.time);
        if (!point) continue;
        const y = source.series.priceToCoordinate(point.value);
        if (y === null || Math.abs(y - params.point.y) >= distance) continue;
        const event = data.events.find(event => event.id === point.sourceId) ?? data.events.find(event =>
          event.id === event.sessionRecord.record_id && event.sessionRecord.market_event?.event_id === point.sourceId);
        if (event) { nearest = event; distance = Math.abs(y - params.point.y); }
      }
      if (nearest) choose(nearest);
    });
    return () => { chart.current = undefined; instance.remove(); };
  }, [data, choose]);
  useEffect(() => {
    if (!selected || !chart.current) return;
    const time = Date.parse(selected.time) / 1000;
    chart.current.timeScale().setVisibleRange({ from: (time - 90) as UTCTimestamp, to: (time + 90) as UTCTimestamp });
  }, [selected]);
  function zoom(factor: number) {
    const scale = chart.current?.timeScale(); const range = scale?.getVisibleLogicalRange();
    if (scale && range) { const middle = (range.from + range.to) / 2; const half = (range.to - range.from) * factor / 2; scale.setVisibleLogicalRange({ from: middle - half, to: middle + half }); }
  }
  function pan(direction: number) {
    const scale = chart.current?.timeScale(); const range = scale?.getVisibleLogicalRange();
    if (scale && range) { const shift = (range.to - range.from) * .3 * direction; scale.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift }); }
  }
  return <><div className="chart-controls"><button onClick={() => zoom(.7)} aria-label="Zoom in">＋</button><button onClick={() => zoom(1.4)} aria-label="Zoom out">−</button><button onClick={() => pan(-1)} aria-label="Pan earlier">←</button><button onClick={() => pan(1)} aria-label="Pan later">→</button><button onClick={() => chart.current?.timeScale().fitContent()}>Fit all</button></div>
    <div ref={container} className="chart" role="img" aria-label="Historical observed prices and recorded fill prices. Exact values are available in the accessible data table." />
    <div className="legend"><span className="bid">● Observed bid</span><span className="ask">● Observed ask</span><span>● Recorded bar close</span><span className="entry">● Entry fills</span><span className="exit">● Exit fills</span></div></>;
}
