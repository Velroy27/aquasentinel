import { useMemo, useRef, useEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { pressureDelta, pressureScale, elevationExaggeration, view3DConfig } from '../../config/settings';
import type { Junction, Network, PressurePoint, Scenario } from '../../data/types';
import type { CameraPreset } from './types';

export function pressureColor(delta: number | undefined): string {
  if (delta === undefined) return pressureScale.normal;
  const neutral = { r: 119, g: 129, b: 125 };
  const low = { r: 188, g: 119, b: 86 };
  const high = { r: 99, g: 152, b: 140 };
  const amount = Math.min(1, Math.abs(delta) / (delta < 0 ? 5 : 4));
  const target = delta < 0 ? low : high;
  const r = Math.round(neutral.r + (target.r - neutral.r) * amount);
  const g = Math.round(neutral.g + (target.g - neutral.g) * amount);
  const b = Math.round(neutral.b + (target.b - neutral.b) * amount);
  return `rgb(${r}, ${g}, ${b})`;
}

export type PipeSegment = {
  id: string;
  fromId: string;
  toId: string;
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  midpoint: THREE.Vector3;
  length: number;
  quaternion: THREE.Quaternion;
  diameter: number;
  trunk: boolean;
};

type Props = {
  network: Network;
  scenario: Scenario | null;
  latest: Record<string, PressurePoint | undefined>;
  selectedJunctionId: string | null;
  hoveredJunctionId: string | null;
  onSelectJunction: (junction: Junction) => void;
  onHoverJunction: (junction: Junction | null) => void;
  running: boolean;
  effectsEnabled: boolean;
  alertActive: boolean;
  heatmapEnabled: boolean;
  revealLeak: boolean;
  demoMode: boolean;
  top3: { junctionId: string; score: number }[];
  cameraPreset: CameraPreset;
  onCameraPresetChange: (preset: CameraPreset) => void;
  autoOrbit: boolean;
  mapFitRevision: number;
  fpsVisible: boolean;
  onFpsUpdate?: (fps: number, ms: number) => void;
  hasElevationData: boolean;
};

// -------------------------------------------------------------
// Scene Controller: Camera management, transitions, & auto-fit
// -------------------------------------------------------------
function CameraController({
  network,
  cameraPreset,
  onCameraPresetChange,
  mapFitRevision,
  suspectPos,
  alertActive,
  autoOrbit,
}: {
  network: Network;
  cameraPreset: CameraPreset;
  onCameraPresetChange: (preset: CameraPreset) => void;
  mapFitRevision: number;
  suspectPos: THREE.Vector3 | null;
  alertActive: boolean;
  autoOrbit: boolean;
}) {
  const { camera } = useThree();
  const controlsRef = useRef<OrbitControlsImpl>(null);

  const targetCamPos = useRef<THREE.Vector3>(new THREE.Vector3(0, 65, 80));
  const targetLookAt = useRef<THREE.Vector3>(new THREE.Vector3(0, 5, 0));
  const isTransitioning = useRef(false);

  // Initial fit & Reset trigger
  useEffect(() => {
    targetCamPos.current.set(0, 65, 80);
    targetLookAt.current.set(0, 4, 0);
    isTransitioning.current = true;
  }, [mapFitRevision, network]);

  // Handle Preset changes
  useEffect(() => {
    if (cameraPreset === 'top') {
      targetCamPos.current.set(0, 115, 0.001);
      targetLookAt.current.set(0, 0, 0);
      isTransitioning.current = true;
    } else if (cameraPreset === 'angled') {
      targetCamPos.current.set(0, 65, 80);
      targetLookAt.current.set(0, 4, 0);
      isTransitioning.current = true;
    } else if (cameraPreset === 'follow') {
      if (suspectPos && alertActive) {
        targetCamPos.current.set(suspectPos.x, suspectPos.y + 16, suspectPos.z + 22);
        targetLookAt.current.set(suspectPos.x, suspectPos.y, suspectPos.z);
        isTransitioning.current = true;
      }
    }
  }, [cameraPreset, suspectPos, alertActive]);

  useFrame((_, delta) => {
    if (isTransitioning.current && controlsRef.current) {
      const step = Math.min(1, delta * 3.5);
      camera.position.lerp(targetCamPos.current, step);
      controlsRef.current.target.lerp(targetLookAt.current, step);
      controlsRef.current.update();

      if (
        camera.position.distanceTo(targetCamPos.current) < 0.2 &&
        controlsRef.current.target.distanceTo(targetLookAt.current) < 0.2
      ) {
        camera.position.copy(targetCamPos.current);
        controlsRef.current.target.copy(targetLookAt.current);
        isTransitioning.current = false;
      }
    }
  });

  return (
    <OrbitControls
      ref={controlsRef}
      enableDamping
      dampingFactor={0.08}
      maxPolarAngle={Math.PI / 2 - 0.05}
      minDistance={10}
      maxDistance={220}
      autoRotate={autoOrbit}
      autoRotateSpeed={view3DConfig.autoOrbitSpeed}
      onStart={() => {
        if (isTransitioning.current) isTransitioning.current = false;
        if (cameraPreset !== 'custom') onCameraPresetChange('custom');
      }}
    />
  );
}

// -------------------------------------------------------------
// Nodes Mesh: InstancedMesh for regular nodes & sensor meshes
// -------------------------------------------------------------
function NetworkNodes({
  junctions,
  sensorsSet,
  nodePosMap,
  latest,
  scenarioBase,
  selectedId,
  hoveredId,
  rankMap,
  onSelect,
  onHover,
}: {
  junctions: Junction[];
  sensorsSet: Set<string>;
  nodePosMap: Map<string, THREE.Vector3>;
  latest: Record<string, PressurePoint | undefined>;
  scenarioBase: number;
  selectedId: string | null;
  hoveredId: string | null;
  rankMap: Map<string, number>;
  onSelect: (j: Junction) => void;
  onHover: (j: Junction | null) => void;
}) {
  const regularNodes = useMemo(() => junctions.filter((j) => !sensorsSet.has(j.id)), [junctions, sensorsSet]);
  const sensorNodes = useMemo(() => junctions.filter((j) => sensorsSet.has(j.id)), [junctions, sensorsSet]);

  const regularMeshRef = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  useEffect(() => {
    if (!regularMeshRef.current) return;
    const mesh = regularMeshRef.current;
    regularNodes.forEach((node, i) => {
      const pos = nodePosMap.get(node.id) || new THREE.Vector3();
      dummy.position.copy(pos);
      const isSelected = node.id === selectedId;
      const isHovered = node.id === hoveredId;
      const scale = isSelected ? 1.6 : isHovered ? 1.3 : 1.0;
      dummy.scale.set(scale, scale, scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      // Color
      const reading = latest[node.id];
      const delta = reading ? reading.pressureM - scenarioBase : undefined;
      const color = new THREE.Color(pressureColor(delta));
      mesh.setColorAt(i, color);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [regularNodes, nodePosMap, selectedId, hoveredId, latest, scenarioBase, dummy]);

  return (
    <group>
      {/* Regular junctions instanced */}
      <instancedMesh
        ref={regularMeshRef}
        args={[undefined, undefined, regularNodes.length]}
        onPointerOver={(e) => {
          e.stopPropagation();
          const instanceId = e.instanceId;
          if (instanceId !== undefined && regularNodes[instanceId]) {
            onHover(regularNodes[instanceId]);
          }
        }}
        onPointerOut={(e) => {
          e.stopPropagation();
          onHover(null);
        }}
        onClick={(e) => {
          e.stopPropagation();
          const instanceId = e.instanceId;
          if (instanceId !== undefined && regularNodes[instanceId]) {
            onSelect(regularNodes[instanceId]);
          }
        }}
      >
        <sphereGeometry args={[0.7, 12, 12]} />
        <meshStandardMaterial roughness={0.4} metalness={0.2} />
      </instancedMesh>

      {/* Sensor Nodes: Distinct Octahedron / Diamond markers */}
      {sensorNodes.map((sensor) => {
        const pos = nodePosMap.get(sensor.id) || new THREE.Vector3();
        const reading = latest[sensor.id];
        const delta = reading ? reading.pressureM - scenarioBase : undefined;
        const color = pressureColor(delta);
        const isSelected = sensor.id === selectedId;
        const isHovered = sensor.id === hoveredId;
        const rank = rankMap.get(sensor.id);

        return (
          <group key={sensor.id} position={pos}>
            <mesh
              onPointerOver={(e) => {
                e.stopPropagation();
                onHover(sensor);
              }}
              onPointerOut={(e) => {
                e.stopPropagation();
                onHover(null);
              }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(sensor);
              }}
              scale={isSelected ? [1.6, 1.6, 1.6] : isHovered ? [1.3, 1.3, 1.3] : [1, 1, 1]}
            >
              <octahedronGeometry args={[1.5, 0]} />
              <meshStandardMaterial color={color} roughness={0.3} metalness={0.3} />
            </mesh>

            {/* Selection halo */}
            {isSelected && (
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[2.2, 2.6, 24]} />
                <meshBasicMaterial color="#dce9e5" side={THREE.DoubleSide} />
              </mesh>
            )}

            {/* Sensor ID Label via drei Html */}
            <Html position={[0, 2.0, 0]} center distanceFactor={85} zIndexRange={[10, 0]}>
              <div
                className={`node-label sensor-label ${isSelected ? 'selected' : ''}`}
                style={{
                  fontSize: '11px',
                  fontFamily: 'var(--font-number)',
                  color: '#ffffff',
                  background: 'rgba(16, 23, 24, 0.82)',
                  padding: '1px 5px',
                  borderRadius: '2px',
                  border: isSelected ? '1px solid #dce9e5' : '1px solid #3a4845',
                  pointerEvents: 'none',
                  whiteSpace: 'nowrap',
                }}
              >
                {sensor.id}
              </div>
            </Html>

            {/* Suspect Rank Badge if sensor is a suspect */}
            {rank && (
              <Html position={[0, 3.8, 0]} center distanceFactor={85} zIndexRange={[20, 0]}>
                <div
                  className={`suspect-rank suspect-${rank}`}
                  style={{ transform: 'scale(0.85)', pointerEvents: 'none' }}
                >
                  <b>#{rank}</b>
                </div>
              </Html>
            )}
          </group>
        );
      })}

      {/* Non-sensor suspect badges or selected non-sensor labels */}
      {regularNodes.map((node) => {
        const rank = rankMap.get(node.id);
        const isSelected = node.id === selectedId;
        if (!rank && !isSelected) return null;
        const pos = nodePosMap.get(node.id) || new THREE.Vector3();

        return (
          <group key={`label-${node.id}`} position={pos}>
            {isSelected && (
              <Html position={[0, 1.8, 0]} center distanceFactor={85} zIndexRange={[10, 0]}>
                <div
                  style={{
                    fontSize: '11px',
                    fontFamily: 'var(--font-number)',
                    color: '#dce9e5',
                    background: 'rgba(16, 23, 24, 0.85)',
                    padding: '1px 5px',
                    borderRadius: '2px',
                    border: '1px solid #dce9e5',
                    pointerEvents: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {node.id}
                </div>
              </Html>
            )}
            {rank && (
              <Html position={[0, isSelected ? 3.4 : 2.4, 0]} center distanceFactor={85} zIndexRange={[20, 0]}>
                <div
                  className={`suspect-rank suspect-${rank}`}
                  style={{ transform: 'scale(0.85)', pointerEvents: 'none' }}
                >
                  <b>#{rank}</b>
                </div>
              </Html>
            )}
          </group>
        );
      })}
    </group>
  );
}

// -------------------------------------------------------------
// Pipes Mesh: Instanced cylinders connecting endpoints
// -------------------------------------------------------------
function NetworkPipes({
  pipes,
  latest,
  scenarioBase,
  effectsEnabled,
}: {
  pipes: PipeSegment[];
  latest: Record<string, PressurePoint | undefined>;
  scenarioBase: number;
  effectsEnabled: boolean;
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  useEffect(() => {
    if (!meshRef.current) return;
    const mesh = meshRef.current;

    pipes.forEach((pipe, i) => {
      dummy.position.copy(pipe.midpoint);
      dummy.quaternion.copy(pipe.quaternion);

      // Determine pressure state
      const fromP = latest[pipe.fromId]?.pressureM;
      const toP = latest[pipe.toId]?.pressureM;
      const fromDelta = fromP !== undefined ? fromP - scenarioBase : undefined;
      const toDelta = toP !== undefined ? toP - scenarioBase : undefined;

      const isLow = (fromDelta !== undefined && fromDelta <= pressureDelta.loss) ||
                    (toDelta !== undefined && toDelta <= pressureDelta.loss);
      const isHigh = (fromDelta !== undefined && fromDelta >= pressureDelta.rise) ||
                     (toDelta !== undefined && toDelta >= pressureDelta.rise);

      // Thickness based on pipe category and pressure
      let radius = pipe.trunk ? 0.38 : pipe.diameter >= 11 ? 0.28 : 0.18;
      if (effectsEnabled) {
        if (isLow) radius *= 0.8;
        else if (isHigh) radius *= 1.25;
      }
      dummy.scale.set(radius, pipe.length, radius);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      // Pipe Color
      let color = new THREE.Color('#4d5755');
      if (effectsEnabled) {
        if (isLow) color = new THREE.Color('#384140');
        else if (isHigh) color = new THREE.Color('#a4bcb1');
      }
      mesh.setColorAt(i, color);
    });

    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [pipes, latest, scenarioBase, effectsEnabled, dummy]);

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, pipes.length]}>
      <cylinderGeometry args={[1, 1, 1, 8]} />
      <meshStandardMaterial roughness={0.5} metalness={0.1} />
    </instancedMesh>
  );
}

// -------------------------------------------------------------
// Flow Particles: InstancedMesh updated in single useFrame loop
// -------------------------------------------------------------
function FlowParticles({
  pipes,
  latest,
  scenarioBase,
  running,
  effectsEnabled,
  reducedMotion,
}: {
  pipes: PipeSegment[];
  latest: Record<string, PressurePoint | undefined>;
  scenarioBase: number;
  running: boolean;
  effectsEnabled: boolean;
  reducedMotion: boolean;
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const count = pipes.length * view3DConfig.particlesPerPipe;

  // Track progress t in [0, 1) per particle
  const particleOffsets = useMemo(() => {
    const arr = new Float32Array(count);
    for (let p = 0; p < pipes.length; p++) {
      for (let k = 0; k < view3DConfig.particlesPerPipe; k++) {
        arr[p * view3DConfig.particlesPerPipe + k] = (k / view3DConfig.particlesPerPipe) + Math.random() * 0.1;
      }
    }
    return arr;
  }, [pipes.length, count]);

  const dummy = useMemo(() => new THREE.Object3D(), []);

  useFrame((_, delta) => {
    if (!meshRef.current) return;
    const mesh = meshRef.current;
    const isPaused = !running || !effectsEnabled || reducedMotion || document.hidden;

    let idx = 0;
    for (let p = 0; p < pipes.length; p++) {
      const pipe = pipes[p];
      const fromP = latest[pipe.fromId]?.pressureM;
      const toP = latest[pipe.toId]?.pressureM;
      const fromDelta = fromP !== undefined ? fromP - scenarioBase : undefined;
      const toDelta = toP !== undefined ? toP - scenarioBase : undefined;

      const isLow = (fromDelta !== undefined && fromDelta <= pressureDelta.loss) ||
                    (toDelta !== undefined && toDelta <= pressureDelta.loss);
      const isHigh = (fromDelta !== undefined && fromDelta >= pressureDelta.rise) ||
                     (toDelta !== undefined && toDelta >= pressureDelta.rise);

      const speed = isHigh ? view3DConfig.flowSpeedHigh : isLow ? view3DConfig.flowSpeedLow : view3DConfig.flowSpeedNormal;

      for (let k = 0; k < view3DConfig.particlesPerPipe; k++) {
        if (!isPaused) {
          particleOffsets[idx] = (particleOffsets[idx] + delta * speed) % 1.0;
        }
        const t = particleOffsets[idx];

        if (isPaused) {
          dummy.scale.set(0, 0, 0);
        } else {
          dummy.position.lerpVectors(pipe.fromPos, pipe.toPos, t);
          dummy.scale.set(0.35, 0.35, 0.35);
        }
        dummy.updateMatrix();
        mesh.setMatrixAt(idx, dummy.matrix);
        idx++;
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]}>
      <sphereGeometry args={[1, 6, 6]} />
      <meshBasicMaterial color="#94c9c2" transparent opacity={view3DConfig.maxFlowOpacity} />
    </instancedMesh>
  );
}

// -------------------------------------------------------------
// Pressure Rings: Contracting (low) or Expanding (high) rings
// -------------------------------------------------------------
function SensorPressureRings({
  sensors,
  nodePosMap,
  latest,
  scenarioBase,
  effectsEnabled,
  running,
  reducedMotion,
}: {
  sensors: Junction[];
  nodePosMap: Map<string, THREE.Vector3>;
  latest: Record<string, PressurePoint | undefined>;
  scenarioBase: number;
  effectsEnabled: boolean;
  running: boolean;
  reducedMotion: boolean;
}) {
  const ringsRef = useRef<THREE.Group>(null);

  useFrame(({ clock }) => {
    if (!ringsRef.current || !effectsEnabled || !running || reducedMotion || document.hidden) return;
    const time = clock.getElapsedTime();

    ringsRef.current.children.forEach((child) => {
      const state = child.userData.state as 'low' | 'high' | undefined;
      if (!state) return;

      if (state === 'low') {
        // Inward contracting ring (period ~2.5s)
        const progress = (time / 2.5) % 1.0;
        const scale = 3.5 * (1.0 - progress) + 0.5;
        const opacity = Math.sin(progress * Math.PI) * 0.7;
        child.scale.set(scale, scale, 1);
        const mat = (child as THREE.Mesh).material as THREE.MeshBasicMaterial;
        if (mat) mat.opacity = opacity;
      } else if (state === 'high') {
        // Outward expanding ring (period ~1.2s)
        const progress = (time / 1.2) % 1.0;
        const scale = 0.5 + progress * 3.5;
        const opacity = (1.0 - progress) * 0.8;
        child.scale.set(scale, scale, 1);
        const mat = (child as THREE.Mesh).material as THREE.MeshBasicMaterial;
        if (mat) mat.opacity = opacity;
      }
    });
  });

  if (!effectsEnabled) return null;

  return (
    <group ref={ringsRef}>
      {sensors.map((sensor) => {
        const pos = nodePosMap.get(sensor.id);
        if (!pos) return null;
        const reading = latest[sensor.id];
        const delta = reading ? reading.pressureM - scenarioBase : undefined;
        if (delta === undefined) return null;

        const isLow = delta <= pressureDelta.loss;
        const isHigh = delta >= pressureDelta.rise;
        if (!isLow && !isHigh) return null;

        const color = isLow ? pressureScale.low : pressureScale.high;
        const state = isLow ? 'low' : 'high';

        return (
          <mesh
            key={`ring-${sensor.id}`}
            position={[pos.x, pos.y + 0.1, pos.z]}
            rotation={[-Math.PI / 2, 0, 0]}
            userData={{ state }}
          >
            <ringGeometry args={[0.9, 1.15, 24]} />
            <meshBasicMaterial color={color} transparent opacity={0.6} side={THREE.DoubleSide} />
          </mesh>
        );
      })}
    </group>
  );
}

// -------------------------------------------------------------
// Leak Effect: Expanding Ground Rings, Droplet Fountain, & Puddle
// -------------------------------------------------------------
function LeakEffect3D({
  leakPos,
  alertActive,
  isTrueLeak,
  effectsEnabled,
  running,
  reducedMotion,
}: {
  leakPos: THREE.Vector3 | null;
  alertActive: boolean;
  isTrueLeak: boolean;
  effectsEnabled: boolean;
  running: boolean;
  reducedMotion: boolean;
}) {
  const dropletsRef = useRef<THREE.InstancedMesh>(null);
  const puddleRef = useRef<THREE.Mesh>(null);
  const wavesRef = useRef<THREE.Group>(null);

  const DROPLET_COUNT = 24;
  const dummy = useMemo(() => new THREE.Object3D(), []);

  // Droplet trajectory seeds (staggered angles and velocities)
  const seeds = useMemo(() => {
    return Array.from({ length: DROPLET_COUNT }, (_, i) => ({
      angle: (i / DROPLET_COUNT) * Math.PI * 2 + (i % 2) * 0.2,
      speedH: 1.2 + (i % 4) * 0.35,
      speedY: 4.2 + (i % 3) * 0.6,
      phase: (i / DROPLET_COUNT),
    }));
  }, [DROPLET_COUNT]);

  const puddleScale = useRef(1.0);

  // Clear within 250ms on reset
  useEffect(() => {
    if (!alertActive) {
      puddleScale.current = 1.0;
    }
  }, [alertActive]);

  useFrame(({ clock }, delta) => {
    if (!leakPos || !alertActive || !effectsEnabled) return;
    const isPaused = !running || reducedMotion || document.hidden;
    const time = clock.getElapsedTime();

    // 1. Puddle disc growth (slowly capped)
    if (puddleRef.current) {
      if (!isPaused && puddleScale.current < 4.8) {
        puddleScale.current += delta * 0.08;
      }
      puddleRef.current.scale.set(puddleScale.current, puddleScale.current, 1);
    }

    // 2. Expanding ground waves
    if (wavesRef.current && !isPaused) {
      wavesRef.current.children.forEach((wave, idx) => {
        const offset = idx * 0.5;
        const progress = ((time / 2.1) + offset) % 1.0;
        const scale = 0.5 + progress * 4.5;
        const opacity = (1.0 - progress) * 0.6;
        wave.scale.set(scale, scale, 1);
        const mat = (wave as THREE.Mesh).material as THREE.MeshBasicMaterial;
        if (mat) mat.opacity = opacity;
      });
    }

    // 3. Droplet fountain animation
    if (dropletsRef.current) {
      const mesh = dropletsRef.current;
      if (isPaused) {
        for (let i = 0; i < DROPLET_COUNT; i++) {
          dummy.scale.set(0, 0, 0);
          dummy.updateMatrix();
          mesh.setMatrixAt(i, dummy.matrix);
        }
      } else {
        for (let i = 0; i < DROPLET_COUNT; i++) {
          const s = seeds[i];
          const tau = ((time * 1.3) + s.phase) % 1.0; // cycle in [0, 1)

          // Parabolic arc
          const dx = Math.cos(s.angle) * s.speedH * tau;
          const dz = Math.sin(s.angle) * s.speedH * tau;
          const dy = s.speedY * tau - 0.5 * 9.8 * tau * tau; // parabolic rise and fall

          if (dy < 0) {
            dummy.scale.set(0, 0, 0);
          } else {
            dummy.position.set(leakPos.x + dx, leakPos.y + dy, leakPos.z + dz);
            const scale = Math.sin(tau * Math.PI) * 0.35;
            dummy.scale.set(scale, scale, scale);
          }
          dummy.updateMatrix();
          mesh.setMatrixAt(i, dummy.matrix);
        }
      }
      mesh.instanceMatrix.needsUpdate = true;
    }
  });

  if (!alertActive || !leakPos || !effectsEnabled) return null;

  const color = isTrueLeak ? '#f0ad9e' : '#63c5ce';

  return (
    <group position={[leakPos.x, leakPos.y, leakPos.z]}>
      {/* Growing Ground Puddle */}
      <mesh ref={puddleRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <circleGeometry args={[1.0, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.22} side={THREE.DoubleSide} />
      </mesh>

      {/* Expanding ground wave rings */}
      <group ref={wavesRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.06, 0]}>
        <mesh>
          <ringGeometry args={[0.9, 1.1, 32]} />
          <meshBasicMaterial color={color} transparent opacity={0.5} side={THREE.DoubleSide} />
        </mesh>
        <mesh>
          <ringGeometry args={[0.9, 1.1, 32]} />
          <meshBasicMaterial color={color} transparent opacity={0.5} side={THREE.DoubleSide} />
        </mesh>
      </group>

      {/* Water Droplet Fountain */}
      <instancedMesh ref={dropletsRef} args={[undefined, undefined, DROPLET_COUNT]}>
        <sphereGeometry args={[1, 6, 6]} />
        <meshBasicMaterial color={color} />
      </instancedMesh>
    </group>
  );
}

// -------------------------------------------------------------
// Suspicion Heatmap: Soft ground-plane glows
// -------------------------------------------------------------
function SuspicionHeatmap3D({
  junctions,
  nodePosMap,
  suspicionMap,
  groundLevel,
  alertActive,
  heatmapEnabled,
}: {
  junctions: Junction[];
  nodePosMap: Map<string, THREE.Vector3>;
  suspicionMap: Map<string, number>;
  groundLevel: number;
  alertActive: boolean;
  heatmapEnabled: boolean;
}) {
  if (!alertActive || !heatmapEnabled) return null;

  return (
    <group position={[0, groundLevel + 0.05, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      {junctions.map((junction) => {
        const score = suspicionMap.get(junction.id) ?? 0;
        if (score < 0.008) return null;
        const pos = nodePosMap.get(junction.id);
        if (!pos) return null;

        const radius = (2.0 + Math.min(8.0, score * 18.0)) * 0.45;
        const opacity = Math.min(0.35, score * 2.5);

        return (
          <mesh key={`heat-${junction.id}`} position={[pos.x, -pos.z, 0]}>
            <circleGeometry args={[radius, 24]} />
            <meshBasicMaterial color="#56b9ac" transparent opacity={opacity} side={THREE.DoubleSide} />
          </mesh>
        );
      })}
    </group>
  );
}

// -------------------------------------------------------------
// FPS Tracker Hook inside Canvas
// -------------------------------------------------------------
function FpsTracker({
  active,
  onFpsUpdate,
}: {
  active: boolean;
  onFpsUpdate?: (fps: number, ms: number) => void;
}) {
  const frameCount = useRef(0);
  const totalDelta = useRef(0);
  const windowStart = useRef(performance.now());

  useFrame((_, delta) => {
    if (!active || !onFpsUpdate) return;
    frameCount.current += 1;
    totalDelta.current += delta;
    const now = performance.now();

    if (now - windowStart.current >= 500) {
      const fps = Math.round((frameCount.current * 1000) / (now - windowStart.current));
      const ms = Math.round((totalDelta.current / frameCount.current) * 10000) / 10;
      onFpsUpdate(fps, ms);
      frameCount.current = 0;
      totalDelta.current = 0;
      windowStart.current = now;
    }
  });

  return null;
}

// -------------------------------------------------------------
// Main Canvas Component Export
// -------------------------------------------------------------
export function Network3DCanvas({
  network,
  scenario,
  latest,
  selectedJunctionId,
  hoveredJunctionId,
  onSelectJunction,
  onHoverJunction,
  running,
  effectsEnabled,
  alertActive,
  heatmapEnabled,
  revealLeak,
  demoMode,
  top3,
  cameraPreset,
  onCameraPresetChange,
  autoOrbit,
  mapFitRevision,
  fpsVisible,
  onFpsUpdate,
  hasElevationData,
}: Props) {
  // Respect prefers-reduced-motion
  const reducedMotion = useMemo(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }, []);

  const viewBoxWidth = network.viewBoxWidth || 100;
  const viewBoxHeight = network.viewBoxHeight || 100;
  const cx = viewBoxWidth / 2;
  const cz = viewBoxHeight / 2;

  // Compute 3D node positions from the SAME 2D coordinates
  const nodePosMap = useMemo(() => {
    const map = new Map<string, THREE.Vector3>();
    network.junctions.forEach((j) => {
      const x = j.x - cx;
      const z = j.y - cz;
      const y = hasElevationData ? (j.elevationM || 0) * elevationExaggeration : 0;
      map.set(j.id, new THREE.Vector3(x, y, z));
    });
    return map;
  }, [network.junctions, cx, cz, hasElevationData]);

  // Compute lowest ground level
  const groundLevel = useMemo(() => {
    let minH = 0;
    nodePosMap.forEach((p) => {
      if (p.y < minH) minH = p.y;
    });
    return Math.min(-1.0, minH - 1.0);
  }, [nodePosMap]);

  // Compute Pipe Segments with orientation quaternions
  const pipeSegments = useMemo(() => {
    const segments: PipeSegment[] = [];
    const yAxis = new THREE.Vector3(0, 1, 0);

    network.pipes.forEach((pipe) => {
      const fromPos = nodePosMap.get(pipe.from);
      const toPos = nodePosMap.get(pipe.to);
      if (!fromPos || !toPos) return;

      const midpoint = new THREE.Vector3().addVectors(fromPos, toPos).multiplyScalar(0.5);
      const dir = new THREE.Vector3().subVectors(toPos, fromPos);
      const length = dir.length();
      if (length < 0.001) return;

      dir.normalize();
      const quaternion = new THREE.Quaternion().setFromUnitVectors(yAxis, dir);

      segments.push({
        id: pipe.id,
        fromId: pipe.from,
        toId: pipe.to,
        fromPos,
        toPos,
        midpoint,
        length,
        quaternion,
        diameter: pipe.diameter ?? 0,
        trunk: (pipe.diameter ?? 0) >= 18,
      });
    });
    return segments;
  }, [network.pipes, nodePosMap]);

  const sensorsSet = useMemo(() => new Set(network.sensors), [network.sensors]);
  const sensorJunctions = useMemo(() => network.junctions.filter((j) => sensorsSet.has(j.id)), [network.junctions, sensorsSet]);
  const rankMap = useMemo(() => new Map(top3.map((item, index) => [item.junctionId, index + 1])), [top3]);
  const scenarioBase = scenario?.basePressureM ?? 52;

  // Suspect & Leak coordinates
  const suspectNodeId = alertActive ? top3[0]?.junctionId : null;
  const suspectPos = suspectNodeId ? nodePosMap.get(suspectNodeId) ?? null : null;

  const leakNodeId = alertActive
    ? (demoMode ? (revealLeak ? scenario?.leakNode : undefined) : top3[0]?.junctionId)
    : undefined;
  const leakPos = leakNodeId ? nodePosMap.get(leakNodeId) ?? null : null;
  const isTrueLeak = Boolean(demoMode && revealLeak && scenario?.leakNode === leakNodeId);

  // Suspicion Map for heatmap
  const suspicionMap = useMemo(() => {
    return new Map(top3.map((t) => [t.junctionId, t.score]));
  }, [top3]);

  return (
    <Canvas
      dpr={[1, view3DConfig.maxDpr]}
      gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
      camera={{ position: view3DConfig.defaultCameraPosition, fov: 45 }}
      style={{ width: '100%', height: '100%', background: '#0d1418' }}
    >
      <fog attach="fog" args={['#0d1418', 60, 260]} />

      {/* Clean lights matching dark schematic palette */}
      <ambientLight intensity={0.7} color="#9ec2ce" />
      <directionalLight position={[60, 100, 40]} intensity={1.1} color="#f0f7f6" />
      <directionalLight position={[-60, -20, -40]} intensity={0.25} color="#15303b" />

      {/* Subtle Ground Grid */}
      <gridHelper
        args={[Math.max(viewBoxWidth, viewBoxHeight) * 1.5, 30, '#263238', '#141f23']}
        position={[0, groundLevel, 0]}
      />

      {/* Orbit Controls & Camera Transitions */}
      <CameraController
        network={network}
        cameraPreset={cameraPreset}
        onCameraPresetChange={onCameraPresetChange}
        mapFitRevision={mapFitRevision}
        suspectPos={suspectPos}
        alertActive={alertActive}
        autoOrbit={autoOrbit}
      />

      {/* FPS Tracker */}
      <FpsTracker active={fpsVisible} onFpsUpdate={onFpsUpdate} />

      {/* Suspicion Heatmap (Ground Glows) */}
      <SuspicionHeatmap3D
        junctions={network.junctions}
        nodePosMap={nodePosMap}
        suspicionMap={suspicionMap}
        groundLevel={groundLevel}
        alertActive={alertActive}
        heatmapEnabled={heatmapEnabled}
      />

      {/* Pipes (Instanced Cylinders) */}
      <NetworkPipes
        pipes={pipeSegments}
        latest={latest}
        scenarioBase={scenarioBase}
        effectsEnabled={effectsEnabled}
      />

      {/* Flow Particles (Instanced Mesh) */}
      <FlowParticles
        pipes={pipeSegments}
        latest={latest}
        scenarioBase={scenarioBase}
        running={running}
        effectsEnabled={effectsEnabled}
        reducedMotion={reducedMotion}
      />

      {/* Pressure Pulse Rings on Sensors */}
      <SensorPressureRings
        sensors={sensorJunctions}
        nodePosMap={nodePosMap}
        latest={latest}
        scenarioBase={scenarioBase}
        effectsEnabled={effectsEnabled}
        running={running}
        reducedMotion={reducedMotion}
      />

      {/* Nodes (Instanced Spheres & Octahedron Sensors) */}
      <NetworkNodes
        junctions={network.junctions}
        sensorsSet={sensorsSet}
        nodePosMap={nodePosMap}
        latest={latest}
        scenarioBase={scenarioBase}
        selectedId={selectedJunctionId}
        hoveredId={hoveredJunctionId}
        rankMap={rankMap}
        onSelect={onSelectJunction}
        onHover={onHoverJunction}
      />

      {/* Leak Fountain, Puddle, & Ground Waves */}
      <LeakEffect3D
        leakPos={leakPos}
        alertActive={alertActive}
        isTrueLeak={isTrueLeak}
        effectsEnabled={effectsEnabled}
        running={running}
        reducedMotion={reducedMotion}
      />
    </Canvas>
  );
}
