import { useEffect, useState } from 'react';
import { demoMode } from '../config/settings';
import { AlertPanel } from '../components/AlertPanel';
import { LitresCounter } from '../components/LitresCounter';
import { LiveMap } from '../components/LiveMap';
import { PressureChart } from '../components/PressureChart';
import { selectSelectedPressures, useDashboard } from '../store/useDashboard';

function ShortcutOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="shortcut-backdrop" role="presentation" onClick={onClose}>
      <section className="shortcut-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcut-title" onClick={(event) => event.stopPropagation()}>
        <div className="shortcut-heading"><div><div className="eyebrow">OPERATOR INPUT</div><h2 id="shortcut-title">Keyboard shortcuts</h2></div><button type="button" className="quiet-button" onClick={onClose}>Close <kbd>Esc</kbd></button></div>
        <dl className="shortcut-list">
          <div><dt><kbd>Space</kbd></dt><dd>Start / stop run</dd></div>
          <div><dt><kbd>R</kbd></dt><dd>Reset run</dd></div>
          <div><dt><kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd></dt><dd>Speed 1× / 5× / 20×</dd></div>
          <div><dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Previous / next sensor</dd></div>
          <div><dt><kbd>G</kbd></dt><dd>Toggle relative suspicion</dd></div>
          {demoMode && <div><dt><kbd>L</kbd></dt><dd>Reveal actual leak</dd></div>}
        </dl>
      </section>
    </div>
  );
}

export function HomePage() {
  const selectedSensor = useDashboard((state) => state.selectedSensor);
  const selectedPressures = useDashboard(selectSelectedPressures);
  const network = useDashboard((state) => state.network);
  const running = useDashboard((state) => state.running);
  const speed = useDashboard((state) => state.speed);
  const setSelectedSensor = useDashboard((state) => state.setSelectedSensor);
  const start = useDashboard((state) => state.start);
  const stop = useDashboard((state) => state.stop);
  const reset = useDashboard((state) => state.reset);
  const setSpeed = useDashboard((state) => state.setSpeed);
  const [heatmap, setHeatmap] = useState(true);
  const [revealLeak, setRevealLeak] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const latest = selectedPressures[selectedPressures.length - 1];

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(target.tagName)) {
        if (event.key !== 'Escape') return;
      }
      const key = event.key.toLowerCase();
      if (event.code === 'Space') {
        event.preventDefault();
        if (running) stop(); else start();
      } else if (key === 'r') {
        event.preventDefault();
        reset();
      } else if (key === '1') setSpeed(1);
      else if (key === '2') setSpeed(5);
      else if (key === '3') setSpeed(20);
      else if (key === 'g') setHeatmap((value) => !value);
      else if (key === 'l' && demoMode) setRevealLeak((value) => !value);
      else if (key === '?') setShowShortcuts(true);
      else if (event.key === 'Escape') {
        setShowShortcuts(false);
        setSelectedSensor('');
      }
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        const sensors = network?.sensors ?? [];
        if (!sensors.length) return;
        const current = Math.max(0, sensors.indexOf(selectedSensor ?? ''));
        const next = (current + (event.key === 'ArrowRight' ? 1 : sensors.length - 1)) % sensors.length;
        setSelectedSensor(sensors[next]);
        event.preventDefault();
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [network, reset, running, selectedSensor, setSelectedSensor, setSpeed, start, stop]);

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
      <button type="button" className="shortcut-trigger" onClick={() => setShowShortcuts(true)} aria-label="Show keyboard shortcuts">?</button>
      {showShortcuts && <ShortcutOverlay onClose={() => setShowShortcuts(false)} />}
      <output className="shortcut-speed" aria-live="polite">{speed}×</output>
    </div>
  );
}
