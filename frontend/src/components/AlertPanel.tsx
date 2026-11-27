import { AlertTriangle, BellRing } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useDashboard } from '../store/useDashboard';

export function AlertPanel() {
  const alerts = useDashboard((state) => state.alerts);
  const [newId, setNewId] = useState<string | null>(null);
  const seen = useRef<string | null>(null);

  useEffect(() => {
    const newestId = alerts[0]?.id;
    if (!newestId || seen.current === newestId) return;
    seen.current = newestId;
    setNewId(newestId);
    const timer = window.setTimeout(() => setNewId(null), 3_000);
    return () => window.clearTimeout(timer);
  }, [alerts]);

  return (
    <section className="panel alert-panel" aria-label="Detection event log">
      <div className="event-log-head">
        <div><div className="eyebrow">EVENT LOG</div><h2>Detector alerts</h2></div>
        <span className="event-count">{String(alerts.length).padStart(2, '0')}</span>
      </div>
      <div className="alert-list" aria-live="polite">
        {alerts.length === 0 ? <div className="empty-events">NO EVENTS</div> : alerts.map((alert) => {
          const isNew = newId === alert.id;
          return (
            <button
              type="button"
              key={alert.id}
              className={`alert-item severity-${alert.severity} ${isNew ? 'flash' : ''}`}
              onClick={() => { if (isNew) setNewId(null); }}
              aria-label={`${alert.severity} alert at ${alert.timestamp} from ${alert.sensorId}, score ${(alert.score * 100).toFixed(0)} percent`}
            >
              <span className="alert-icon">{alert.severity === 'critical' ? <AlertTriangle size={17} /> : <BellRing size={17} />}</span>
              <span className="alert-body">
                <span className="alert-top"><strong>{alert.severity.toUpperCase()}</strong><time>{new Date(alert.timestamp).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST</time></span>
                <span className="alert-message">{alert.message}</span>
                <span className="alert-meta"><span>{alert.sensorId}</span><span>MODEL SCORE {alert.score.toFixed(2)}</span></span>
              </span>
              {isNew && <span className="new-indicator">NEW</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
