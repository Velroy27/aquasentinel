import { useEffect, useRef, useState } from 'react';
import { formatRs, currencyPerLitre } from '../config/settings';
import { selectCurrentScenario, useDashboard } from '../store/useDashboard';

function useCountUp(value: number): number {
  const [display, setDisplay] = useState(value);
  const current = useRef(value);

  useEffect(() => {
    const from = current.current;
    const start = performance.now();
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const duration = reduceMotion ? 0 : 520;
    let frame = 0;

    function tick(now: number) {
      const progress = duration === 0 ? 1 : Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const next = from + (value - from) * eased;
      current.current = next;
      setDisplay(next);
      if (progress < 1) frame = window.requestAnimationFrame(tick);
    }

    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [value]);

  return display;
}

export function LitresCounter() {
  const litresLost = useDashboard((state) => state.litresLost);
  const anomalyAt = useDashboard((state) => state.anomalyAt);
  const scenario = useDashboard(selectCurrentScenario);
  const displayedLitres = useCountUp(litresLost);
  const estimatedRs = litresLost * currencyPerLitre;
  const anomalyTime = anomalyAt
    ? new Date(anomalyAt).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })
    : '—';

  return (
    <section className="panel counter-panel" aria-label="Estimated water loss">
      <div className="counter-kicker"><span className="eyebrow">LOSS ESTIMATE</span><span className="estimate-mark">EST</span></div>
      <div className="counter-value" aria-live="polite" aria-atomic="true">
        {Math.round(displayedLitres).toLocaleString('en-IN')}<span className="counter-unit">L</span>
      </div>
      <div className="counter-caption">WATER LOST <span>·</span> MOCK ESTIMATE</div>
      <div className="counter-grid">
        <div className="counter-stat"><span>Value at risk</span><strong>{formatRs(estimatedRs)}</strong></div>
        <div className="counter-stat"><span>Leak rate</span><strong>{scenario ? `${scenario.leakLitresPerSecond.toFixed(1)} L/s` : '—'}</strong></div>
        <div className="counter-stat"><span>First anomaly</span><strong>{anomalyTime} IST</strong></div>
      </div>
      <div className="counter-footnote">Rs {currencyPerLitre.toFixed(3)} / L ASSUMED</div>
    </section>
  );
}
