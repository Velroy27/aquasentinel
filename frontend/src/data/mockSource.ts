import type { DataSource } from './DataSource';
import { network, placements, results, scenarios } from './mockData';
import type { CostData, DataHandler, Network, Placement, Results, Scenario, StatusHandler, StreamMessage } from './types';

type TimelineCache = Map<string, StreamMessage[][]>;

function buildTimeline(scenario: Scenario): StreamMessage[][] {
  const start = Date.parse(scenario.startedAt);
  const sensorIds = network.sensors;
  const leakNode = scenario.leakNode;
  const steps: StreamMessage[][] = [];
  let anomalyAt = -1;

  for (let step = 0; step < 120; step += 1) {
    const timestamp = new Date(start + step * 1_000).toISOString();
    const batch: StreamMessage[] = [];
    let anomalySeen = false;

    for (let i = 0; i < sensorIds.length; i += 1) {
      const sensorId = sensorIds[i];
      const sensor = network.junctions.find((j) => j.id === sensorId);
      const leak = network.junctions.find((j) => j.id === leakNode);
      const dist = sensor && leak ? Math.hypot(sensor.x - leak.x, sensor.y - leak.y) : 40;
      const proximity = Math.max(0, 1 - dist / 55);
      const noise = Math.sin(step * 0.45 + i * 1.7) * 0.18 + Math.cos(step * 0.17 + i) * 0.08;
      const drop = step > 18 ? Math.min(scenario.leakLitresPerSecond * 1.6, (step - 18) * 0.12) * proximity : 0;
      const pressureM = +(scenario.basePressureM + noise - drop).toFixed(2);
      const anomaly = step > 20 && drop > 0.55;
      if (anomaly) anomalySeen = true;
      batch.push({ type: 'pressure', timestamp, point: { timestamp, sensorId, pressureM, anomaly } });
    }

    if (anomalySeen && anomalyAt < 0) anomalyAt = step;
    if (anomalyAt >= 0 && step === anomalyAt + 2) {
      const sensorId = sensorIds[Math.floor(sensorIds.length / 2)] ?? leakNode;
      batch.push({
        type: 'alert',
        timestamp,
        id: `AL-${String(1000 + step)}`,
        severity: 'critical',
        sensorId,
        score: 0.96,
        message: `Pressure anomaly detected near ${leakNode}`,
      });
    }

    if (anomalyAt >= 0 && step >= anomalyAt + 2) {
      const estimateLps = scenario.leakLitresPerSecond;
      const elapsedSeconds = (step - (anomalyAt + 2)) * 10;
      batch.push({
        type: 'loss',
        alertId: `AL-${String(1000 + anomalyAt + 2)}`,
        estimateLps,
        litresLost: Math.round(estimateLps * elapsedSeconds),
      });
    }

    if (anomalyAt >= 0 && step >= anomalyAt + 3 && step % 4 === 0 && step < anomalyAt + 24) {
      const ranked = network.junctions
        .map((junction) => {
          const leak = network.junctions.find((j) => j.id === leakNode)!;
          const d = Math.hypot(junction.x - leak.x, junction.y - leak.y);
          const raw = Math.exp(-d / (8 + (step - anomalyAt) * 0.8));
          return { junctionId: junction.id, raw };
        })
        .sort((a, b) => b.raw - a.raw);
      const total = ranked.reduce((sum, item) => sum + item.raw, 0) || 1;
      batch.push({
        type: 'localization',
        timestamp,
        alertId: '',
        scores: ranked.map((item) => ({ junctionId: item.junctionId, score: +(item.raw / total).toFixed(4) })),
      });
    }

    steps.push(batch);
  }

  return steps;
}

export class MockSource implements DataSource {
  private handlers = new Set<DataHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private timer: number | undefined;
  private index = 0;
  private scenarioId = scenarios[0]?.id ?? '';
  private activeAlertId = '';
  private cache: TimelineCache = new Map();

  private timeline(): StreamMessage[][] {
    const scenario = scenarios.find((s) => s.id === this.scenarioId) ?? scenarios[0];
    if (!scenario) return [];
    const cached = this.cache.get(scenario.id);
    if (cached) return cached;
    const built = buildTimeline(scenario);
    this.cache.set(scenario.id, built);
    return built;
  }

  private emitStatus(status: import('./types').StreamStatus) {
    this.statusHandlers.forEach((handler) => handler(status));
  }

  async getNetwork(): Promise<Network> { return network; }
  async getResults(): Promise<Results> { return results; }
  async getPlacement(): Promise<Placement[]> { return placements; }
  async getCost(nodes = 8): Promise<CostData> {
    return {
      hardwareRs: nodes * 1500,
      perNodeRs: 1500,
      estLossAvoidedRs: nodes * 32000,
    };
  }
  async getScenarios(): Promise<Scenario[]> { return scenarios; }
  async getHealth(): Promise<null> { return null; }

  startScenario(id: string, speed: 1 | 5 | 20): void {
    if (this.scenarioId === id && this.timer !== undefined) {
      window.clearInterval(this.timer);
    } else {
      this.stop();
      this.scenarioId = id;
      this.activeAlertId = '';
      this.index = 0;
    }
    this.emitStatus('live');
    const steps = this.timeline();
    this.timer = window.setInterval(() => {
      if (!steps.length) return;
      const batch = steps[this.index];
      this.index += 1;
      if (batch) {
        batch.forEach((message) => {
          if (message.type === 'alert') this.activeAlertId = message.id;
          if (message.type === 'localization') message.alertId = this.activeAlertId;
          this.handlers.forEach((handler) => handler(message));
        });
      }
      if (this.index >= steps.length) this.stop();
    }, Math.max(50, 1000 / speed));
  }

  stop(): void {
    if (this.timer !== undefined) window.clearInterval(this.timer);
    this.timer = undefined;
    this.handlers.forEach((handler) => handler({ type: 'replay_end' }));
    this.emitStatus('idle');
  }

  reset(): void {
    if (this.timer !== undefined) window.clearInterval(this.timer);
    this.timer = undefined;
    this.index = 0;
    this.activeAlertId = '';
    this.handlers.forEach((handler) => handler({ type: 'reset' }));
    this.emitStatus('idle');
  }

  subscribe(handler: DataHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }
}
