import type { BackendHealth, CostData, DataHandler, Network, Placement, Results, Scenario, StatusHandler } from './types';

export interface DataSource {
  getNetwork(): Promise<Network>;
  getResults(): Promise<Results>;
  getPlacement(results?: Results | null): Promise<Placement[]>;
  getCost(nodes?: number): Promise<CostData>;
  getScenarios(): Promise<Scenario[]>;
  getHealth(): Promise<BackendHealth | null>;
  startScenario(id: string, speed: 1 | 5 | 20): void;
  stop(): void;
  reset(): void;
  subscribe(handler: DataHandler): () => void;
  onStatus(handler: StatusHandler): () => void;
}

export function isStreamMessage(value: unknown): value is import('./types').StreamMessage {
  if (typeof value !== 'object' || value === null) return false;
  const message = value as Record<string, unknown>;
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
      && typeof message.score === 'number'
      && typeof message.severity === 'string';
  }

  if (message.type === 'localization') {
    return typeof message.alert_id === 'string'
      && (typeof message.probs === 'object' && message.probs !== null || Array.isArray(message.top3));
  }

  if (message.type === 'loss') {
    return typeof message.alert_id === 'string'
      && typeof message.est_leak_lps === 'number'
      && typeof message.litres_lost_cum === 'number';
  }

  if (message.type === 'readings') {
    return typeof message.t === 'number' && Array.isArray(message.readings)
      && message.readings.every((item: unknown) => {
        if (typeof item !== 'object' || item === null) return false;
        const candidate = item as Record<string, unknown>;
        return typeof candidate.node_id === 'string' && typeof candidate.pressure_m === 'number';
      });
  }

  if (message.type === 'fault') {
    return typeof message.t === 'number';
  }

  if (message.type === 'reset' || message.type === 'replay_end' || message.type === 'replay_stop' || message.type === 'leak_resolved') {
    return true;
  }

  return false;
}

export function normalizeStreamMessage(value: unknown): import('./types').StreamMessage[] {
  if (!isStreamMessage(value)) return [];
  const message = value as Record<string, any>;
  const timestamp = new Date(Number(message.t ?? Math.floor(Date.now() / 1000)) * 1000).toISOString();

  if (message.type === 'readings') {
    return message.readings.map((reading: Record<string, unknown>) => {
      const pointTimestamp = new Date(Number(reading.t ?? message.t) * 1000).toISOString();
      return {
        type: 'pressure' as const,
        timestamp: pointTimestamp,
        point: {
          timestamp: pointTimestamp,
          sensorId: String(reading.node_id),
          pressureM: Number(reading.pressure_m),
          anomaly: Boolean(reading.anomaly),
        },
      };
    });
  }
  if (message.type === 'alert') {
    const rawSev = String(message.severity).toLowerCase();
    const severity: import('./types').AlertMessage['severity'] =
      (rawSev === 'critical' || rawSev === 'high' || rawSev === 'medium' || rawSev === 'low')
        ? rawSev
        : 'warning';
    return [{
      type: 'alert', timestamp, id: String(message.id),
      severity,
      sensorId: String(message.detector ?? 'DETECTOR'), score: Number(message.score),
      message: `${String(message.severity).toUpperCase()} · ${String(message.detector ?? 'DETECTOR')}`,
    }];
  }
  if (message.type === 'localization') {
    const scores = message.probs && typeof message.probs === 'object'
      ? Object.entries(message.probs as Record<string, number>)
      : (message.top3 as { node_id: string; prob: number }[]).map((item) => [item.node_id, item.prob] as [string, number]);
    return [{
      type: 'localization', timestamp, alertId: String(message.alert_id),
      scores: scores.map(([junctionId, score]) => ({ junctionId, score: Number(score) })),
    }];
  }
  if (message.type === 'fault') {
    return [{
      type: 'fault',
      timestamp,
      t: Number(message.t),
      detail: (message.detail as Record<string, unknown> | string) ?? {},
    }];
  }
  if (message.type === 'loss') {
    return [{
      type: 'loss', alertId: String(message.alert_id),
      estimateLps: Number(message.est_leak_lps), litresLost: Number(message.litres_lost_cum),
    }];
  }
  if (message.type === 'reset') {
    return [{ type: 'reset' }];
  }
  if (message.type === 'replay_end' || message.type === 'replay_stop') {
    return [{ type: 'replay_end' }];
  }
  if (message.type === 'leak_resolved') {
    return [{ type: 'leak_resolved', alertId: String(message.alert_id ?? '') }];
  }
  return [];
}

