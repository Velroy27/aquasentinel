import { useEffect, useMemo, useRef, useState } from 'react';
import { selectCurrentScenario, useDashboard } from '../store/useDashboard';
import { demoMode } from '../config/settings';
import { ConnectionPill } from './ConnectionPill';

function useMessageRate(): number {
  const readings = useDashboard((state) => state.pressuresBySensor);
  const signature = useMemo(
    () => Object.entries(readings).map(([id, points]) => `${id}:${points[points.length - 1]?.timestamp ?? ''}`).join('|'),
    [readings],
  );
  const observedSignature = useRef(signature);
  const events = useRef<number[]>([]);
  const [rate, setRate] = useState(0);

  useEffect(() => {
    if (signature !== observedSignature.current) {
      events.current.push(performance.now());
      observedSignature.current = signature;
    }
  }, [signature]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const cutoff = performance.now() - 1_000;
      events.current = events.current.filter((timestamp) => timestamp >= cutoff);
      setRate(events.current.length);
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  return rate;
}

export function Header() {
  const stubBackend = useDashboard((state) => state.stubBackend);
  const anomalyAt = useDashboard((state) => state.anomalyAt);
  const scenario = useDashboard(selectCurrentScenario);
  const pressuresBySensor = useDashboard((state) => state.pressuresBySensor);
  const running = useDashboard((state) => state.running);
  const rate = useMessageRate();
  const [wallClock, setWallClock] = useState(() => new Date());
  const newestTimestamp = Object.values(pressuresBySensor)
    .flatMap((points) => points.slice(-1))
    .map((point) => point.timestamp)
    .sort()
    .slice(-1)[0];
  const simulationTime = newestTimestamp
    ? new Date(newestTimestamp).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })
    : '—';

  useEffect(() => {
    const timer = window.setInterval(() => setWallClock(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/" aria-label="AquaSentinel home">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 40 40" fill="none">
              <path d="M20 3.5 35 9v10.2c0 8.8-6 14.5-15 17.3-9-2.8-15-8.5-15-17.3V9l15-5.5Z" stroke="currentColor" strokeWidth="1.1" />
              <path d="M20 10c-3.8 5-7.1 8.5-7.1 12a7.1 7.1 0 0 0 14.2 0c0-3.5-3.3-7-7.1-12Z" stroke="currentColor" strokeWidth="1.35" />
            </svg>
          </span>
          <span><b>AquaSentinel</b><small>NETWORK CONTROL / NET3</small></span>
        </a>
        <div className="header-right">
          <span className="network-name">{scenario?.name ?? 'Net3'}</span>
          <ConnectionPill />
          <span className="wall-clock">{wallClock.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST</span>
        </div>
      </header>
      <div className="system-status-bar" role="status" aria-live="polite">
        <span><i className={`status-led ${demoMode ? 'teal' : ''}`} />{demoMode ? 'MOCK DATA' : 'LIVE DATA'}</span>
        <span><i className={`status-led ${running ? 'teal pulse' : anomalyAt ? 'red' : ''}`} />{running ? 'SIM RUNNING' : anomalyAt ? 'ANOMALY' : 'SIM STOPPED'}</span>
        {stubBackend && <span className="stub-backend"><i className="status-led amber" />STUB BACKEND</span>}
        <span>SIM <b className="numeric">{simulationTime}</b></span>
        <span>SCENARIO <b>{scenario?.name ?? '—'}</b></span>
        <span>RATE <b className="numeric">{rate.toFixed(0)} msg/s</b></span>
        <span className="utc-label">UTC+05:30</span>
      </div>
    </>
  );
}
