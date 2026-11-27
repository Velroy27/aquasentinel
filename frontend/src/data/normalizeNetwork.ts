import type { Network, Scenario } from './types';

export function normalizeNetwork(value: unknown): Network {
  if (typeof value !== 'object' || value === null) throw new Error('Invalid network response');
  const raw = value as { nodes?: unknown; junctions?: unknown; pipes?: unknown; sensors?: unknown };
  const nodes = raw.junctions ?? raw.nodes;
  if (!Array.isArray(nodes) || !Array.isArray(raw.pipes) || !Array.isArray(raw.sensors)) {
    throw new Error('Invalid network response');
  }
  const junctions = nodes.map((entry) => {
      const node = entry as Record<string, unknown>;
      return {
        id: String(node.id),
        x: Number(node.x),
        y: Number(node.y),
        elevationM: Number(node.elevationM ?? node.elevation),
      };
    });

  let viewBoxWidth = 100;
  let viewBoxHeight = 100;
  // EPANET coordinates use a bottom-left origin. Convert them to a centred,
  // uniformly scaled SVG plane with a 7% gutter on every side. Match the
  // viewBox aspect ratio to the network so the map uses the panel efficiently.
  if (junctions.length) {
    const minX = Math.min(...junctions.map((node) => node.x));
    const maxX = Math.max(...junctions.map((node) => node.x));
    const minY = Math.min(...junctions.map((node) => node.y));
    const maxY = Math.max(...junctions.map((node) => node.y));
    const rangeX = maxX - minX;
    const rangeY = maxY - minY;
    const aspect = rangeX > 0 && rangeY > 0 ? rangeX / rangeY : rangeX > 0 ? 100 : rangeY > 0 ? 0.01 : 1;
    viewBoxWidth = aspect >= 1 ? 100 : 100 * aspect;
    viewBoxHeight = aspect >= 1 ? 100 / aspect : 100;
    const scaleX = rangeX > 0 ? viewBoxWidth * 0.86 / rangeX : Number.POSITIVE_INFINITY;
    const scaleY = rangeY > 0 ? viewBoxHeight * 0.86 / rangeY : Number.POSITIVE_INFINITY;
    const candidateScale = Math.min(scaleX, scaleY);
    const scale = Number.isFinite(candidateScale) ? candidateScale : 1;
    const width = rangeX * scale;
    const height = rangeY * scale;
    const offsetX = (viewBoxWidth - width) / 2;
    const offsetY = (viewBoxHeight - height) / 2;
    for (const node of junctions) {
      node.x = offsetX + (node.x - minX) * scale;
      node.y = offsetY + (maxY - node.y) * scale;
    }
  }

  return {
    junctions,
    pipes: raw.pipes.map((entry) => {
      const pipe = entry as Record<string, unknown>;
      return {
        id: String(pipe.id ?? `${pipe.from}-${pipe.to}`),
        from: String(pipe.from),
        to: String(pipe.to),
        length: typeof pipe.length === 'number' ? pipe.length : undefined,
        diameter: typeof pipe.diameter === 'number' ? pipe.diameter : undefined,
      };
    }),
    sensors: raw.sensors.map(String),
    viewBoxWidth,
    viewBoxHeight,
  };
}

export function normalizeScenarios(value: unknown): Scenario[] {
  if (!Array.isArray(value)) throw new Error('Invalid scenarios response');
  return value.map((entry) => {
    const scenario = entry as Record<string, unknown>;
    const start = Number(scenario.start_t ?? 0);
    return {
      id: String(scenario.id ?? scenario.scenario_id),
      name: String(scenario.name ?? scenario.description ?? scenario.id ?? scenario.scenario_id),
      leakNode: scenario.leak_node === null || scenario.leak_node === 'null' ? '' : String(scenario.leak_node ?? ''),
      startedAt: new Date(start * 1000).toISOString(),
      leakLitresPerSecond: Number(scenario.leak_size_lps ?? 0),
      basePressureM: Number(scenario.base_pressure_m ?? 52),
    };
  });
}
