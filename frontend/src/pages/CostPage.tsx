import { useMemo, useState } from 'react';
import { costPerNode, formatRs, lossAvoided } from '../config/settings';
import { useDashboard } from '../store/useDashboard';

export function CostPage() {
  const network = useDashboard((s) => s.network);
  const placements = useDashboard((s) => s.placements);
  const max = Math.max(20, network?.junctions.length ?? 20);
  const initial = placements.find((p) => p.sensorCount === 8)?.sensorCount ?? 8;
  const [nodes, setNodes] = useState(initial);

  const cost = nodes * costPerNode;
  const avoided = useMemo(() => lossAvoided(nodes), [nodes]);
  const paybackDays = avoided > 0 ? Math.ceil((cost / avoided) * 365) : null;

  return (
    <div className="page-grid cost-grid">
      <section className="panel cost-panel">
        <div className="section-heading">
          <div>
            <div className="eyebrow">Investment</div>
            <h2>Sensor deployment cost</h2>
          </div>
          <span className="unit-chip">{formatRs(costPerNode)} / node</span>
        </div>
        <label className="slider-block">
          <div className="slider-head"><span>Node count</span><strong>{nodes} nodes</strong></div>
          <input type="range" min={1} max={max} value={nodes} onChange={(e) => setNodes(Number(e.target.value))} aria-label="Sensor node count" />
          <div className="slider-scale"><span>1</span><span>{max}</span></div>
        </label>
        <div className="metric-list">
          <div><span>Total sensor cost</span><strong>{formatRs(cost)}</strong></div>
          <div><span>Estimated loss avoided / year</span><strong>{formatRs(avoided)}</strong></div>
          <div><span>Simple payback</span><strong>{paybackDays ? `${paybackDays} days` : '—'}</strong></div>
        </div>
      </section>

      <section className="panel cost-summary">
        <div className="eyebrow">Assumptions</div>
        <h2>Planning estimate</h2>
        <p>The loss avoided estimate uses the planning defaults in the app configuration: Rs 0.012 per litre, 18,000 litres/day leak volume, 72% avoided after detection, and a 365-day year. Replace these with validated tariff and engineering assumptions before using outside the demo.</p>
        <div className="cost-callout">
          <div className="cost-callout-value">{formatRs(avoided - cost)}</div>
          <div className="cost-callout-label">Estimated year-one net benefit</div>
        </div>
      </section>
    </div>
  );
}