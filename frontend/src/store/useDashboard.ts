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
  ResourceKey,
  ResourceStatus,
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
  stubBackend: boolean;
  anomalyAt: string | null;
  litresLost: number;
  mapFitRevision: number;
  heatmapEnabled: boolean;
  revealLeak: boolean;
  shortcutsOpen: boolean;
  resources: Record<ResourceKey, ResourceStatus>;
  loading: boolean;
  error: string | null;
  init: () => Promise<void>;
  retryResource: (key: ResourceKey) => Promise<void>;
  start: () => void;
  stop: () => void;
  reset: () => void;
  setSpeed: (speed: 1 | 5 | 20) => void;
  setScenario: (id: string) => void;
  setSelectedSensor: (id: string) => void;
  setHeatmapEnabled: (enabled: boolean) => void;
  setRevealLeak: (enabled: boolean) => void;
  setShortcutsOpen: (open: boolean) => void;
  receive: (message: StreamMessage) => void;
};

let unsubscribe: (() => void) | undefined;
let unsubscribeStatus: (() => void) | undefined;

const MAX_POINTS = 180;
const MAX_ALERTS = 40;

const initialResources: Record<ResourceKey, ResourceStatus> = {
  network: { loading: true, error: null },
  scenarios: { loading: true, error: null },
  results: { loading: true, error: null },
  placement: { loading: true, error: null },
};

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
  stubBackend: false,
  anomalyAt: null,
  litresLost: 0,
  mapFitRevision: 0,
  heatmapEnabled: true,
  revealLeak: false,
  shortcutsOpen: false,
  resources: initialResources,
  loading: true,
  error: null,

  retryResource: async (key) => {
    set((state) => ({ resources: { ...state.resources, [key]: { loading: true, error: null } } }));
    try {
      if (key === 'network') {
        const network = await dataSource.getNetwork();
        set({ network, pressuresBySensor: emptyReadings(network.sensors), selectedSensor: network.sensors[0] ?? null, error: null });
      } else if (key === 'scenarios') {
        const scenarios = await dataSource.getScenarios();
        set({ scenarios, scenarioId: get().scenarioId || scenarios[0]?.id || '' });
      } else if (key === 'results') {
        const results = await dataSource.getResults();
        set({ results });
        await get().retryResource('placement');
      } else {
        const placements = await dataSource.getPlacement(get().results);
        set({ placements });
      }
      set((state) => ({ resources: { ...state.resources, [key]: { loading: false, error: null } } }));
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Request failed';
      set((state) => ({
        error: key === 'network' ? 'Network unavailable' : state.error,
        resources: { ...state.resources, [key]: { loading: false, error: message } },
      }));
    }
  },

  init: async () => {
    set({ loading: true, error: null, resources: initialResources });
    await Promise.all([
      get().retryResource('network'),
      get().retryResource('scenarios'),
      get().retryResource('results'),
    ]);
    if (get().resources.placement.loading) await get().retryResource('placement');
    const health = await dataSource.getHealth().catch(() => null);
    unsubscribe?.();
    unsubscribeStatus?.();
    unsubscribe = dataSource.subscribe(get().receive);
    unsubscribeStatus = dataSource.onStatus((streamStatus) => set({ streamStatus, running: streamStatus === 'live' ? get().running : streamStatus === 'connecting' ? get().running : false }));
    const network = get().network;
    const scenarios = get().scenarios;
    set({
      stubBackend: health?.ml_mode === 'stub',
      scenarioId: get().scenarioId || scenarios[0]?.id || '',
      selectedSensor: get().selectedSensor ?? network?.sensors[0] ?? null,
      pressuresBySensor: network ? (Object.keys(get().pressuresBySensor).length ? get().pressuresBySensor : emptyReadings(network.sensors)) : {},
      loading: false,
    });
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
      mapFitRevision: get().mapFitRevision + 1,
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
      mapFitRevision: get().mapFitRevision + 1,
    });
  },

  setSelectedSensor: (selectedSensor) => set({ selectedSensor }),
  setHeatmapEnabled: (heatmapEnabled) => set({ heatmapEnabled }),
  setRevealLeak: (revealLeak) => set({ revealLeak }),
  setShortcutsOpen: (shortcutsOpen) => set({ shortcutsOpen }),

  receive: (message) => set((state) => {
    if (message.type === 'pressure') {
      const existing = state.pressuresBySensor[message.point.sensorId] ?? [];
      const next = [...existing, message.point].slice(-MAX_POINTS);
      return {
        pressuresBySensor: { ...state.pressuresBySensor, [message.point.sensorId]: next },
        // A pressure frame is authoritative proof that the socket is open. This
        // also recovers the pill if the browser missed the WebSocket open event.
        streamStatus: 'live',
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

export const selectTop3Suspects = (state: State) => {
  if (!state.alertActive || !state.localization) return [];
  return [...(state.localization.scores ?? [])].sort((a, b) => b.score - a.score).slice(0, 3);
};

export const selectSuspectRankMap = (state: State) => {
  const top3 = selectTop3Suspects(state);
  return new Map(top3.map((item, index) => [item.junctionId, index + 1]));
};
