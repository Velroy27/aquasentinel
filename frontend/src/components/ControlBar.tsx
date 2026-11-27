import { RotateCcw, Square, Triangle } from 'lucide-react';
import { useDashboard } from '../store/useDashboard';

export function ControlBar() {
  const scenarios = useDashboard((s) => s.scenarios);
  const scenarioStatus = useDashboard((s) => s.resources.scenarios);
  const retryResource = useDashboard((s) => s.retryResource);
  const scenarioId = useDashboard((s) => s.scenarioId);
  const speed = useDashboard((s) => s.speed);
  const running = useDashboard((s) => s.running);
  const setScenario = useDashboard((s) => s.setScenario);
  const setSpeed = useDashboard((s) => s.setSpeed);
  const start = useDashboard((s) => s.start);
  const stop = useDashboard((s) => s.stop);
  const reset = useDashboard((s) => s.reset);

  return (
    <div className="control-bar" role="region" aria-label="Simulation controls">
      <label className="scenario-picker">
        <span className="eyebrow">Scenario</span>
        <select value={scenarioId} onChange={(e) => setScenario(e.target.value)} aria-label="Select scenario" disabled={scenarioStatus.loading || Boolean(scenarioStatus.error) || !scenarios.length}>
          {scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.name}</option>)}
        </select>
      </label>
      {scenarioStatus.loading && <span className="control-notice">Loading scenarios…</span>}
      {scenarioStatus.error && <span className="control-notice is-error" role="alert">Scenario data unavailable <button type="button" onClick={() => void retryResource('scenarios')}>Retry</button></span>}
      <div className="speed-control">
        <span className="eyebrow">Speed</span>
        <div className="speed-options" role="radiogroup" aria-label="Playback speed">
          {([1, 5, 20] as const).map((value) => (
            <button key={value} type="button" className={speed === value ? 'selected' : ''} onClick={() => setSpeed(value)} aria-pressed={speed === value}>{value}x</button>
          ))}
        </div>
      </div>
      <div className="control-actions">
        <button type="button" className="action-button start-button" onClick={start} disabled={running || !scenarioId}><Triangle size={16} fill="currentColor" /> Start</button>
        <button type="button" className="action-button" onClick={stop} disabled={!running}><Square size={15} fill="currentColor" /> Stop</button>
        <button type="button" className="action-button reset-button" onClick={reset}><RotateCcw size={16} /> Reset demo</button>
      </div>
    </div>
  );
}
