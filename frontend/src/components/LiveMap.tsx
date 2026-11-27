import { memo, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
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
  emptyNotice?: string;
};

type NodeViewProps = {
  junction: Junction;
  delta?: number;
  isSensor: boolean;
  selected: boolean;
  hovered: boolean;
  rank?: number;
  leak: boolean;
  screenUnit: number;
  label?: { x: number; y: number; anchor: 'middle' | 'start' | 'end' };
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
  screenUnit,
  label,
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
      <circle cx={junction.x} cy={junction.y} r={6 * screenUnit} className="node-hit" />
      {selected && <circle cx={junction.x} cy={junction.y} r={8 * screenUnit} className="node-selection-ring" />}
      {rank && <circle cx={junction.x} cy={junction.y} r={7 * screenUnit} className="rank-halo" />}
      {leak && <circle cx={junction.x} cy={junction.y} r={10 * screenUnit} className="leak-ring" />}
      {isSensor ? (
        <path d={`M ${junction.x} ${junction.y - 4.5 * screenUnit} L ${junction.x + 4.5 * screenUnit} ${junction.y} L ${junction.x} ${junction.y + 4.5 * screenUnit} L ${junction.x - 4.5 * screenUnit} ${junction.y} Z`} fill={color} stroke="#dce9e5" strokeWidth="1.2" vectorEffect="non-scaling-stroke" className="node-dot sensor-diamond" />
      ) : (
        <circle cx={junction.x} cy={junction.y} r={3.3 * screenUnit} fill={color} stroke="#a9b6b4" strokeWidth="0.7" vectorEffect="non-scaling-stroke" className="node-dot" />
      )}
      {label && (
        <text x={label.x} y={label.y} textAnchor={label.anchor} style={{ fontSize: `${12 * screenUnit}px` }} className={`node-label ${isSensor ? 'sensor-label' : ''}`}>{junction.id}</text>
      )}
      {rank && (
        <g className={`rank-badge rank-${rank}`}>
          <circle cx={junction.x + 5 * screenUnit} cy={junction.y - 5 * screenUnit} r={6 * screenUnit} />
          <text x={junction.x + 5 * screenUnit} y={junction.y - 2.2 * screenUnit} style={{ fontSize: `${9 * screenUnit}px` }}>{rank}</text>
        </g>
      )}
      {leak && <text x={junction.x} y={junction.y + 15 * screenUnit} style={{ fontSize: `${10 * screenUnit}px` }} className="leak-label">ACTUAL LEAK</text>}
    </g>
  );
});

type LabelPlacement = { x: number; y: number; anchor: 'middle' | 'start' | 'end' };

