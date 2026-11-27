import type { Network, Scenario } from './types';

export function normalizeNetwork(value: unknown): Network {
  if (typeof value !== 'object' || value === null) throw new Error('Invalid network response');
  const raw = value as { nodes?: unknown; junctions?: unknown; pipes?: unknown; sensors?: unknown };
  const nodes = raw.junctions ?? raw.nodes;
  if (!Array.isArray(nodes) || !Array.isArray(raw.pipes) || !Array.isArray(raw.sensors)) {
    throw new Error('Invalid network response');
  }
  return {
    junctions: nodes.map((entry) => {
      const node = entry as Record<string, unknown>;
      return {
        id: String(node.id),
        x: Number(node.x),
        y: Number(node.y),
        elevationM: Number(node.elevationM ?? node.elevation),
      };
    }),
    pipes: raw.pipes.map((entry) => {
      const pipe = entry as Record<string, unknown>;
      return { id: String(pipe.id ?? `${pipe.from}-${pipe.to}`), from: String(pipe.from), to: String(pipe.to) };
    }),
    sensors: raw.sensors.map(String),
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
