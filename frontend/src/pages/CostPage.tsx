import { useEffect, useMemo, useState } from 'react';
import { costPerNode, formatRs, lossAvoided } from '../config/settings';
import { useDashboard } from '../store/useDashboard';
import { ResourceNotice } from '../components/ResourceNotice';

export function CostPage() {
  const results = useDashboard((s) => s.results);
  const available = (results?.placement ?? []).map((p) => p.sensorCount).sort((a, b) => a - b);
  const min = available[0] ?? 1;
  const max = available[available.length - 1] ?? 1;
  const initial = available.includes(8) ? 8 : min;
  const [nodes, setNodes] = useState(initial);
  useEffect(() => { if (available.length && !available.includes(nodes)) setNodes(initial); }, [available.join(','), initial, nodes]);
  const selectedCount = available.reduce((nearest, value) => Math.abs(value - nodes) < Math.abs(nearest - nodes) ? value : nearest, available[0] ?? nodes);
  const selectedIndex = Math.max(0, available.indexOf(selectedCount));

  const cost = selectedCount * costPerNode;
  const avoided = useMemo(() => lossAvoided(selectedCount), [selectedCount]);
  const paybackDays = avoided > 0 ? Math.ceil((cost / avoided) * 365) : null;

  return (
    <ResourceNotice resource="results"><div className="page-grid cost-grid">
      <section className="panel cost-panel">
        <div className="section-heading">
          <div>
            <div className="eyebrow">Investment</div>
            <h2>Sensor deployment cost</h2>
          </div>
          <span className="unit-chip">{formatRs(costPerNode)} / node</span>
        </div>
        {available.length ? <label className="slider-block">
          <div className="slider-head"><span>Sensor count</span><strong>{selectedCount} sensors</strong></div>
          <input type="range" min={0} max={Math.max(0, available.length - 1)} value={selectedIndex} step="1" disabled={available.length < 2} onChange={(e) => setNodes(available[Number(e.target.value)])} aria-label="Sensor count" />
          <div className="slider-scale"><span>{min}</span><span>{max}</span></div>
        </label> : <p className="empty-inline">No placement sensor counts returned.</p>}
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
    </div></ResourceNotice>
  );
}
