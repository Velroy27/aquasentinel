import { apiBaseUrl, wsUrl } from '../config/settings';
import { isStreamMessage, type DataSource } from './DataSource';
import { normalizeResults } from './normalizeResults';
import { normalizeNetwork, normalizeScenarios } from './normalizeNetwork';
import type { DataHandler, Network, Placement, Results, Scenario, StatusHandler, StreamStatus } from './types';

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
  private lastStart: { id: string; speed: 1 | 5 | 20 } | null = null;

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
      if (this.lastStart) {
        ws.send(JSON.stringify({ action: 'start', scenarioId: this.lastStart.id, speed: this.lastStart.speed }));
      }
    });

    ws.addEventListener('message', (event) => {
      try {
        const value = JSON.parse(String(event.data));
        if (isStreamMessage(value)) this.handlers.forEach((handler) => handler(value));
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

  private send(payload: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  async getNetwork(): Promise<Network> { return normalizeNetwork(await getJson<unknown>('/network')); }
  async getResults(): Promise<Results> { return normalizeResults(await getJson<unknown>('/results')); }
  async getPlacement(): Promise<Placement[]> { return getJson<Placement[]>('/placement'); }
  async getScenarios(): Promise<Scenario[]> { return normalizeScenarios(await getJson<unknown>('/scenarios')); }

  startScenario(id: string, speed: 1 | 5 | 20): void {
    this.lastStart = { id, speed };
    this.shouldReconnect = true;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.openSocket();
      return;
    }
    this.send({ action: 'start', scenarioId: id, speed });
  }

  stop(): void {
    this.lastStart = null;
    this.send({ action: 'stop' });
  }

  reset(): void {
    this.lastStart = null;
    this.send({ action: 'reset' });
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
