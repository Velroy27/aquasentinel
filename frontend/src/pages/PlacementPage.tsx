import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { LiveMap } from '../components/LiveMap';
import { useDashboard } from '../store/useDashboard';

export function PlacementPage() {
  const placements = useDashboard((s) => s.placements);
  const results = useDashboard((s) => s.results);
  const placementStatus = useDashboard((s) => s.resources.placement);
  const retryResource = useDashboard((s) => s.retryResource);
  const [count, setCount] = useState<number>(8);
  const [mode, setMode] = useState<'optimized' | 'random'>('optimized');

  const available = useMemo(() => (results?.placement ?? []).map((p) => p.sensorCount).sort((a, b) => a - b), [results]);
  const activeCount = available.includes(count) ? count : available[0] ?? 8;
  const current = placements.find((p) => p.sensorCount === activeCount);
  const sensors = current ? (mode === 'optimized' ? current.optimizedSensors : current.randomSensors ?? []) : [];

  const chartData = (results?.placement ?? []).map((p) => ({
    name: `${p.sensorCount} sensors`,
    Optimized: +(p.optimized * 100).toFixed(1),
    Random: +(p.randomMean * 100).toFixed(1),
  }));

  return (
    <div className="page-grid">
      <section className="panel controls-panel">
        <div className="section-heading">
          <div>
            <div className="eyebrow">Comparison</div>
            <h2>Placement strategy</h2>
          </div>
        </div>
        <div className="segmented">
          {available.map((value) => (
            <button key={value} type="button" className={activeCount === value ? 'selected' : ''} onClick={() => setCount(value)}>{value} sensors</button>
          ))}
        </div>
        <div className="segmented">
          <button type="button" className={mode === 'optimized' ? 'selected' : ''} onClick={() => setMode('optimized')}>Optimized</button>
          <button type="button" className={mode === 'random' ? 'selected' : ''} onClick={() => setMode('random')}>Random</button>
        </div>
        {current && (
          <div className="metric-list">
            <div><span>Placement API coverage</span><strong>{mode === 'random' || current.optimizedCoverage === null ? '—' : `${(current.optimizedCoverage * 100).toFixed(1)}%`}</strong></div>
            <div><span>Selected sensors</span><strong>{sensors.length}</strong></div>
          </div>
        )}
        {mode === 'random' && current?.randomSensors === null && <p className="empty-inline">Random sensor IDs are not provided by the backend.</p>}
        {placementStatus.loading && <p className="empty-inline">Loading optimized sensor IDs…</p>}
        {placementStatus.error && <p className="empty-inline" role="alert">Placement data unavailable <button type="button" onClick={() => void retryResource('placement')}>Retry</button></p>}
        {mode === 'optimized' && !current && !placementStatus.loading && !placementStatus.error && <p className="empty-inline">Optimized sensor IDs are unavailable for this count.</p>}
        {!results?.placement.length && <p className="empty-inline">No benchmark sensor counts returned.</p>}
      </section>

      <LiveMap
        selectedSensor={null}
        highlightSensors={sensors}
        showLocalization={false}
        emptyNotice={mode === 'random' && current?.randomSensors === null ? 'RANDOM SENSOR IDS NOT PROVIDED' : undefined}
        title={`${mode === 'optimized' ? 'Optimized' : 'Random'} placement`}
        subtitle={`${activeCount} sensors on Net3`}
      />

      <section className="panel chart-block">
        <div className="section-heading">
          <div>
            <div className="eyebrow">Results endpoint</div>
            <h2>Benchmark coverage</h2>
          </div>
          <span className="unit-chip">%</span>
        </div>
        <div className="chart-wrap tall">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#27383d" strokeDasharray="3 4" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: '#93a5a8', fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fill: '#93a5a8', fontSize: 12 }} axisLine={false} tickLine={false} width={42} />
              <Tooltip contentStyle={{ background: '#132024', border: '1px solid #355056', color: '#ecf3f0' }} />
              <Bar dataKey="Optimized" fill="#48c7b3" radius={[3, 3, 0, 0]} />
              <Bar dataKey="Random" fill="#7a8f94" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}
