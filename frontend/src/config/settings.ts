export const currencyPerLitre = 0.25;
export const costPerNode = 1_500;
export const demoMode = import.meta.env.VITE_DATA_MODE !== 'live';
export const apiBaseUrl = (import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000/api').replace(/\/$/, '');
export const wsUrl = import.meta.env.VITE_WS_URL ?? 'ws://127.0.0.1:8000/ws/stream';

export const pressureScale = {
  low: '#d96b54',
  normal: '#66787d',
  high: '#25b6a5',
} as const;

export const pressureDelta = {
  loss: -1.5,
  strongLoss: -4,
  rise: 1.0,
};

export const mapEffectMotion = {
  transitionMs: 400,
  lowMildMs: 2_800,
  lowStrongMs: 2_200,
  highMs: 1_200,
  flowNormalMs: 2_200,
  flowLowMs: 3_200,
  flowHighMs: 1_100,
  leakCycleMs: 2_100,
  puddleGrowthMs: 45_000,
  maxAnimatedPipes: 5,
  pressureRingPx: 7,
  suspectRingPx: 10,
  leakRingPx: 5,
  puddleStartPx: 5,
  puddleMaxPx: 28,
  dropletRisePx: 16,
  dropletSizePx: 4,
  dropletArcPx: 7,
  flowDashPx: 1.2,
  flowGapPx: 3.8,
  flowDashOffsetPx: 10,
  flowStaggerMs: 180,
  flowStaggerSlots: 3,
  leakRateScale: 8,
} as const;

export const elevationExaggeration = 0.18;

export const view3DConfig = {
  elevationExaggeration,
  maxDpr: 1.5,
  defaultCameraPosition: [0, 65, 80] as [number, number, number],
  topCameraPosition: [0, 115, 0.001] as [number, number, number],
  autoOrbitSpeed: 0.8,
  particlesPerPipe: 2,
  flowSpeedNormal: 0.35,
  flowSpeedLow: 0.18,
  flowSpeedHigh: 0.70,
  maxFlowOpacity: 0.5,
} as const;

export const formatRs = (value: number) =>
  `Rs ${Math.round(value).toLocaleString('en-IN')}`;

export const lossAvoided = (
  nodes: number,
  dailyLeakLitres = 18_000,
  avoidedFraction = 0.72,
  days = 365,
): number => Math.round(nodes * dailyLeakLitres * avoidedFraction * currencyPerLitre * days);
