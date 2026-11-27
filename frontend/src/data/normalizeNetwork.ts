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
      elevationM: Number(node.elevationM ?? node.elevation ?? 0),
    };
  });

  const viewBoxWidth = 100;
  const viewBoxHeight = 100;

  // EPANET coordinates use a bottom-left origin (Y increases up).
  // Convert them to a centred, uniformly scaled SVG plane (Y increases down)
  // with 7% padding on all sides so nothing is clipped at any border.
  // Works whether input coordinates are raw EPANET or pre-normalized 0-100.
  if (junctions.length > 0) {
    const minX = Math.min(...junctions.map((node) => node.x));
    const maxX = Math.max(...junctions.map((node) => node.x));
    const minY = Math.min(...junctions.map((node) => node.y));
    const maxY = Math.max(...junctions.map((node) => node.y));
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    const padding = 0.07; // 7% padding (6-8% required)
    const usableW = viewBoxWidth * (1 - 2 * padding);
    const usableH = viewBoxHeight * (1 - 2 * padding);

    // Uniform scale preserving aspect ratio
    const scale = Math.min(usableW / rangeX, usableH / rangeY);
    const contentW = rangeX * scale;
    const contentH = rangeY * scale;

    const offsetX = (viewBoxWidth - contentW) / 2;
    const offsetY = (viewBoxHeight - contentH) / 2;

    for (const node of junctions) {
      node.x = Math.round((offsetX + (node.x - minX) * scale) * 100) / 100;
      node.y = Math.round((offsetY + (maxY - node.y) * scale) * 100) / 100; // Flip Y
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
