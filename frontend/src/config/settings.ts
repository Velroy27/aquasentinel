export const currencyPerLitre = 0.012;
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
  rise: 1.0,
};

export const formatRs = (value: number) =>
  `Rs ${Math.round(value).toLocaleString('en-IN')}`;

export const lossAvoided = (
  nodes: number,
  dailyLeakLitres = 18_000,
  avoidedFraction = 0.72,
  days = 365,
): number => Math.round(nodes * dailyLeakLitres * avoidedFraction * currencyPerLitre * days);
