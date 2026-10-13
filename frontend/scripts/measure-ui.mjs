// Repeatable CPU observations, not a claim of browser frame-rate or live data.
import { readFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { createServer } from 'vite';
import React from 'react';
import { renderToString } from 'react-dom/server';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { debugData } = await server.ssrLoadModule('/src/debugger/data.ts');
  const { Visualization } = await server.ssrLoadModule('/src/pages/Debugger.tsx');
  const fixture = JSON.parse(readFileSync('src/debugger/offline.json', 'utf8')).paper;
  for (const size of [fixture.snapshot.records.length, 2001]) {
    const report = structuredClone(fixture);
    if (size === 2001) report.snapshot.records = Array.from({ length: size }, (_, index) => ({
      ...fixture.snapshot.records[index % fixture.snapshot.records.length], record_id: `cpu-fixture-${index}`, sequence: index + 1,
    }));
    const transform = [], render = [];
    let markup = '';
    for (let sample = 0; sample < 6; sample++) {
      let start = performance.now(); debugData(report); const transformed = performance.now() - start;
      start = performance.now(); markup = renderToString(React.createElement(Visualization, { report })); const rendered = performance.now() - start;
      if (sample > 0) { transform.push(transformed); render.push(rendered); }
    }
    const median = items => [...items].sort((a, b) => a - b)[Math.floor(items.length / 2)].toFixed(2);
    console.log(`${size} source records: transform median ${median(transform)} ms; bounded SSR render median ${median(render)} ms; HTML ${(Buffer.byteLength(markup) / 1024).toFixed(1)} KiB`);
  }
  console.log('Warm medians of five samples. 2001-record input repeats actual fixture records only for CPU scaling. SSR omits canvas/effects/network; browser responsiveness remains unmeasured.');
} finally { await server.close(); }
