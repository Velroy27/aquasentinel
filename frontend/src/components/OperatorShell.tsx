import { useEffect } from 'react';
import { BarChart3, CircleDollarSign, Map, Waypoints } from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { demoMode } from '../config/settings';
import { ShortcutOverlay } from '../pages/HomePage';
import { useDashboard } from '../store/useDashboard';
import { ControlBar } from './ControlBar';
import { Header } from './Header';

const navigation = [
  { to: '/', label: 'Live map', icon: Map },
  { to: '/placement', label: 'Placement', icon: Waypoints },
  { to: '/results', label: 'Results', icon: BarChart3 },
  { to: '/cost', label: 'Cost', icon: CircleDollarSign },
];

const pageTitles: Record<string, string> = {
  '/': 'Network operations',
  '/placement': 'Sensor placement',
  '/results': 'Detection performance',
  '/cost': 'Deployment economics',
};

function EventTicker() {
  const alerts = useDashboard((state) => state.alerts);
  const anomalyAt = useDashboard((state) => state.anomalyAt);
  const latest = alerts[0];
  const event = latest
    ? `${latest.severity.toUpperCase()} · ${new Date(latest.timestamp).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST · ${latest.sensorId} · MODEL SCORE ${latest.score.toFixed(2)}`
    : anomalyAt
      ? `ANOMALY · ${new Date(anomalyAt).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST`
      : 'EVENT LOG · NO EVENTS';

  return <div className="event-ticker"><span className="ticker-label">EVENT</span><span className="ticker-value">{event}</span><span className="ticker-hint">? SHORTCUTS</span></div>;
}

export function OperatorShell() {
  const loading = useDashboard((state) => state.loading);
  const error = useDashboard((state) => state.error);
  const location = useLocation();
  const running = useDashboard((state) => state.running);
  const network = useDashboard((state) => state.network);
  const selectedSensor = useDashboard((state) => state.selectedSensor);
  const start = useDashboard((state) => state.start);
  const stop = useDashboard((state) => state.stop);
  const reset = useDashboard((state) => state.reset);
  const setSpeed = useDashboard((state) => state.setSpeed);
  const setSelectedSensor = useDashboard((state) => state.setSelectedSensor);
  const setHeatmapEnabled = useDashboard((state) => state.setHeatmapEnabled);
  const setRevealLeak = useDashboard((state) => state.setRevealLeak);
  const heatmapEnabled = useDashboard((state) => state.heatmapEnabled);
  const revealLeak = useDashboard((state) => state.revealLeak);
  const shortcutsOpen = useDashboard((state) => state.shortcutsOpen);
  const setShortcutsOpen = useDashboard((state) => state.setShortcutsOpen);
  const pageTitle = pageTitles[location.pathname] ?? pageTitles['/'];

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      const editable = target instanceof HTMLElement && (/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(target.tagName) || target.isContentEditable);
      if (editable && event.key !== 'Escape' && event.key !== '?') return;
      const key = event.key.toLowerCase();
      if (event.key === '?') setShortcutsOpen(true);
      else if (event.key === 'Escape') { setShortcutsOpen(false); setSelectedSensor(''); }
      else if (event.code === 'Space') { event.preventDefault(); if (running) stop(); else start(); }
      else if (key === 'r') { event.preventDefault(); reset(); }
      else if (key === '1') setSpeed(1);
      else if (key === '2') setSpeed(5);
      else if (key === '3') setSpeed(20);
      else if (key === 'g') setHeatmapEnabled(!heatmapEnabled);
      else if (key === 'l' && demoMode) setRevealLeak(!revealLeak);
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        const sensors = network?.sensors ?? [];
        if (!sensors.length) return;
        const current = sensors.indexOf(selectedSensor ?? '');
        const next = current < 0
          ? (event.key === 'ArrowRight' ? 0 : sensors.length - 1)
          : (current + (event.key === 'ArrowRight' ? 1 : sensors.length - 1)) % sensors.length;
        setSelectedSensor(sensors[next]);
        event.preventDefault();
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [heatmapEnabled, network, reset, revealLeak, running, selectedSensor, setHeatmapEnabled, setRevealLeak, setSelectedSensor, setShortcutsOpen, setSpeed, start, stop]);

  return (
    <main className="app-shell">
      <Header />
      <nav className="primary-nav" aria-label="Primary navigation">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'}><Icon size={18} />{label}</NavLink>
        ))}
      </nav>
      <div className="page-heading">
        <div className="eyebrow">CONTROL ROOM / {location.pathname === '/' ? 'LIVE NETWORK' : pageTitle.toUpperCase()}</div>
        <h1>{pageTitle}</h1>
        <span className={`page-state ${error ? 'is-error' : ''}`}>{loading ? 'LOADING' : error ? 'DATA ERROR' : 'NET3'}</span>
      </div>
      <div className="workspace"><div className="main-content"><Outlet /></div></div>
      <ControlBar />
      <EventTicker />
      <button type="button" className="shortcut-trigger" onClick={() => setShortcutsOpen(true)} aria-label="Show keyboard shortcuts">?</button>
      {shortcutsOpen && <ShortcutOverlay onClose={() => setShortcutsOpen(false)} />}
    </main>
  );
}
