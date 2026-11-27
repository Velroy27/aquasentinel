import type { Placement, Results } from './types';

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function normalizePlacementResponse(value: unknown, count: number, results?: Results | null): Placement {
  const raw = record(value);
  if (!raw || !Array.isArray(raw.sensors)) throw new Error('Invalid placement response');
  const metrics = results?.placement.find((row) => row.sensorCount === count);
  const coverage = typeof raw.coverage === 'number' ? raw.coverage : metrics?.optimized ?? null;
  const randomSensors = Array.isArray(raw.random_sensors) ? raw.random_sensors.map(String) : null;
  return {
    sensorCount: count,
    optimizedCoverage: coverage,
    randomCoverage: metrics?.randomMean ?? null,
    optimizedSensors: raw.sensors.map(String),
    randomSensors,
  };
}

