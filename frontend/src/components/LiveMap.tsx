import { memo, useEffect, useMemo, useState } from 'react';
import { Copy, LocateFixed, Minus, Plus, X } from 'lucide-react';
import { demoMode, pressureDelta, pressureScale } from '../config/settings';
import { selectCurrentScenario, useDashboard } from '../store/useDashboard';
import type { Junction } from '../data/types';

type Props = {
  selectedSensor: string | null;
  onSelectSensor?: (id: string) => void;
  highlightSensors?: string[];
  showLocalization?: boolean;
  heatmapEnabled?: boolean;
  onHeatmapChange?: (enabled: boolean) => void;
  revealLeak?: boolean;
  onRevealLeakChange?: (enabled: boolean) => void;
  title?: string;
  subtitle?: string;
};

type NodeViewProps = {
  junction: Junction;
  delta?: number;
  isSensor: boolean;
  selected: boolean;
  hovered: boolean;
  rank?: number;
  leak: boolean;
  onHover: (junction: Junction | null) => void;
  onSelect: (junction: Junction) => void;
};

function pressureColor(delta: number | undefined): string {
  if (delta === undefined) return pressureScale.normal;
  const neutral = { r: 119, g: 129, b: 125 };
  const low = { r: 188, g: 119, b: 86 };
  const high = { r: 99, g: 152, b: 140 };
  const amount = Math.min(1, Math.abs(delta) / (delta < 0 ? 5 : 4));
  const target = delta < 0 ? low : high;
  const r = Math.round(neutral.r + (target.r - neutral.r) * amount);
  const g = Math.round(neutral.g + (target.g - neutral.g) * amount);
  const b = Math.round(neutral.b + (target.b - neutral.b) * amount);
  return `rgb(${r} ${g} ${b})`;
}

const JunctionNode = memo(function JunctionNode({
  junction,
  delta,
  isSensor,
  selected,
  hovered,
  rank,
  leak,
  onHover,
  onSelect,
}: NodeViewProps) {
  const color = pressureColor(delta);
  return (
    <g
      className={`map-node ${isSensor ? 'sensor' : ''} ${selected ? 'selected' : ''} ${hovered ? 'hovered' : ''}`}
      tabIndex={0}
      role="button"
      aria-label={`${junction.id}${isSensor ? ', sensor' : ', junction'}${delta === undefined ? '' : `, pressure change ${delta.toFixed(1)} metres`}`}
      onPointerEnter={() => onHover(junction)}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onHover(junction)}
      onBlur={() => onHover(null)}
      onClick={() => onSelect(junction)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(junction);
        }
      }}
    >
      {selected && <circle cx={junction.x} cy={junction.y} r="6.2" className="node-selection-ring" />}
      {rank && <circle cx={junction.x} cy={junction.y} r="5.4" className="rank-halo" />}
      {leak && <circle cx={junction.x} cy={junction.y} r="7" className="leak-ring" />}
      {isSensor ? (
        <path d={`M ${junction.x} ${junction.y - 2.9} L ${junction.x + 2.9} ${junction.y} L ${junction.x} ${junction.y + 2.9} L ${junction.x - 2.9} ${junction.y} Z`} fill={color} stroke="#dce9e5" strokeWidth="0.55" className="node-dot sensor-diamond" />
      ) : (
        <circle cx={junction.x} cy={junction.y} r="1.55" fill={color} stroke="#a9b6b4" strokeWidth="0.2" className="node-dot" />
      )}
      {(isSensor || rank || hovered || selected) && (
        <text x={junction.x} y={junction.y - (isSensor ? 4.5 : 3)} className={`node-label ${isSensor ? 'sensor-label' : ''}`}>{junction.id}</text>
      )}
      {rank && (
        <g className={`rank-badge rank-${rank}`}>
          <circle cx={junction.x + 3.8} cy={junction.y - 3.8} r="2.35" />
          <text x={junction.x + 3.8} y={junction.y - 3.05}>{rank}</text>
        </g>
      )}
      {leak && <text x={junction.x} y={junction.y + 7.8} className="leak-label">ACTUAL LEAK</text>}
    </g>
  );
});

