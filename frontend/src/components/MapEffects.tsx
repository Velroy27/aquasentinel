import { memo, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { mapEffectMotion, pressureDelta, pressureScale } from '../config/settings';
import type { Junction, PressurePoint } from '../data/types';

type PipeEndpoint = { id: string; from: Junction; to: Junction };
type Props = {
  junctions: Junction[];
  sensors: Junction[];
  pipes: PipeEndpoint[];
  latest: Record<string, PressurePoint | undefined>;
  scenarioBase: number;
  screenUnit: number;
  running: boolean;
  enabled: boolean;
  alertActive: boolean;
  suspectId?: string;
  leakNode?: string | null;
  leakRate?: number | null;
};

type PressureState = 'normal' | 'low-mild' | 'low-strong' | 'high';

export function pressureStateForDelta(delta?: number): PressureState {
  if (delta === undefined) return 'normal';
  if (delta <= pressureDelta.strongLoss) return 'low-strong';
  if (delta < pressureDelta.loss) return 'low-mild';
  if (delta >= pressureDelta.rise) return 'high';
  return 'normal';
}

function pulseDuration(state: PressureState): number {
  if (state === 'low-strong') return mapEffectMotion.lowStrongMs;
  if (state === 'low-mild') return mapEffectMotion.lowMildMs;
  return mapEffectMotion.highMs;
}

export const MapEffects = memo(function MapEffects({
  junctions, sensors, pipes, latest, scenarioBase, screenUnit, running, enabled, alertActive,
  suspectId, leakNode, leakRate,
}: Props) {
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const updateVisibility = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', updateVisibility);
    return () => document.removeEventListener('visibilitychange', updateVisibility);
  }, []);

  const active = enabled && running && visible;
  const animationReady = enabled && visible;
  const sensorStates = useMemo(() => sensors.map((sensor) => {
    const reading = latest[sensor.id];
    return { sensor, state: pressureStateForDelta(reading ? reading.pressureM - scenarioBase : undefined) };
  }), [sensors, latest, scenarioBase]);
  const sensorStateById = useMemo(() => new Map(sensorStates.map(({ sensor, state }) => [sensor.id, state])), [sensorStates]);
  const flowStates = useMemo(() => pipes.map((pipe) => {
    const fromState = sensorStateById.get(pipe.from.id) ?? 'normal';
    const toState = sensorStateById.get(pipe.to.id) ?? 'normal';
    const state: PressureState = fromState === 'low-strong' || toState === 'low-strong' ? 'low-strong'
      : fromState === 'low-mild' || toState === 'low-mild' ? 'low-mild'
        : fromState === 'high' || toState === 'high' ? 'high' : 'normal';
    return { pipe, state };
  }), [pipes, sensorStateById]);

  const trueLeakVisible = alertActive && leakNode;
  const intensity = alertActive && leakRate !== null && leakRate !== undefined
    ? Math.min(1.25, Math.max(0.85, 0.85 + leakRate / mapEffectMotion.leakRateScale))
    : 1;
  const dropSize = mapEffectMotion.dropletSizePx * screenUnit;
  const dropletPath = `M 0 0 C -${dropSize * .25} -${dropSize * .45} ${dropSize * .5} -${dropSize * .7} 0 -${dropSize} C -${dropSize * .5} -${dropSize * .7} ${dropSize * .25} -${dropSize * .45} 0 0 Z`;

  return (
    <g
      className={`map-effects ${enabled ? 'effects-enabled' : ''} ${animationReady ? 'effects-animated' : ''} ${animationReady && !running ? 'effects-paused' : ''} ${active ? 'effects-running' : ''} ${alertActive ? 'alert-active' : ''}`}
      style={{
        '--effect-low': pressureScale.low,
        '--effect-high': pressureScale.high,
        '--effect-transition': `${mapEffectMotion.transitionMs}ms`,
        '--leak-cycle': `${mapEffectMotion.leakCycleMs}ms`,
        '--drop-rise': `${mapEffectMotion.dropletRisePx * screenUnit}px`,
        '--drop-arc': `${mapEffectMotion.dropletArcPx * screenUnit}px`,
        '--puddle-max-scale': mapEffectMotion.puddleMaxPx / mapEffectMotion.puddleStartPx,
        '--leak-opacity': Math.min(1, intensity),
      } as CSSProperties}
      aria-hidden="true"
    >
      {active && flowStates.map(({ pipe, state }, index) => (
        <line
          key={`flow-${pipe.id}`}
          x1={pipe.from.x} y1={pipe.from.y} x2={pipe.to.x} y2={pipe.to.y}
          className={`pipe-flow flow-${state} ${index < mapEffectMotion.maxAnimatedPipes ? 'flow-animated' : ''}`}
          style={{
            animationDuration: `${state === 'high' ? mapEffectMotion.flowHighMs : state.startsWith('low') ? mapEffectMotion.flowLowMs : mapEffectMotion.flowNormalMs}ms`,
            animationDelay: `${(index % mapEffectMotion.flowStaggerSlots) * -mapEffectMotion.flowStaggerMs}ms`,
            strokeDasharray: `${mapEffectMotion.flowDashPx}px ${mapEffectMotion.flowGapPx}px`,
            '--flow-distance': `${-mapEffectMotion.flowDashOffsetPx}px`,
          } as CSSProperties}
        />
      ))}

      {sensorStates.map(({ sensor, state }) => (
        <g key={`pressure-${sensor.id}`} className={`pressure-effect pressure-${state}`} transform={`translate(${sensor.x} ${sensor.y})`}>
          <circle r={mapEffectMotion.pressureRingPx * screenUnit} className="pressure-pulse" style={{ animationDuration: `${pulseDuration(state)}ms` }} />
        </g>
      ))}

      {enabled && alertActive && suspectId && (
        (() => {
          const node = junctions.find((junction) => junction.id === suspectId);
          return node ? <g key={`suspect-${suspectId}`} className="suspect-effect" transform={`translate(${node.x} ${node.y})`}><circle r={mapEffectMotion.suspectRingPx * screenUnit} style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms` }} /></g> : null;
        })()
      )}

      {enabled && trueLeakVisible && (() => {
        const node = junctions.find((junction) => junction.id === leakNode);
        if (!node) return null;
        return (
          <g key={`leak-${leakNode}`} className="leak-effect" transform={`translate(${node.x} ${node.y})`} style={{ '--leak-intensity': intensity, '--puddle-growth': `${mapEffectMotion.puddleGrowthMs}ms` } as CSSProperties}>
            <ellipse rx={mapEffectMotion.puddleStartPx * screenUnit} ry={mapEffectMotion.puddleStartPx * 0.42 * screenUnit} className="leak-puddle" />
            <circle r={mapEffectMotion.leakRingPx * screenUnit} className="leak-wave leak-wave-a" style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms` }} />
            <circle r={mapEffectMotion.leakRingPx * screenUnit} className="leak-wave leak-wave-b" style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms`, animationDelay: `${-mapEffectMotion.leakCycleMs / 2}ms` }} />
            <path d={dropletPath} className="leak-drop drop-one" style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms` }} />
            <path d={dropletPath} className="leak-drop drop-two" style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms`, animationDelay: `${-mapEffectMotion.leakCycleMs / 3}ms` }} />
            <path d={dropletPath} className="leak-drop drop-three" style={{ animationDuration: `${mapEffectMotion.leakCycleMs}ms`, animationDelay: `${-mapEffectMotion.leakCycleMs * 2 / 3}ms` }} />
          </g>
        );
      })()}
    </g>
  );
});
