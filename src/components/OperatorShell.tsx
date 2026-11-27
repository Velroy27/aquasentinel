import { BarChart3, CircleDollarSign, Map, Waypoints } from 'lucide-react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
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
    ? `${latest.severity.toUpperCase()} · ${new Date(latest.timestamp).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST · ${latest.sensorId} · SCORE ${(latest.score * 100).toFixed(0)}%`
    : anomalyAt
      ? `ANOMALY · ${new Date(anomalyAt).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST`
      : 'EVENT LOG · NO EVENTS';

  return <div className="event-ticker"><span className="ticker-label">EVENT</span><span className="ticker-value">{event}</span><span className="ticker-hint">? SHORTCUTS</span></div>;
}

export function OperatorShell() {
  const loading = useDashboard((state) => state.loading);
  const error = useDashboard((state) => state.error);
  const location = useLocation();
  const pageTitle = pageTitles[location.pathname] ?? pageTitles['/'];

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
    </main>
  );
}
