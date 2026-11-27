import { apiBaseUrl, wsUrl } from '../config/settings';
import { normalizeStreamMessage, type DataSource } from './DataSource';
import { normalizeResults } from './normalizeResults';
import { normalizePlacementResponse } from './normalizePlacement';
import { normalizeNetwork, normalizeScenarios } from './normalizeNetwork';
import type { BackendHealth, DataHandler, Network, Placement, Results, Scenario, StatusHandler, StreamStatus } from './types';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export class LiveSource implements DataSource {
  private handlers = new Set<DataHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private ws: WebSocket | undefined;
  private reconnectTimer: number | undefined;
  private shouldReconnect = false;

  private emitStatus(status: StreamStatus) {
    this.statusHandlers.forEach((handler) => handler(status));
  }

  private openSocket() {
    this.ws?.close();
    this.emitStatus('connecting');
    const ws = new WebSocket(wsUrl);
    this.ws = ws;

    ws.addEventListener('open', () => {
      this.emitStatus('live');
    });

    ws.addEventListener('message', (event) => {
      try {
        const value = JSON.parse(String(event.data));
        normalizeStreamMessage(value).forEach((message) => this.handlers.forEach((handler) => handler(message)));
      } catch {
        // Ignore malformed messages; contract violations should not crash the UI.
      }
    });

    ws.addEventListener('close', () => {
      if (this.ws === ws) this.ws = undefined;
      if (this.shouldReconnect) {
        this.emitStatus('disconnected');
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = window.setTimeout(() => this.openSocket(), 1500);
      } else {
        this.emitStatus('idle');
      }
    });

    ws.addEventListener('error', () => {
      this.emitStatus('error');
    });
  }

  async getNetwork(): Promise<Network> { return normalizeNetwork(await getJson<unknown>('/network')); }
  async getResults(): Promise<Results> { return normalizeResults(await getJson<unknown>('/results')); }
  async getPlacement(results?: Results | null): Promise<Placement[]> {
    const counts = results?.placement.map((row) => row.sensorCount) ?? [8];
    const responses = await Promise.all(counts.map(async (count) => {
      try {
        const response = await getJson<unknown>(`/placement?n=${count}`);
        return normalizePlacementResponse(response, count, results);
      } catch {
        return null;
      }
    }));
    const placements = responses.filter((row): row is Placement => row !== null);
    if (!placements.length) throw new Error('Placement data unavailable');
    return placements;
  }
  async getScenarios(): Promise<Scenario[]> { return normalizeScenarios(await getJson<unknown>('/scenarios')); }
  async getHealth(): Promise<BackendHealth | null> {
    const base = apiBaseUrl.replace(/\/api\/?$/, '');
    const response = await fetch(`${base}/health`);
    if (!response.ok) return null;
    return response.json() as Promise<BackendHealth>;
  }

  startScenario(id: string, speed: 1 | 5 | 20): void {
    this.shouldReconnect = true;
    const start = () => fetch(`${apiBaseUrl}/replay/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_id: id, speed }),
    }).then((response) => {
      if (!response.ok) throw new Error(`Replay start failed: ${response.status}`);
    }).catch((error) => console.error(error));
    if (this.ws?.readyState === WebSocket.OPEN) void start();
    else {
      this.openSocket();
      this.ws?.addEventListener('open', () => void start(), { once: true });
    }
  }

  stop(): void {
    void fetch(`${apiBaseUrl}/replay/stop`, { method: 'POST' }).catch((error) => console.error(error));
  }

  reset(): void {
    this.stop();
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