function placeLabels(
  nodes: Junction[],
  sensors: Set<string>,
  ranks: Map<string, number>,
  selectedId: string | null,
  hoveredId: string | null,
  unitsPerPixel: number,
  pixelsPerUnit: number,
  viewBoxWidth: number,
  viewBoxHeight: number,
  zoom: number,
  pan: { x: number; y: number },
): Map<string, LabelPlacement> {
  const candidates = nodes
    .filter((node) => sensors.has(node.id) || ranks.has(node.id) || node.id === selectedId || node.id === hoveredId)
    .sort((a, b) => {
      const priority = (node: Junction) => node.id === selectedId ? 4 : ranks.has(node.id) ? 3 : sensors.has(node.id) ? 2 : 1;
      return priority(b) - priority(a);
    });
  const occupied: { left: number; right: number; top: number; bottom: number }[] = [];
  const placed = new Map<string, LabelPlacement>();
  const offsets = [
    { dx: 0, dy: -7, anchor: 'middle' as const },
    { dx: 7, dy: 4, anchor: 'start' as const },
    { dx: -7, dy: 4, anchor: 'end' as const },
    { dx: 0, dy: 15, anchor: 'middle' as const },
  ];
  const screenPoint = (node: Junction) => ({
    x: (viewBoxWidth / 2 + pan.x + (node.x - viewBoxWidth / 2) * zoom) * pixelsPerUnit,
    y: (viewBoxHeight / 2 + pan.y + (node.y - viewBoxHeight / 2) * zoom) * pixelsPerUnit,
  });

  for (const node of candidates) {
    const screen = screenPoint(node);
    const width = Math.max(18, node.id.length * 7);
    for (const offset of offsets) {
      const x = node.x + offset.dx * unitsPerPixel;
      const y = node.y + offset.dy * unitsPerPixel;
      const labelScreenX = screen.x + offset.dx;
      const labelScreenY = screen.y + offset.dy;
      const left = offset.anchor === 'middle' ? labelScreenX - width / 2 : offset.anchor === 'start' ? labelScreenX : labelScreenX - width;
      const box = { left: left - 2, right: left + width + 2, top: labelScreenY - 12, bottom: labelScreenY + 3 };
      if (occupied.some((other) => box.left < other.right + 2 && box.right + 2 > other.left && box.top < other.bottom + 2 && box.bottom + 2 > other.top)) continue;
      occupied.push(box);
      placed.set(node.id, { x, y, anchor: offset.anchor });
      break;
    }
  }
  return placed;
}

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
  emptyNotice,
}: Props) {
  const network = useDashboard((state) => state.network);
  const pressuresBySensor = useDashboard((state) => state.pressuresBySensor);
  const localization = useDashboard((state) => state.localization);
  const alertActive = useDashboard((state) => state.alertActive);
  const scenario = useDashboard(selectCurrentScenario);
  const networkStatus = useDashboard((state) => state.resources.network);
  const retryResource = useDashboard((state) => state.retryResource);
  const mapFitRevision = useDashboard((state) => state.mapFitRevision);
  const [selectedJunctionId, setSelectedJunctionId] = useState<string | null>(null);
  const [hovered, setHovered] = useState<Junction | null>(null);
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [viewSize, setViewSize] = useState({ width: 0, height: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; panX: number; panY: number } | null>(null);

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
      return [{ id: pipe.id, from, to, diameter: pipe.diameter ?? 0, trunk: (pipe.diameter ?? 0) >= 18 }];
    });
  }, [network]);
  const suspicionMap = useMemo(
    () => new Map((localization?.scores ?? []).map((item) => [item.junctionId, item.score])),
    [localization],
  );
  const top3 = useMemo(
    () => [...(localization?.scores ?? [])].sort((a, b) => b.score - a.score).slice(0, 3),
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
  const viewBoxWidth = network?.viewBoxWidth ?? 100;
  const viewBoxHeight = network?.viewBoxHeight ?? 100;
  const pixelsPerUnit = Math.max(0.001, Math.min((viewSize.width || viewBoxWidth) / viewBoxWidth, (viewSize.height || viewBoxHeight) / viewBoxHeight));
  const screenUnit = 1 / (pixelsPerUnit * zoom);
  const labelPlacements = useMemo(
    () => placeLabels(network?.junctions ?? [], sensorSet, rankMap, selectedJunctionId, hovered?.id ?? null, screenUnit, pixelsPerUnit, viewBoxWidth, viewBoxHeight, zoom, pan),
    [network, sensorSet, rankMap, selectedJunctionId, hovered?.id, screenUnit, pixelsPerUnit, viewBoxWidth, viewBoxHeight, zoom, pan],
  );

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setViewSize({ width, height });
      setZoom(1);
      setPan({ x: 0, y: 0 });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [networkStatus.loading, network]);

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [mapFitRevision]);

  function zoomAt(nextZoom: number, clientX?: number, clientY?: number) {
    const bounded = Math.max(0.75, Math.min(4, nextZoom));
    if (clientX === undefined || clientY === undefined || !svgRef.current || bounded === zoom) {
      setZoom(bounded);
      return;
    }
    const svgPoint = svgRef.current.createSVGPoint();
    svgPoint.x = clientX;
    svgPoint.y = clientY;
    const matrix = svgRef.current.getScreenCTM();
    if (!matrix) return;
    const point = svgPoint.matrixTransform(matrix.inverse());
    const centerX = viewBoxWidth / 2;
    const centerY = viewBoxHeight / 2;
    const worldX = (point.x - pan.x - centerX * (1 - zoom)) / zoom;
    const worldY = (point.y - pan.y - centerY * (1 - zoom)) / zoom;
    setPan({ x: point.x - centerX * (1 - bounded) - worldX * bounded, y: point.y - centerY * (1 - bounded) - worldY * bounded });
    setZoom(bounded);
  }

  function beginPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target instanceof Element && event.target.closest('.map-node'))) return;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function movePan(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setPan({ x: drag.panX + (event.clientX - drag.x) / pixelsPerUnit, y: drag.panY + (event.clientY - drag.y) / pixelsPerUnit });
  }

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

  if (networkStatus.loading) return <div className="panel map-shell skeleton">Loading network…</div>;
  if (networkStatus.error || !network) return <div className="panel map-shell error-state" role="alert"><span>{networkStatus.error ?? 'Network unavailable'}</span><button type="button" onClick={() => void retryResource('network')}>Retry</button></div>;

  return (
    <section className="panel map-shell map-stage">
      <div className="map-stage-head">
        <div className="map-heading">
          <div className="eyebrow">NET3 / DISTRIBUTION</div>
          <h2>{title}</h2>
          <span className="map-count">{network.sensors.length} sensors <i>·</i> {network.junctions.length} nodes <i>·</i> {network.pipes.length} links</span>
        </div>
        <div className="map-actions">
          {showLocalization && alertActive && <button type="button" className={`map-toggle ${heatmapEnabled ? 'is-on' : ''}`} onClick={() => onHeatmapChange?.(!heatmapEnabled)} aria-pressed={heatmapEnabled}>Relative suspicion <kbd>G</kbd></button>}
          {showLocalization && alertActive && demoMode && <button type="button" className={`map-toggle reveal-toggle ${revealLeak ? 'is-on' : ''}`} onClick={() => onRevealLeakChange?.(!revealLeak)} aria-pressed={revealLeak}>Reveal true leak <kbd>L</kbd></button>}
          <div className="map-zoom"><button type="button" aria-label="Zoom out" onClick={() => zoomAt(zoom - 0.25)} disabled={zoom <= 0.75}><Minus size={16} /></button><span>{Math.round(zoom * 100)}%</span><button type="button" aria-label="Zoom in" onClick={() => zoomAt(zoom + 0.25)} disabled={zoom >= 4}><Plus size={16} /></button><button type="button" className="fit-button" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</button></div>
        </div>
      </div>

      {top3.length > 0 && showLocalization && alertActive && <div className="suspect-strip" aria-label="Ranked suspect nodes"><span className="eyebrow">RANKED NODES</span>{top3.map((item, index) => <button type="button" key={item.junctionId} className={`suspect-rank suspect-${index + 1}`} onClick={() => setSelectedJunctionId(item.junctionId)}><b>#{index + 1}</b><span>{item.junctionId}</span></button>)}</div>}

      <div
        className="map-canvas map-viewport"
        ref={canvasRef}
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={(event) => { if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null; }}
        onPointerCancel={() => { dragRef.current = null; }}
        onWheel={(event) => { event.preventDefault(); zoomAt(zoom + (event.deltaY < 0 ? 0.15 : -0.15), event.clientX, event.clientY); }}
        style={{ touchAction: 'none', cursor: dragRef.current ? 'grabbing' : zoom > 1 ? 'grab' : 'crosshair' }}
      >
        <svg ref={svgRef} viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`} role="img" aria-label="Water distribution network map" preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="graticule" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#263238" strokeWidth="0.13" />
              <path d="M 10 0 L 10 20 M 0 10 L 20 10" fill="none" stroke="#1d292d" strokeWidth="0.08" strokeDasharray="0.5 0.8" />
            </pattern>
          </defs>
          <rect width={viewBoxWidth} height={viewBoxHeight} fill="url(#graticule)" />
          <g className="map-zoom-layer" transform={`translate(${pan.x + viewBoxWidth / 2 - (viewBoxWidth / 2) * zoom} ${pan.y + viewBoxHeight / 2 - (viewBoxHeight / 2) * zoom}) scale(${zoom})`}>
            {heatmapEnabled && showLocalization && alertActive && localization && network.junctions.map((junction) => {
              const suspicion = suspicionMap.get(junction.id) ?? 0;
              if (suspicion < 0.008) return null;
              return <circle key={`heat-${junction.id}`} cx={junction.x} cy={junction.y} r={(2 + Math.min(8, suspicion * 18)) * screenUnit} fill="#56b9ac" opacity={Math.min(0.32, suspicion * 2.5)} className="heat-cell" />;
            })}
            {pipeEndpoints.map(({ id, from, to, diameter, trunk }) => (
              <g key={id} className={`pipe-group ${trunk ? 'pipe-main' : diameter >= 11 ? 'pipe-secondary' : 'pipe-branch'}`}>
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
                  rank={showLocalization && alertActive ? rankMap.get(junction.id) : undefined}
                  leak={demoMode && revealLeak && scenario?.leakNode === junction.id}
                  screenUnit={screenUnit}
                  label={labelPlacements.get(junction.id)}
                  onHover={setHovered}
                  onSelect={(node) => {
                    setSelectedJunctionId(node.id);
                    if (network.sensors.includes(node.id)) onSelectSensor?.(node.id);
                  }}
                />
              );
            })}
            {hovered && <g className="map-crosshair"><line x1={hovered.x} y1="0" x2={hovered.x} y2={viewBoxHeight} /><line x1="0" y1={hovered.y} x2={viewBoxWidth} y2={hovered.y} /></g>}
          </g>
        </svg>
        {emptyNotice && <div className="map-data-notice" role="status">{emptyNotice}</div>}

        <div className="map-orientation"><span className="north-arrow">↑</span><span>N</span></div>
        <div className="map-scale"><i /><span>0</span><span>schematic</span></div>
        {hovered && <div className="map-hover-hud" role="status"><b>{hovered.id}</b><span>GRID {hovered.x.toFixed(1)} / {hovered.y.toFixed(1)}</span><span>{network.sensors.includes(hovered.id) ? 'SENSOR' : 'JUNCTION'}</span><span>{latest[hovered.id] ? `P ${latest[hovered.id]!.pressureM.toFixed(1)} m` : 'P —'}</span><span>Δ {latest[hovered.id] ? `${(latest[hovered.id]!.pressureM - scenarioBase).toFixed(1)} m` : '—'}</span><span>{showLocalization && alertActive && rankMap.has(hovered.id) ? `RANK #${rankMap.get(hovered.id)}` : 'RANK —'}</span></div>}
        {selectedJunction && <aside className="node-inspector" aria-label={`${selectedJunction.id} inspector`}>
          <div className="inspector-head"><div><span className="eyebrow">NODE INSPECTOR</span><h3>{selectedJunction.id}</h3></div><button type="button" className="icon-button" onClick={() => setSelectedJunctionId(null)} aria-label="Close inspector"><X size={17} /></button></div>
          <button type="button" className="copy-id" onClick={() => void copyId(selectedJunction.id)}><Copy size={14} />{copied ? 'Copied' : 'Copy ID'}</button>
          <dl className="inspector-data">
            <div><dt>Type</dt><dd>{network.sensors.includes(selectedJunction.id) ? 'Sensor / junction' : 'Junction'}</dd></div>
            <div><dt>Elevation</dt><dd>{selectedJunction.elevationM.toFixed(1)} m</dd></div>
            <div><dt>Pressure</dt><dd>{selectedReading ? `${selectedReading.pressureM.toFixed(1)} m` : '—'}</dd></div>
            <div><dt>Change</dt><dd className={selectedDelta !== undefined && selectedDelta <= pressureDelta.loss ? 'value-warn' : ''}>{selectedDelta === undefined ? '—' : `${selectedDelta > 0 ? '+' : ''}${selectedDelta.toFixed(1)} m`}</dd></div>
            {showLocalization && alertActive && rankMap.has(selectedJunction.id) && <div><dt>Suspect rank</dt><dd>#{rankMap.get(selectedJunction.id)}</dd></div>}
            <div><dt>Connected</dt><dd>{neighbors.length} links</dd></div>
          </dl>
          <div className="inspector-links"><span>NEIGHBOURS</span><div>{neighbors.slice(0, 8).map((id) => <button key={id} type="button" onClick={() => setSelectedJunctionId(id)}>{id}</button>)}</div></div>
          <div className="inspector-links"><span>NEAREST SENSORS</span><div>{nearestSensors.map((id) => <button key={id} type="button" onClick={() => { setSelectedJunctionId(id); onSelectSensor?.(id); }}>{id}</button>)}</div></div>
        </aside>}
      </div>

      <div className="map-footer">
        <div className="map-legends"><span><i className="legend-dot low" />Drop ≤ {pressureDelta.loss} m</span><span><i className="legend-dot stable" />Nominal</span><span><i className="legend-dot high" />Rise ≥ +{pressureDelta.rise} m</span>
          {heatmapEnabled && showLocalization && alertActive && localization && <span className="suspicion-key"><i />RELATIVE SUSPICION</span>}
        </div>
        <span className="map-data-state"><i className={`live-dot ${hasPressure ? 'active' : ''}`} />{hasPressure ? 'PRESSURE VALUES / SENSORS ONLY' : 'NO PRESSURE SAMPLE'}</span>
      </div>
      <span className="map-coordinate"><LocateFixed size={13} /> {hovered ? `${hovered.x.toFixed(1)} / ${hovered.y.toFixed(1)}` : 'GRID REF —'}</span>
      {subtitle && <span className="visually-hidden">{subtitle}</span>}
    </section>
  );
}
