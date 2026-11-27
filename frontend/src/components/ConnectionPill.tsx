import { demoMode } from '../config/settings';
import { useDashboard } from '../store/useDashboard';

export function ConnectionPill() {
  const status = useDashboard((s) => s.streamStatus);
  const label = demoMode ? 'Demo data' : status === 'live' ? 'Live feed' : status === 'connecting' ? 'Connecting' : status === 'disconnected' ? 'Disconnected' : status === 'error' ? 'Feed error' : 'Standby';
  const tone = demoMode ? 'demo' : status;

  return (
    <span className={`connection-pill tone-${tone}`} role="status" aria-live="polite">
      <i aria-hidden="true" />
      {label}
    </span>
  );
}
