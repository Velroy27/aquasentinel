import { useDashboard } from '../store/useDashboard';
import { demoMode } from '../config/settings';
import { AlertPanel } from '../components/AlertPanel';
import { LitresCounter } from '../components/LitresCounter';
import { LiveMap } from '../components/LiveMap';
import { PressureChart } from '../components/PressureChart';
import { selectSelectedPressures } from '../store/useDashboard';

export function ShortcutOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="shortcut-backdrop" role="presentation" onClick={onClose}>
      <section className="shortcut-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcut-title" onClick={(event) => event.stopPropagation()}>
        <div className="shortcut-heading"><div><div className="eyebrow">OPERATOR INPUT</div><h2 id="shortcut-title">Keyboard shortcuts</h2></div><button type="button" className="quiet-button" onClick={onClose}>Close <kbd>Esc</kbd></button></div>
        <dl className="shortcut-list">
          <div><dt><kbd>Space</kbd></dt><dd>Start / stop run</dd></div>
          <div><dt><kbd>R</kbd></dt><dd>Reset run</dd></div>
          <div><dt><kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd></dt><dd>Speed 1× / 5× / 20×</dd></div>
          <div><dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Previous / next sensor</dd></div>
          <div><dt><kbd>T</kbd></dt><dd>Toggle 2D / 3D network view</dd></div>
          <div><dt><kbd>O</kbd></dt><dd>Toggle 3D auto-orbit</dd></div>
          <div><dt><kbd>F</kbd></dt><dd>Toggle FPS overlay in 3D</dd></div>
          <div><dt><kbd>E</kbd></dt><dd>Toggle animation effects</dd></div>
          <div><dt><kbd>G</kbd></dt><dd>Toggle relative suspicion while an alert is active</dd></div>
          <div><dt><kbd>L</kbd></dt><dd>{demoMode ? 'Reveal actual leak' : 'Unavailable in live data mode'}</dd></div>
        </dl>
      </section>
    </div>
  );
}

export function HomePage() {
  const selectedSensor = useDashboard((state) => state.selectedSensor);
  const selectedPressures = useDashboard(selectSelectedPressures);
  const heatmap = useDashboard((state) => state.heatmapEnabled);
  const revealLeak = useDashboard((state) => state.revealLeak);
  const setSelectedSensor = useDashboard((state) => state.setSelectedSensor);
  const setHeatmap = useDashboard((state) => state.setHeatmapEnabled);
  const setRevealLeak = useDashboard((state) => state.setRevealLeak);
  const latest = selectedPressures[selectedPressures.length - 1];

  return (
    <div className="home-ops">
      <div className="ops-main">
        <LiveMap
          selectedSensor={selectedSensor}
          onSelectSensor={setSelectedSensor}
          heatmapEnabled={heatmap}
          onHeatmapChange={setHeatmap}
          revealLeak={revealLeak}
          onRevealLeakChange={setRevealLeak}
        />
        <section className="chart-strip">
          <div className="chart-strip-meta">
            <div className="eyebrow">PRESSURE / {selectedSensor || '—'}</div>
            <div className="chart-current">
              <strong>{latest ? latest.pressureM.toFixed(1) : '—'}</strong><span>m</span>
            </div>
            <div className="chart-delta">{latest ? `${latest.anomaly ? 'ANOMALY' : 'CURRENT'} · ${new Date(latest.timestamp).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST` : 'NO SAMPLE'}</div>
          </div>
          <PressureChart />
        </section>
      </div>
      <aside className="ops-side">
        <LitresCounter />
        <AlertPanel />
      </aside>
    </div>
  );
}
