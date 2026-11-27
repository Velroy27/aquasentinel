import { create } from 'zustand';
import { dataSource } from '../data';
import { demoMode } from '../config/settings';
import type {
  AlertMessage,
  LocalizationMessage,
  Network,
  Placement,
  PressurePoint,
  Results,
  Scenario,
  StreamMessage,
  StreamStatus,
} from '../data/types';

type State = {
  network: Network | null;
  scenarios: Scenario[];
  results: Results | null;
  placements: Placement[];
  scenarioId: string;
  selectedSensor: string | null;
  running: boolean;
  speed: 1 | 5 | 20;
  streamStatus: StreamStatus;
  pressuresBySensor: Record<string, PressurePoint[]>;
  alerts: AlertMessage[];
  localization: LocalizationMessage | null;
  alertActive: boolean;
  activeAlertId: string | null;
  leakRateEstimate: number | null;
  anomalyAt: string | null;
  litresLost: number;
  loading: boolean;
  error: string | null;
  init: () => Promise<void>;
  start: () => void;
  stop: () => void;
  reset: () => void;
  setSpeed: (speed: 1 | 5 | 20) => void;
  setScenario: (id: string) => void;
  setSelectedSensor: (id: string) => void;
  receive: (message: StreamMessage) => void;
};

let unsubscribe: (() => void) | undefined;
let unsubscribeStatus: (() => void) | undefined;

const MAX_POINTS = 180;
const MAX_ALERTS = 40;

function emptyReadings(sensors: string[]): Record<string, PressurePoint[]> {
  return Object.fromEntries(sensors.map((id) => [id, []]));
}

export const useDashboard = create<State>((set, get) => ({
  network: null,
  scenarios: [],
  results: null,
  placements: [],
  scenarioId: '',
  selectedSensor: null,
  running: false,
  speed: 1,
  streamStatus: 'idle',
  pressuresBySensor: {},
  alerts: [],
  localization: null,
  alertActive: false,
  activeAlertId: null,
  leakRateEstimate: null,
  anomalyAt: null,
  litresLost: 0,
  loading: true,
  error: null,

  init: async () => {
    try {
      const [network, scenarios, results, placements] = await Promise.all([
        dataSource.getNetwork(),
        dataSource.getScenarios(),
        dataSource.getResults(),
        dataSource.getPlacement(),
      ]);
      unsubscribe?.();
      unsubscribeStatus?.();
      unsubscribe = dataSource.subscribe(get().receive);
      unsubscribeStatus = dataSource.onStatus((streamStatus) => set({ streamStatus, running: streamStatus === 'live' ? get().running : streamStatus === 'connecting' ? get().running : false }));
      const scenarioId = scenarios[0]?.id ?? '';
      set({
        network,
        scenarios,
        results,
        placements,
        scenarioId,
        selectedSensor: network.sensors[0] ?? null,
        pressuresBySensor: emptyReadings(network.sensors),
        loading: false,
        error: null,
        streamStatus: 'idle',
      });
    } catch {
      set({ loading: false, error: 'Could not load network data. Check the API or mock fixtures.' });
    }
  },

  start: () => {
    const { scenarioId, speed } = get();
    if (!scenarioId) return;
    set({ running: true, streamStatus: demoMode ? 'live' : 'connecting' });
    dataSource.startScenario(scenarioId, speed);
  },

  stop: () => {
    dataSource.stop();
    set({ running: false, streamStatus: 'idle' });
  },

  reset: () => {
    dataSource.reset();
    const sensors = get().network?.sensors ?? [];
    set({
      running: false,
      streamStatus: 'idle',
      pressuresBySensor: emptyReadings(sensors),
      alerts: [],
      localization: null,
      alertActive: false,
      activeAlertId: null,
      leakRateEstimate: null,
      anomalyAt: null,
      litresLost: 0,
    });
  },

  setSpeed: (speed) => {
    set({ speed });
    const { running, scenarioId } = get();
    if (running && scenarioId) dataSource.startScenario(scenarioId, speed);
  },

  setScenario: (scenarioId) => {
    dataSource.stop();
    const sensors = get().network?.sensors ?? [];
    set({
      scenarioId,
      running: false,
      streamStatus: 'idle',
      pressuresBySensor: emptyReadings(sensors),
      alerts: [],
      localization: null,
      alertActive: false,
      activeAlertId: null,
      leakRateEstimate: null,
      anomalyAt: null,
      litresLost: 0,
    });
  },

  setSelectedSensor: (selectedSensor) => set({ selectedSensor }),

  receive: (message) => set((state) => {
    if (message.type === 'pressure') {
      const existing = state.pressuresBySensor[message.point.sensorId] ?? [];
      const next = [...existing, message.point].slice(-MAX_POINTS);
      return {
        pressuresBySensor: { ...state.pressuresBySensor, [message.point.sensorId]: next },
        anomalyAt: message.point.anomaly ? message.point.timestamp : state.anomalyAt,
      };
    }

    if (message.type === 'alert') {
      if (state.alerts.some((a) => a.id === message.id)) return {};
      return {
        alerts: [message, ...state.alerts].slice(0, MAX_ALERTS),
        alertActive: true,
        activeAlertId: message.id,
      };
    }

    if (message.type === 'localization') {
      if (!state.alertActive || state.activeAlertId !== message.alertId) {
        if (import.meta.env.DEV) console.warn('Ignoring localization without a matching active alert', message.alertId);
        return {};
      }
      return { localization: message };
    }

    if (message.type === 'loss') {
      if (!state.alertActive || state.activeAlertId !== message.alertId) {
        if (import.meta.env.DEV) console.warn('Ignoring size/loss message without a matching active alert', message.alertId);
        return {};
      }
      return { leakRateEstimate: message.estimateLps, litresLost: message.litresLost };
    }

    return {};
  }),
}));

export const selectLatestBySensor = (state: State) => {
  const latest: Record<string, PressurePoint | undefined> = {};
  for (const [sensorId, points] of Object.entries(state.pressuresBySensor)) {
    latest[sensorId] = points[points.length - 1];
  }
  return latest;
};

const EMPTY_PRESSURES: PressurePoint[] = [];

export const selectSelectedPressures = (state: State) =>
  state.selectedSensor ? state.pressuresBySensor[state.selectedSensor] ?? EMPTY_PRESSURES : EMPTY_PRESSURES;

export const selectCurrentScenario = (state: State) =>
  state.scenarios.find((scenario) => scenario.id === state.scenarioId) ?? null;
