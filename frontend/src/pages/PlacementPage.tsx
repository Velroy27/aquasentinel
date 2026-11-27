import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { LiveMap } from '../components/LiveMap';
import { useDashboard } from '../store/useDashboard';

export function PlacementPage() {
  const placements = useDashboard((s) => s.placements);
  const network = useDashboard((s) => s.network);
  const [count, setCount] = useState<number>(8);
  const [mode, setMode] = useState<'optimized' | 'random'>('optimized');

  const available = useMemo(() => placements.map((p) => p.sensorCount).sort((a, b) => a - b), [placements]);
  const activeCount = available.includes(count) ? count : available[0] ?? 8;
  const current = placements.find((p) => p.sensorCount === activeCount);
  const sensors = current ? (mode === 'optimized' ? current.optimizedSensors : current.randomSensors) : [];

  const chartData = placements.map((p) => ({
    name: `${p.sensorCount} sensors`,
    Optimized: +(p.optimizedCoverage * 100).toFixed(1),
    Random: +(p.randomCoverage * 100).toFixed(1),
    optimizedAccuracy: +(p.optimizedAccuracy * 100).toFixed(1),
    randomAccuracy: +(p.randomAccuracy * 100).toFixed(1),
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
            <div><span>Coverage</span><strong>{((mode === 'optimized' ? current.optimizedCoverage : current.randomCoverage) * 100).toFixed(1)}%</strong></div>
            <div><span>Accuracy</span><strong>{((mode === 'optimized' ? current.optimizedAccuracy : current.randomAccuracy) * 100).toFixed(1)}%</strong></div>
            <div><span>Selected sensors</span><strong>{sensors.length}</strong></div>
          </div>
        )}
      </section>

      <LiveMap
        selectedSensor={null}
        highlightSensors={sensors.length ? sensors : network?.sensors}
        showLocalization={false}
        title={`${mode === 'optimized' ? 'Optimized' : 'Random'} placement`}
        subtitle={`${activeCount} sensors on Net3`}
      />

      <section className="panel chart-block">
        <div className="section-heading">
          <div>
            <div className="eyebrow">Coverage by sensor count</div>
            <h2>Optimized vs random</h2>
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