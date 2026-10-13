import { useId, useMemo, useState } from 'react';
import { chartPoint, type Point } from './debugger/data';
import type { PaperReport } from './api/client';
import { Empty } from './components';
import { decimal, utc } from './format';

export type RecordPoint = Point & { timestamp?: string };
export function accountPoints(report: PaperReport): RecordPoint[] {
  return report.snapshot.records.flatMap(record => {
    const point = chartPoint(record.account.equity, record.timestamp, record.record_id);
    return point ? [{ ...point, timestamp: record.timestamp }] : [];
  });
}
/** Approximate SVG coordinates only. Readouts always use the original strings.
 * Every point comes from an actual recorded mark; connecting lines are guides. */
export function RecordChart({ points, label, unit = '' }: { points: RecordPoint[]; label: string; unit?: string }) {
  const gradient = useId().replaceAll(':', '');
  const [range, setRange] = useState(0);
  const [selection, setSelection] = useState<string>();
  const shown = useMemo(() => range ? points.slice(-range) : points, [points, range]);
  const active = shown.find(point => point.sourceId === selection) ?? shown.at(-1);
  if (!active) return <Empty>No recorded equity history is available. A registered paper session or retained result with account marks is required.</Empty>;
  const minTime = Math.min(...shown.map(point => point.time)), maxTime = Math.max(...shown.map(point => point.time));
  const low = Math.min(...shown.map(point => point.value)), high = Math.max(...shown.map(point => point.value));
  const padding = (high - low || Math.abs(high) * .0001 || 1) * .15;
  const lower = low - padding, upper = high + padding;
  const position = (point: Point) => ({ x: 20 + (point.time - minTime) / (maxTime - minTime || 1) * 820, y: 280 - (point.value - lower) / (upper - lower) * 250 });
  const coordinates = shown.map(position);
  const line = coordinates.map((point, index) => `${index ? 'L' : 'M'}${point.x},${point.y}`).join(' ');
  const selected = position(active);
  return <div className="record-chart">
    <div className="chart-toolbar"><div><span className="chart-name">{label}</span><strong>{decimal(active.exact)} <small>{unit}</small></strong><small>{utc(active.timestamp ?? new Date(active.time * 1000).toISOString())} · recorded mark</small></div>
      <div className="segmented" aria-label="Recorded chart window">{[[0, 'All records'], [20, 'Last 20'], [10, 'Last 10']].map(([count, title]) => <button key={count} aria-pressed={range === count} onClick={() => setRange(count as number)}>{title}</button>)}</div></div>
    <svg className="equity-canvas" viewBox="0 0 960 325" role="img" aria-label={`${label}. Approximate chart of ${shown.length} recorded marks; exact values available using the record selector.`}>
      <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4d91ff" stopOpacity=".26" /><stop offset="100%" stopColor="#4d91ff" stopOpacity="0" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map(index => { const y = 30 + index * 62.5; return <g key={index}><line x1="20" y1={y} x2="850" y2={y} stroke="#26313f" strokeDasharray="3 5" /><text x="865" y={y + 4}>{(upper - index / 4 * (upper - lower)).toFixed(2)}</text></g>; })}
      {[0, 1, 2, 3, 4].map(index => { const time = minTime + (maxTime - minTime) * index / 4; return <g key={index}><line x1={20 + index * 205} y1="30" x2={20 + index * 205} y2="280" stroke="#202b38" /><text x={20 + index * 205} y="310" textAnchor={index === 0 ? 'start' : index === 4 ? 'end' : 'middle'}>{new Date(time * 1000).toISOString().slice(11, 19)}</text></g>; })}
      <path d={`${line} L${coordinates.at(-1)!.x},280 L${coordinates[0].x},280 Z`} fill={`url(#${gradient})`} />
      <path d={line} fill="none" stroke="#619eff" strokeWidth="2.4" vectorEffect="non-scaling-stroke" />
      <line x1={selected.x} y1="30" x2={selected.x} y2="280" stroke="#718399" strokeDasharray="4 4" />
      {shown.map((point, index) => <circle key={`${point.sourceId}-${index}`} cx={coordinates[index].x} cy={coordinates[index].y} r={point === active ? 5 : 3} fill={point === active ? '#d5e7ff' : '#619eff'} onMouseEnter={() => setSelection(point.sourceId)} onClick={() => setSelection(point.sourceId)}><title>{decimal(point.exact)} · {utc(point.timestamp ?? new Date(point.time * 1000).toISOString())}</title></circle>)}
    </svg>
    <div className="chart-footnote"><span>UTC · {shown.length} recorded marks · approximate axes</span><label>Exact mark<select aria-label={`${label} exact mark`} value={shown.indexOf(active)} onChange={event => setSelection(shown[Number(event.target.value)].sourceId)}>{shown.map((point, index) => <option key={index} value={index}>#{index + 1} · {decimal(point.exact)} · {new Date(point.time * 1000).toISOString().slice(11, 19)} UTC</option>)}</select></label></div>
    <p className="chart-disclaimer">Historical recorded account marks. Connecting lines are visual guides; no intrabar path or current valuation is implied.</p>
  </div>;
}
