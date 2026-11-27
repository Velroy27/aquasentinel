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
  length?: number;
  diameter?: number;
};

export type Network = {
  junctions: Junction[];
  pipes: Pipe[];
  sensors: string[];
  viewBoxWidth: number;
  viewBoxHeight: number;
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
  severity: 'critical' | 'high' | 'medium' | 'low' | 'warning';
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

export type FaultMessage = {
  type: 'fault';
  timestamp: string;
  t: number;
  detail: Record<string, unknown> | string;
};

export type ResetMessage = {
  type: 'reset';
};

export type ReplayEndMessage = {
  type: 'replay_end';
};

export type LeakResolvedMessage = {
  type: 'leak_resolved';
  alertId?: string;
};

export type StreamStatus = 'idle' | 'connecting' | 'live' | 'disconnected' | 'error';

export type BackendHealth = { ml_mode?: 'real' | 'stub' | string };

export type StreamMessage = AlertMessage | LocalizationMessage | PressureMessage | LossMessage | FaultMessage | ResetMessage | ReplayEndMessage | LeakResolvedMessage;

export type CostData = {
  hardwareRs: number;
  perNodeRs: number;
  estLossAvoidedRs: number;
};

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
  optimizedCoverage: number | null;
  randomCoverage: number | null;
  optimizedSensors: string[];
  randomSensors: string[] | null;
};

export type ResourceKey = 'network' | 'scenarios' | 'results' | 'placement';
export type ResourceStatus = { loading: boolean; error: string | null };

export type DataHandler = (message: StreamMessage) => void;
export type StatusHandler = (status: StreamStatus) => void;
