import type { Results } from './types';

type RawResult = Record<string, unknown>;

function numberField(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Invalid results field: ${field}`);
  }
  return value;
}

function pick(value: RawResult, camel: string, snake: string): unknown {
  return value[camel] ?? value[snake];
}

export function normalizeResults(value: unknown): Results {
  if (typeof value !== 'object' || value === null) throw new Error('Invalid results response');
  const raw = value as RawResult;
  const rawPlacement = raw.placement;
  if (!Array.isArray(rawPlacement) || typeof pick(raw, 'measuredAt', 'measured_at') !== 'string') {
    throw new Error('Invalid results response');
  }

  return {
    detectionRate: numberField(pick(raw, 'detectionRate', 'detection_rate'), 'detection_rate'),
    falseAlarmsPerDay: numberField(pick(raw, 'falseAlarmsPerDay', 'false_alarm_per_day'), 'false_alarm_per_day'),
    top1: numberField(raw.top1, 'top1'),
    top3: numberField(raw.top3, 'top3'),
    neighbourAccuracy: numberField(pick(raw, 'neighbourAccuracy', 'neighbour_acc'), 'neighbour_acc'),
    chanceBaseline: numberField(pick(raw, 'chanceBaseline', 'chance_baseline'), 'chance_baseline'),
    placement: rawPlacement.map((item, index) => {
      if (typeof item !== 'object' || item === null) throw new Error(`Invalid placement row: ${index}`);
      const row = item as RawResult;
      return {
        sensorCount: numberField(pick(row, 'sensorCount', 'n_sensors'), `placement[${index}].n_sensors`),
        optimized: numberField(pick(row, 'optimized', 'optimized'), `placement[${index}].optimized`),
        randomMean: numberField(pick(row, 'randomMean', 'random_mean'), `placement[${index}].random_mean`),
        randomStd: numberField(pick(row, 'randomStd', 'random_std'), `placement[${index}].random_std`),
      };
    }),
    measuredAt: String(pick(raw, 'measuredAt', 'measured_at')),
  };
}
