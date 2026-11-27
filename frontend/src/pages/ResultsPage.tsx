import { useDashboard } from '../store/useDashboard';

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card panel">
      <div className="eyebrow">{label}</div>
      <div className="metric-value">{value}</div>
      <p>{detail}</p>
    </article>
  );
}

export function ResultsPage() {
  const results = useDashboard((s) => s.results);
  const loading = useDashboard((s) => s.loading);

  if (loading) return <div className="panel map-shell skeleton">Loading results…</div>;
  if (!results) return <div className="panel map-shell error-state">Results unavailable.</div>;

  return (
    <div className="page-grid results-grid">
      <Metric label="Detection rate" value={`${(results.detectionRate * 100).toFixed(1)}%`} detail="Share of simulated leaks detected." />
      <Metric label="False alarms / day" value={results.falseAlarmsPerDay.toFixed(1)} detail="Average false positives per day." />
      <Metric label="Top-1 accuracy" value={`${(results.top1 * 100).toFixed(1)}%`} detail="Leak node ranked first." />
      <Metric label="Top-3 accuracy" value={`${(results.top3 * 100).toFixed(1)}%`} detail="True leak included in top three." />
      <Metric label="Neighbour accuracy" value={`${(results.neighbourAccuracy * 100).toFixed(1)}%`} detail="Prediction within one hop of leak." />
      <Metric label="Chance baseline" value={`${(results.chanceBaseline * 100).toFixed(2)}%`} detail="Expected random top-1 accuracy." />
      <section className="panel results-note">
        <div className="eyebrow">Measured</div>
        <h2>Evaluation snapshot</h2>
        <p>Results loaded from <code>/api/results</code>. Measured on {new Date(results.measuredAt).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })}.</p>
      </section>
    </div>
  );
}