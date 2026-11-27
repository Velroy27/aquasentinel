import { useState, useEffect, useMemo, useCallback } from 'react';
import { Copy, LocateFixed, X, Compass } from 'lucide-react';
import { demoMode, pressureDelta } from '../../config/settings';
import { useDashboard, selectCurrentScenario } from '../../store/useDashboard';
import type { Junction, PressurePoint } from '../../data/types';
import type { CameraPreset, Network3DProps } from './types';
import { Network3DCanvas } from './Network3DCanvas';
import { ErrorBoundary3D } from './ErrorBoundary3D';

export default function Network3DView({
  selectedSensor,
  onSelectSensor,
  highlightSensors: _highlightSensors,
  showLocalization = true,
  heatmapEnabled = true,
  onHeatmapChange,
  revealLeak = false,
  onRevealLeakChange,
  title = 'Live 3D Network',
  subtitle,
  emptyNotice,
  onToggleViewMode,
}: Network3DProps) {
  const network = useDashboard((state) => state.network);
  const networkStatus = useDashboard((state) => state.resources.network);
  const retryResource = useDashboard((state) => state.retryResource);
  const running = useDashboard((state) => state.running);
  const alertActive = useDashboard((state) => state.alertActive);
  const scenario = useDashboard(selectCurrentScenario);
  const mapFitRevision = useDashboard((state) => state.mapFitRevision);
  const pressuresBySensor = useDashboard((state) => state.pressuresBySensor);
  const localization = useDashboard((state) => state.localization);

  const [effectsEnabled, setEffectsEnabled] = useState(true);
  const [cameraPreset, setCameraPreset] = useState<CameraPreset>('angled');
  const [autoOrbit, setAutoOrbit] = useState(false);
  const [fpsVisible, setFpsVisible] = useState(false);
  const [fpsData, setFpsData] = useState<{ fps: number; ms: number } | null>(null);
  const [selectedJunctionId, setSelectedJunctionId] = useState<string | null>(selectedSensor);
  const [hoveredJunction, setHoveredJunction] = useState<Junction | null>(null);
  const [copied, setCopied] = useState(false);

  // Sync selectedSensor prop
  useEffect(() => {
    setSelectedJunctionId(selectedSensor);
  }, [selectedSensor]);

  // Derived values
  const latest = useMemo(() => {
    const res: Record<string, PressurePoint | undefined> = {};
    for (const [id, points] of Object.entries(pressuresBySensor)) {
      res[id] = points[points.length - 1];
    }
    return res;
  }, [pressuresBySensor]);

  const top3 = useMemo(() => {
    if (!alertActive || !localization) return [];
    return [...(localization.scores ?? [])].sort((a, b) => b.score - a.score).slice(0, 3);
  }, [alertActive, localization]);

  const rankMap = useMemo(() => new Map(top3.map((item, index) => [item.junctionId, index + 1])), [top3]);
  const junctionMap = useMemo(() => new Map((network?.junctions ?? []).map((node) => [node.id, node])), [network]);

  const hasElevationData = useMemo(() => {
    if (!network?.junctions) return false;
    return network.junctions.some((j) => typeof j.elevationM === 'number' && j.elevationM !== 0);
  }, [network]);

  const selectedJunction = selectedJunctionId ? junctionMap.get(selectedJunctionId) : undefined;
  const selectedReading = selectedJunction ? latest[selectedJunction.id] : undefined;
  const scenarioBase = scenario?.basePressureM ?? 52;
  const selectedDelta = selectedReading ? selectedReading.pressureM - scenarioBase : undefined;

  const neighbors = selectedJunction && network
    ? network.pipes.flatMap((pipe) => pipe.from === selectedJunction.id ? [pipe.to] : pipe.to === selectedJunction.id ? [pipe.from] : [])
    : [];

  const nearestSensors = selectedJunction && network
    ? network.sensors
      .map((id) => ({ id, node: junctionMap.get(id) }))
      .filter((item): item is { id: string; node: Junction } => Boolean(item.node))
      .sort((a, b) => Math.hypot(a.node.x - selectedJunction.x, a.node.y - selectedJunction.y) - Math.hypot(b.node.x - selectedJunction.x, b.node.y - selectedJunction.y))
      .slice(0, 3)
      .map((item) => item.id)
    : [];

  const hasPressure = Object.values(latest).some(Boolean);

  // Keyboard shortcut listener for 3D view
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target;
      const editable = target instanceof HTMLElement && (/^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName) || target.isContentEditable);
      if (editable || event.ctrlKey || event.metaKey || event.altKey) return;

      const key = event.key.toLowerCase();
      if (key === 't') {
        event.preventDefault();
        onToggleViewMode?.();
      } else if (key === 'e') {
        event.preventDefault();
        setEffectsEnabled((prev) => !prev);
      } else if (key === 'o') {
        event.preventDefault();
        setAutoOrbit((prev) => !prev);
      } else if (key === 'f') {
        event.preventDefault();
        setFpsVisible((prev) => !prev);
      } else if (key === 'escape') {
        setSelectedJunctionId(null);
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onToggleViewMode]);

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_200);
    } catch {
      setCopied(false);
    }
  }

  const handleFpsUpdate = useCallback((fps: number, ms: number) => {
    setFpsData({ fps, ms });
  }, []);

  if (networkStatus.loading) return <div className="panel map-shell skeleton">Loading 3D network…</div>;
  if (networkStatus.error || !network) {
    return (
      <div className="panel map-shell error-state" role="alert">
        <span>{networkStatus.error ?? 'Network unavailable'}</span>
        <button type="button" onClick={() => void retryResource('network')}>Retry</button>
      </div>
    );
  }

  return (
    <section className="panel map-shell map-stage">
      {/* Head Toolbar */}
      <div className="map-stage-head">
        <div className="map-heading">
          <div className="eyebrow">NET3 / 3D PERSPECTIVE</div>
          <h2>{title}</h2>
          <span className="map-count">
            {network.sensors.length} sensors <i>·</i> {network.junctions.length} nodes <i>·</i> {network.pipes.length} links
          </span>
        </div>

        <div className="map-actions">
          {/* 2D / 3D Toggle Button */}
          <button
            type="button"
            className="map-toggle is-on"
            onClick={onToggleViewMode}
            title="Switch back to 2D Schematic Map (T)"
          >
            <Compass size={14} /> 2D / <b>3D</b> <kbd>T</kbd>
          </button>

          {/* Effects Toggle */}
          <button
            type="button"
            className={`map-toggle effects-toggle ${effectsEnabled ? 'is-on' : ''}`}
            onClick={() => setEffectsEnabled((enabled) => !enabled)}
            aria-pressed={effectsEnabled}
          >
            Effects <kbd>E</kbd>
          </button>

          {/* Suspicion Field */}
          {showLocalization && alertActive && (
            <button
              type="button"
              className={`map-toggle ${heatmapEnabled ? 'is-on' : ''}`}
              onClick={() => onHeatmapChange?.(!heatmapEnabled)}
              aria-pressed={heatmapEnabled}
            >
              Suspicion field <kbd>G</kbd>
            </button>
          )}

          {/* Reveal True Leak in Demo Mode */}
          {showLocalization && alertActive && demoMode && (
            <button
              type="button"
              className={`map-toggle reveal-toggle ${revealLeak ? 'is-on' : ''}`}
              onClick={() => onRevealLeakChange?.(!revealLeak)}
              aria-pressed={revealLeak}
            >
              Reveal true leak <kbd>L</kbd>
            </button>
          )}

          {/* Camera Presets */}
          <div className="map-zoom" style={{ gap: '4px' }}>
            <button
              type="button"
              className={`map-toggle ${cameraPreset === 'top' ? 'is-on' : ''}`}
              style={{ minHeight: '29px', padding: '0 6px', fontSize: '11px' }}
              onClick={() => setCameraPreset('top')}
              title="Camera Top-Down View"
            >
              Top
            </button>
            <button
              type="button"
              className={`map-toggle ${cameraPreset === 'angled' ? 'is-on' : ''}`}
              style={{ minHeight: '29px', padding: '0 6px', fontSize: '11px' }}
              onClick={() => setCameraPreset('angled')}
              title="Camera Angled 3D Perspective"
            >
              Angled
            </button>
            <button
              type="button"
              className={`map-toggle ${cameraPreset === 'follow' ? 'is-on' : ''}`}
              style={{ minHeight: '29px', padding: '0 6px', fontSize: '11px' }}
              onClick={() => setCameraPreset('follow')}
              disabled={!alertActive || top3.length === 0}
              title="Follow #1 Ranked Suspect Node"
            >
              Follow #1
            </button>
            <button
              type="button"
              className={`map-toggle ${autoOrbit ? 'is-on' : ''}`}
              style={{ minHeight: '29px', padding: '0 6px', fontSize: '11px' }}
              onClick={() => setAutoOrbit((o) => !o)}
              title="Slow Auto-Orbit (O)"
            >
              Orbit <kbd>O</kbd>
            </button>
            <button
              type="button"
              className="fit-button"
              onClick={() => setCameraPreset('angled')}
              title="Fit Entire Network"
            >
              Fit
            </button>
            <button
              type="button"
              className={`map-toggle ${fpsVisible ? 'is-on' : ''}`}
              style={{ minHeight: '29px', padding: '0 6px', fontSize: '11px' }}
              onClick={() => setFpsVisible((v) => !v)}
              title="Toggle Dev FPS (F)"
            >
              FPS <kbd>F</kbd>
            </button>
          </div>
        </div>
      </div>

      {/* Suspect Strip when Alert Active */}
      {top3.length > 0 && showLocalization && alertActive && (
        <div className="suspect-strip" aria-label="Ranked suspect nodes">
          <span className="eyebrow">RANKED NODES</span>
          {top3.map((item, index) => (
            <button
              type="button"
              key={item.junctionId}
              className={`suspect-rank suspect-${index + 1}`}
              onClick={() => {
                setSelectedJunctionId(item.junctionId);
                setCameraPreset('follow');
              }}
            >
              <b>#{index + 1}</b>
              <span>{item.junctionId}</span>
            </button>
          ))}
        </div>
      )}

      {/* 3D Canvas Area */}
      <div className="map-canvas map-viewport" style={{ position: 'relative', width: '100%', height: '100%' }}>
        <ErrorBoundary3D onFallback={() => onToggleViewMode?.()}>
          <Network3DCanvas
            network={network}
            scenario={scenario}
            latest={latest}
            selectedJunctionId={selectedJunctionId}
            hoveredJunctionId={hoveredJunction?.id ?? null}
            onSelectJunction={(j) => {
              setSelectedJunctionId(j.id);
              if (network.sensors.includes(j.id)) onSelectSensor?.(j.id);
            }}
            onHoverJunction={setHoveredJunction}
            running={running}
            effectsEnabled={effectsEnabled}
            alertActive={alertActive}
            heatmapEnabled={heatmapEnabled}
            revealLeak={revealLeak}
            demoMode={demoMode}
            top3={top3}
            cameraPreset={cameraPreset}
            onCameraPresetChange={setCameraPreset}
            autoOrbit={autoOrbit}
            mapFitRevision={mapFitRevision}
            fpsVisible={fpsVisible}
            onFpsUpdate={handleFpsUpdate}
            hasElevationData={hasElevationData}
          />
        </ErrorBoundary3D>

        {/* Notice if network lacks elevation */}
        {!hasElevationData && (
          <div className="map-data-notice" role="status" style={{ pointerEvents: 'none' }}>
            NO ELEVATION DATA · Rendering flat schematic
          </div>
        )}

        {emptyNotice && <div className="map-data-notice" role="status">{emptyNotice}</div>}

        {/* FPS Counter Overlay */}
        {fpsVisible && (
          <span className="map-fps" style={{ display: 'block' }}>
            {fpsData ? `FPS ${fpsData.fps} · ${fpsData.ms} ms` : 'FPS Measuring…'}
          </span>
        )}

        {/* Hover HUD */}
        {hoveredJunction && (
          <div className="map-hover-hud" role="status">
            <b>{hoveredJunction.id}</b>
            <span>GRID {hoveredJunction.x.toFixed(1)} / {hoveredJunction.y.toFixed(1)}</span>
            <span>ELEV {hoveredJunction.elevationM !== undefined ? `${hoveredJunction.elevationM.toFixed(1)} m` : '—'}</span>
            <span>{network.sensors.includes(hoveredJunction.id) ? 'SENSOR' : 'JUNCTION'}</span>
            <span>{latest[hoveredJunction.id] ? `P ${latest[hoveredJunction.id]!.pressureM.toFixed(1)} m` : 'P —'}</span>
            <span>
              Δ {latest[hoveredJunction.id] ? `${(latest[hoveredJunction.id]!.pressureM - scenarioBase).toFixed(1)} m` : '—'}
            </span>
            <span>
              {showLocalization && alertActive && rankMap.has(hoveredJunction.id)
                ? `SUSPECT #${rankMap.get(hoveredJunction.id)}`
                : 'SUSPECT —'}
            </span>
          </div>
        )}

        {/* Node Inspector side panel */}
        {selectedJunction && (
          <aside className="node-inspector" aria-label={`${selectedJunction.id} inspector`}>
            <div className="inspector-head">
              <div>
                <span className="eyebrow">NODE INSPECTOR (3D)</span>
                <h3>{selectedJunction.id}</h3>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setSelectedJunctionId(null)}
                aria-label="Close inspector"
              >
                <X size={17} />
              </button>
            </div>
            <button type="button" className="copy-id" onClick={() => void copyId(selectedJunction.id)}>
              <Copy size={14} />
              {copied ? 'Copied' : 'Copy ID'}
            </button>
            <dl className="inspector-data">
              <div>
                <dt>Type</dt>
                <dd>{network.sensors.includes(selectedJunction.id) ? 'Sensor / junction' : 'Junction'}</dd>
              </div>
              <div>
                <dt>Elevation</dt>
                <dd>{selectedJunction.elevationM.toFixed(1)} m</dd>
              </div>
              <div>
                <dt>Pressure</dt>
                <dd>{selectedReading ? `${selectedReading.pressureM.toFixed(1)} m` : '—'}</dd>
              </div>
              <div>
                <dt>Change</dt>
                <dd className={selectedDelta !== undefined && selectedDelta <= pressureDelta.loss ? 'value-warn' : ''}>
                  {selectedDelta === undefined ? '—' : `${selectedDelta > 0 ? '+' : ''}${selectedDelta.toFixed(1)} m`}
                </dd>
              </div>
              {showLocalization && alertActive && rankMap.has(selectedJunction.id) && (
                <div>
                  <dt>Suspect rank</dt>
                  <dd>#{rankMap.get(selectedJunction.id)}</dd>
                </div>
              )}
              <div>
                <dt>Connected</dt>
                <dd>{neighbors.length} links</dd>
              </div>
            </dl>
            <div className="inspector-links">
              <span>NEIGHBOURS</span>
              <div>
                {neighbors.slice(0, 8).map((id) => (
                  <button key={id} type="button" onClick={() => setSelectedJunctionId(id)}>
                    {id}
                  </button>
                ))}
              </div>
            </div>
            <div className="inspector-links">
              <span>NEAREST SENSORS</span>
              <div>
                {nearestSensors.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => {
                      setSelectedJunctionId(id);
                      onSelectSensor?.(id);
                    }}
                  >
                    {id}
                  </button>
                ))}
              </div>
            </div>
          </aside>
        )}
      </div>

      {/* Footer / Legends */}
      <div className="map-footer">
        <div className="map-legends">
          <span>
            <i className="legend-dot low" />Low Δ ≤ {pressureDelta.loss} m <i className="effect-icon effect-in" aria-hidden="true" />contracting
          </span>
          <span><i className="legend-dot stable" />Nominal</span>
          <span>
            <i className="legend-dot high" />High Δ ≥ +{pressureDelta.rise} m <i className="effect-icon effect-out" aria-hidden="true" />expanding
          </span>
          <span><i className="effect-icon effect-flow" aria-hidden="true" />Flow from → to</span>
          {heatmapEnabled && showLocalization && alertActive && localization && (
            <span className="suspicion-key"><i />RELATIVE SUSPICION</span>
          )}
        </div>
        <span className="map-data-state">
          <i className={`live-dot ${hasPressure ? 'active' : ''}`} />
          {hasPressure ? '3D VIEW · HARDWARE ACCELERATED' : 'NO PRESSURE SAMPLE'}
        </span>
      </div>
      <span className="map-coordinate">
        <LocateFixed size={13} /> {hoveredJunction ? `${hoveredJunction.x.toFixed(1)} / ${hoveredJunction.y.toFixed(1)} (Z:${hoveredJunction.elevationM.toFixed(1)}m)` : '3D PERSPECTIVE —'}
      </span>
      {subtitle && <span className="visually-hidden">{subtitle}</span>}
    </section>
  );
}