export function LiveMap({
  selectedSensor,
  onSelectSensor,
  highlightSensors,
  showLocalization = true,
  heatmapEnabled = true,
  onHeatmapChange,
  revealLeak = false,
  onRevealLeakChange,
  title = 'Pressure field',
  subtitle,
}: Props) {
  const network = useDashboard((state) => state.network);
  const pressuresBySensor = useDashboard((state) => state.pressuresBySensor);
  const localization = useDashboard((state) => state.localization);
  const scenario = useDashboard(selectCurrentScenario);
  const loading = useDashboard((state) => state.loading);
  const error = useDashboard((state) => state.error);
  const [selectedJunctionId, setSelectedJunctionId] = useState<string | null>(null);
  const [hovered, setHovered] = useState<Junction | null>(null);
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(1);

  const latest = useMemo(() => {
    const points: Record<string, (typeof pressuresBySensor)[string][number] | undefined> = {};
    for (const [sensorId, readings] of Object.entries(pressuresBySensor)) points[sensorId] = readings[readings.length - 1];
    return points;
  }, [pressuresBySensor]);
  const pipeEndpoints = useMemo(() => {
    if (!network) return [];
    const nodes = new Map(network.junctions.map((junction) => [junction.id, junction]));
    return network.pipes.flatMap((pipe) => {
      const from = nodes.get(pipe.from);
      const to = nodes.get(pipe.to);
      if (!from || !to) return [];
      const length = Math.hypot(from.x - to.x, from.y - to.y);
      return [{ id: pipe.id, from, to, trunk: length > 17 }];
    });
  }, [network]);
  const probabilityMap = useMemo(
    () => new Map((localization?.probabilities ?? []).map((item) => [item.junctionId, item.probability])),
    [localization],
  );
  const top3 = useMemo(
    () => [...(localization?.probabilities ?? [])].sort((a, b) => b.probability - a.probability).slice(0, 3),
    [localization],
  );
  const rankMap = useMemo(() => new Map(top3.map((item, index) => [item.junctionId, index + 1])), [top3]);
  const junctionMap = useMemo(() => new Map((network?.junctions ?? []).map((node) => [node.id, node])), [network]);
  const sensorSet = useMemo(() => new Set(highlightSensors ?? network?.sensors ?? []), [highlightSensors, network]);
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

  useEffect(() => {
    function onEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setSelectedJunctionId(null);
    }
    window.addEventListener('keydown', onEscape);
    return () => window.removeEventListener('keydown', onEscape);
  }, []);

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_200);
    } catch {
      setCopied(false);
    }
  }

  if (loading) return <div className="panel map-shell skeleton">Loading network…</div>;
  if (error || !network) return <div className="panel map-shell error-state">{error ?? 'Network unavailable'}</div>;

  return (
    <section className="panel map-shell map-stage">
      <div className="map-stage-head">
        <div className="map-heading">
          <div className="eyebrow">NET3 / DISTRIBUTION</div>
          <h2>{title}</h2>
          <span className="map-count">{network.sensors.length} sensors <i>·</i> {network.junctions.length} nodes <i>·</i> {network.pipes.length} links</span>
        </div>
        <div className="map-actions">
          {showLocalization && <button type="button" className={`map-toggle ${heatmapEnabled ? 'is-on' : ''}`} onClick={() => onHeatmapChange?.(!heatmapEnabled)} aria-pressed={heatmapEnabled}>P(leak) field <kbd>G</kbd></button>}
          {showLocalization && demoMode && <button type="button" className={`map-toggle reveal-toggle ${revealLeak ? 'is-on' : ''}`} onClick={() => onRevealLeakChange?.(!revealLeak)} aria-pressed={revealLeak}>Reveal true leak <kbd>L</kbd></button>}
          <div className="map-zoom"><button type="button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(1, value - 0.25))}><Minus size={16} /></button><span>{Math.round(zoom * 100)}%</span><button type="button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(2, value + 0.25))}><Plus size={16} /></button></div>
        </div>
      </div>

      {top3.length > 0 && showLocalization && <div className="suspect-strip">{top3.map((item, index) => <button type="button" key={item.junctionId} className={`suspect-rank suspect-${index + 1}`} onClick={() => setSelectedJunctionId(item.junctionId)}><b>{index + 1}</b><span>{item.junctionId}</span><strong>{(item.probability * 100).toFixed(0)}%</strong></button>)}</div>}

      <div className="map-canvas map-viewport">
        <svg viewBox="0 0 100 100" role="img" aria-label="Water distribution network map" preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="graticule" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#263238" strokeWidth="0.13" />
              <path d="M 10 0 L 10 20 M 0 10 L 20 10" fill="none" stroke="#1d292d" strokeWidth="0.08" strokeDasharray="0.5 0.8" />
            </pattern>
          </defs>
          <rect width="100" height="100" fill="url(#graticule)" />
          <g className="map-zoom-layer" transform={`translate(${50 - 50 * zoom} ${50 - 50 * zoom}) scale(${zoom})`}>
            {heatmapEnabled && showLocalization && localization && network.junctions.map((junction) => {
              const probability = probabilityMap.get(junction.id) ?? 0;
              if (probability < 0.008) return null;
              return <circle key={`heat-${junction.id}`} cx={junction.x} cy={junction.y} r={1.3 + probability * 16} fill="#56b9ac" opacity={Math.min(0.32, probability * 2.5)} className="heat-cell" />;
            })}
            {pipeEndpoints.map(({ id, from, to, trunk }) => (
              <g key={id} className={trunk ? 'pipe-group pipe-trunk' : 'pipe-group pipe-branch'}>
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="pipe-casing" />
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="pipe" />
              </g>
            ))}
            {network.junctions.map((junction) => {
              const point = latest[junction.id];
              const delta = point ? point.pressureM - scenarioBase : undefined;
              return (
                <JunctionNode
                  key={junction.id}
                  junction={junction}
                  delta={delta}
                  isSensor={sensorSet.has(junction.id)}
                  selected={selectedSensor === junction.id || selectedJunctionId === junction.id}
                  hovered={hovered?.id === junction.id}
                  rank={showLocalization ? rankMap.get(junction.id) : undefined}
                  leak={demoMode && revealLeak && scenario?.leakNode === junction.id}
                  onHover={setHovered}
                  onSelect={(node) => {
                    setSelectedJunctionId(node.id);
                    if (network.sensors.includes(node.id)) onSelectSensor?.(node.id);
                  }}
                />
              );
            })}
            {hovered && <g className="map-crosshair"><line x1={hovered.x} y1="0" x2={hovered.x} y2="100" /><line x1="0" y1={hovered.y} x2="100" y2={hovered.y} /></g>}
          </g>
        </svg>

        <div className="map-orientation"><span className="north-arrow">↑</span><span>N</span></div>
        <div className="map-scale"><i /><span>0</span><span>schematic</span></div>
        {hovered && <div className="map-hover-hud"><b>{hovered.id}</b><span>{hovered.x.toFixed(1)}, {hovered.y.toFixed(1)}</span><span>{network.sensors.includes(hovered.id) ? 'SENSOR' : 'JUNCTION'}</span></div>}
        {hovered && <div className="map-tooltip" style={{ left: `${Math.min(hovered.x, 74)}%`, top: `${Math.min(hovered.y, 72)}%` }}>
          <b>{hovered.id}</b><span>{latest[hovered.id] ? `${latest[hovered.id]!.pressureM.toFixed(1)} m` : 'No pressure sample'}</span><span>Δ {latest[hovered.id] ? `${(latest[hovered.id]!.pressureM - scenarioBase).toFixed(1)} m` : '—'}</span><span>P(leak) {probabilityMap.has(hovered.id) ? `${((probabilityMap.get(hovered.id) ?? 0) * 100).toFixed(1)}%` : '—'}</span><span>{network.sensors.includes(hovered.id) ? 'Sensor' : 'Junction'}</span>
        </div>}
        {selectedJunction && <aside className="node-inspector" aria-label={`${selectedJunction.id} inspector`}>
          <div className="inspector-head"><div><span className="eyebrow">NODE INSPECTOR</span><h3>{selectedJunction.id}</h3></div><button type="button" className="icon-button" onClick={() => setSelectedJunctionId(null)} aria-label="Close inspector"><X size={17} /></button></div>
          <button type="button" className="copy-id" onClick={() => void copyId(selectedJunction.id)}><Copy size={14} />{copied ? 'Copied' : 'Copy ID'}</button>
          <dl className="inspector-data">
            <div><dt>Type</dt><dd>{network.sensors.includes(selectedJunction.id) ? 'Sensor / junction' : 'Junction'}</dd></div>
            <div><dt>Elevation</dt><dd>{selectedJunction.elevationM.toFixed(1)} m</dd></div>
            <div><dt>Pressure</dt><dd>{selectedReading ? `${selectedReading.pressureM.toFixed(1)} m` : '—'}</dd></div>
            <div><dt>Change</dt><dd className={selectedDelta !== undefined && selectedDelta <= pressureDelta.loss ? 'value-warn' : ''}>{selectedDelta === undefined ? '—' : `${selectedDelta > 0 ? '+' : ''}${selectedDelta.toFixed(1)} m`}</dd></div>
            <div><dt>P(leak)</dt><dd>{probabilityMap.has(selectedJunction.id) ? `${((probabilityMap.get(selectedJunction.id) ?? 0) * 100).toFixed(1)}%` : '—'}</dd></div>
            <div><dt>Connected</dt><dd>{neighbors.length} links</dd></div>
          </dl>
          <div className="inspector-links"><span>NEIGHBOURS</span><div>{neighbors.slice(0, 8).map((id) => <button key={id} type="button" onClick={() => setSelectedJunctionId(id)}>{id}</button>)}</div></div>
          <div className="inspector-links"><span>NEAREST SENSORS</span><div>{nearestSensors.map((id) => <button key={id} type="button" onClick={() => { setSelectedJunctionId(id); onSelectSensor?.(id); }}>{id}</button>)}</div></div>
        </aside>}
      </div>

      <div className="map-footer">
        <div className="map-legends"><span><i className="legend-dot low" />Drop ≤ {pressureDelta.loss} m</span><span><i className="legend-dot stable" />Nominal</span><span><i className="legend-dot high" />Rise ≥ +{pressureDelta.rise} m</span>
          {heatmapEnabled && localization && <span className="probability-key"><i />P(leak) low → high</span>}
        </div>
        <span className="map-data-state"><i className={`live-dot ${hasPressure ? 'active' : ''}`} />{hasPressure ? 'PRESSURE VALUES / SENSORS ONLY' : 'NO PRESSURE SAMPLE'}</span>
      </div>
      {showLocalization && top3.length > 0 && <div className="top3-summary">{top3.map((item, index) => `${index + 1}. ${item.junctionId} · P ${item.probability.toFixed(2)}`).join('     ')}</div>}
      <span className="map-coordinate"><LocateFixed size={13} /> {hovered ? `${hovered.x.toFixed(1)} / ${hovered.y.toFixed(1)}` : 'GRID REF —'}</span>
      {subtitle && <span className="visually-hidden">{subtitle}</span>}
    </section>
  );
}
