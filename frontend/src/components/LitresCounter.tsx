import { useEffect, useRef, useState } from 'react';
import { formatRs, currencyPerLitre, demoMode } from '../config/settings';
import { useDashboard } from '../store/useDashboard';

function useContinuousLitres(
  authoritativeLitres: number,
  leakRate: number | null,
  active: boolean,
  running: boolean,
  speed: number,
): number {
  const [display, setDisplay] = useState(authoritativeLitres);
  const syncRef = useRef({ litres: authoritativeLitres, time: performance.now() });

  // Whenever authoritative backend loss arrives, re-anchor interpolation base
  useEffect(() => {
    syncRef.current = {
      litres: authoritativeLitres,
      time: performance.now(),
    };
    if (authoritativeLitres === 0) {
      setDisplay(0);
    }
  }, [authoritativeLitres]);

  useEffect(() => {
    let frame = 0;

    function tick() {
      const now = performance.now();
      if (!running || !active || leakRate === null || leakRate <= 0) {
        setDisplay(authoritativeLitres);
        return;
      }

      const elapsedSec = Math.max(0, (now - syncRef.current.time) / 1000);
      const interpolated = syncRef.current.litres + leakRate * elapsedSec * speed;
      setDisplay(interpolated);

      frame = window.requestAnimationFrame(tick);
    }

    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [authoritativeLitres, leakRate, active, running, speed]);

  return display;
}

export function LitresCounter() {
  const litresLost = useDashboard((state) => state.litresLost);
  const anomalyAt = useDashboard((state) => state.anomalyAt);
  const alertActive = useDashboard((state) => state.alertActive);
  const leakRateEstimate = useDashboard((state) => state.leakRateEstimate);
  const running = useDashboard((state) => state.running);
  const speed = useDashboard((state) => state.speed);

  const displayedLitres = useContinuousLitres(litresLost, leakRateEstimate, alertActive, running, speed);
  const estimatedRs = displayedLitres * currencyPerLitre;
  const anomalyTime = anomalyAt
    ? new Date(anomalyAt).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })
    : '—';

  return (
    <section className="panel counter-panel" aria-label="Estimated water loss">
      <div className="counter-kicker"><span className="eyebrow">LOSS ESTIMATE</span><span className="estimate-mark">EST</span></div>
      <div className="counter-value" aria-live="polite" aria-atomic="true">
        {Math.round(displayedLitres).toLocaleString('en-IN')}<span className="counter-unit">L</span>
      </div>
      <div className="counter-caption">WATER LOST <span>·</span> {demoMode ? 'MOCK TOTAL' : 'BACKEND TOTAL'}</div>
      <div className="counter-grid">
        <div className="counter-stat"><span>Value at risk</span><strong>{formatRs(estimatedRs)}</strong></div>
        <div className="counter-stat"><span>Leak rate</span><strong>{alertActive && leakRateEstimate !== null ? `${leakRateEstimate.toFixed(1)} L/s` : '—'}</strong></div>
        <div className="counter-stat"><span>First anomaly</span><strong>{anomalyTime} IST</strong></div>
      </div>
      <div className="counter-footnote">Rs {currencyPerLitre.toFixed(3)} / L ASSUMED</div>
    </section>
  );
}
