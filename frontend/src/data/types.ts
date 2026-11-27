// Contract-aligned types. Keep in sync with contracts/CONTRACT.md.
export type Junction = {
  id: string;
  x: number;
  y: number;
  elevationM: number;
};

export type Pipe = {
  id: string;
  from: string;
  to: string;
};

export type Network = {
  junctions: Junction[];
  pipes: Pipe[];
  sensors: string[];
};

export type Scenario = {
  id: string;
  name: string;
  leakNode: string;
  startedAt: string;
  leakLitresPerSecond: number;
  basePressureM: number;
};

export type PressurePoint = {
  timestamp: string;
  sensorId: string;
  pressureM: number;
  anomaly: boolean;
};

export type AlertMessage = {
  type: 'alert';
  timestamp: string;
  id: string;
  severity: 'critical' | 'warning';
  sensorId: string;
  score: number;
  message: string;
};

export type LocalizationScore = {
  junctionId: string;
  score: number;
};

export type LocalizationMessage = {
  type: 'localization';
  timestamp: string;
  alertId: string;
  scores: LocalizationScore[];
};

export type LossMessage = {
  type: 'loss';
  alertId: string;
  estimateLps: number;
  litresLost: number;
};

export type PressureMessage = {
  type: 'pressure';
  timestamp: string;
  point: PressurePoint;
};

export type StreamStatus = 'idle' | 'connecting' | 'live' | 'disconnected' | 'error';

export type StreamMessage = AlertMessage | LocalizationMessage | PressureMessage | LossMessage;

export type Results = {
  detectionRate: number;
  falseAlarmsPerDay: number;
  top1: number;
  top3: number;
  neighbourAccuracy: number;
  chanceBaseline: number;
  placement: {
    sensorCount: number;
    optimized: number;
    randomMean: number;
    randomStd: number;
  }[];
  measuredAt: string;
};

export type Placement = {
  sensorCount: number;
  optimizedCoverage: number;
  randomCoverage: number;
  optimizedAccuracy: number;
  randomAccuracy: number;
  optimizedSensors: string[];
  randomSensors: string[];
};

export type DataHandler = (message: StreamMessage) => void;
export type StatusHandler = (status: StreamStatus) => void;
