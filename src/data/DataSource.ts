import type { DataHandler, Network, Placement, Results, Scenario, StatusHandler } from './types';

export interface DataSource {
  getNetwork(): Promise<Network>;
  getResults(): Promise<Results>;
  getPlacement(): Promise<Placement[]>;
  getScenarios(): Promise<Scenario[]>;
  startScenario(id: string, speed: 1 | 5 | 20): void;
  stop(): void;
  reset(): void;
  subscribe(handler: DataHandler): () => void;
  onStatus(handler: StatusHandler): () => void;
}

export function isStreamMessage(value: unknown): value is import('./types').StreamMessage {
  if (typeof value !== 'object' || value === null) return false;
  const message = value as Record<string, unknown>;
  if (typeof message.timestamp !== 'string' || Number.isNaN(Date.parse(message.timestamp))) return false;

  if (message.type === 'pressure') {
    const point = message.point as Record<string, unknown> | undefined;
    return Boolean(point)
      && typeof point?.sensorId === 'string'
      && typeof point?.timestamp === 'string'
      && typeof point?.pressureM === 'number'
      && typeof point?.anomaly === 'boolean';
  }

  if (message.type === 'alert') {
    return typeof message.id === 'string'
      && typeof message.sensorId === 'string'
      && typeof message.score === 'number'
      && (message.severity === 'critical' || message.severity === 'warning')
      && typeof message.message === 'string';
  }

  if (message.type === 'localization') {
    return typeof message.scenarioId === 'string'
      && Array.isArray(message.probabilities)
      && message.probabilities.every((item: unknown) => {
        if (typeof item !== 'object' || item === null) return false;
        const candidate = item as Record<string, unknown>;
        return typeof candidate.junctionId === 'string' && typeof candidate.probability === 'number';
      });
  }

  return false;
}
