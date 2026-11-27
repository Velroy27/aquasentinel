# AquaSentinel Backend Service

AquaSentinel backend and integration layer: simulates EPANET Net3 sensor nodes, streams pressure/flow telemetry, executes leak detection, localization, and size estimation pipelines, persists runs/alerts/losses to SQLite, and serves the dashboard over REST and WebSockets.

---

## 1. Quick Start

### Prerequisites
- Python 3.12+ (Active virtual environment at `.venv`)
- Windows PowerShell

### Single-Command Start (API + Emulator)
From repository root:
```powershell
.venv\Scripts\python.exe backend/run_all.py
```
This checks port 8000, starts the FastAPI server (`http://0.0.0.0:8000`), waits for `/health`, and launches the supervised node emulator (`scenario_105_leak` @ 5x speed).

### Manual Server Launch
```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### Pre-Demo Verification
Run the automated pre-flight checklist:
```powershell
.venv\Scripts\python.exe backend/check_ready.py
```

---

## 2. Ports & Networking

| Service | Host | Port | Protocol | Notes |
|---|---|---|---|---|
| FastAPI Server | `0.0.0.0` | `8000` | HTTP / WebSocket | CORS enabled for all origins (`*`) |
| MQTT Broker (Optional) | `127.0.0.1` | `1883` | TCP | Mosquitto (Optional for emulator `--mqtt`) |

---

## 3. Endpoints & API Reference

### Contract Endpoints (from `contracts/CONTRACT.md`)

- **`GET /api/network`**
  Returns network topology with 92 junctions, pipe geometry, and default sensor locations.
  - Response: `{"nodes":[{"id","x","y","elevation","is_sensor"}],"pipes":[{"id","from","to","length","diameter"}],"sensors":["id",...]}`

- **`GET /api/scenarios`**
  Returns available replay scenarios.
  - Response: `[{"id","leak_node","leak_size_lps","start_t","description"}]`

- **`POST /api/replay/start`**
  Starts replaying sensor telemetry at accelerated speed.
  - Body: `{"scenario_id": "scenario_105_leak", "speed": 5}` (Allowed speeds: `1`, `5`, `20`)
  - Response: `{"status": "started", "run_id": str, "scenario_id": str, "speed": float}`

- **`POST /api/replay/stop`**
  Stops active replay playback (idempotent).
  - Response: `{"status": "stopped"}`

- **`GET /api/results`**
  Returns benchmark detection metrics and sensor placement curves.
  - Response: `{"detection_rate": float, "false_alarm_per_day": float, "top1": float, "top3": float, "neighbour_acc": float, "chance_baseline": float, "placement": [{"n_sensors": int, "optimized": float, "random_mean": float, "random_std": float}], "measured_at": str}`

- **`GET /api/placement?n=8`**
  Returns greedy sensor placement set for `n` sensors and network coverage.
  - Response: `{"sensors": ["105", "115", ...], "coverage": float}`

- **`GET /api/cost?nodes=8`**
  Returns exact cost model.
  - Response: `{"hardware_rs": 12000, "per_node_rs": 1500, "est_loss_avoided_rs": 256000}`
  - Note: `hardware_rs = nodes * 1500` (ESP32 450 + transducer 650 + enclosure/power/connectors 400).

- **`WebSocket /ws/stream`**
  Real-time streaming channel for live dashboard updates.
  - Message Types:
    1. `"readings"`: `{"type": "readings", "t": int, "readings": [{"t": int, "node_id": str, "pressure_m": float, "flow_lps": float | null}]}`
    2. `"alert"`: `{"type": "alert", "id": str, "t": int, "severity": "critical"|"high"|"medium"|"low", "score": float, "detector": str, "stub": Optional[bool]}`
    3. `"localization"`: `{"type": "localization", "alert_id": str, "top3": [{"node_id": str, "prob": float}], "probs": {node_id: prob}, "stub": Optional[bool]}`
    4. `"loss"`: `{"type": "loss", "alert_id": str, "est_leak_lps": float, "litres_lost_cum": float, "stub": Optional[bool]}`
    5. `"fault"`: `{"type": "fault", "t": int, "detail": dict | str}` *(Contract Addition: emitted when sensor health check detects stuck/dropout fault; does not trigger leak alert, localization, or loss)*
    *(Note: "stub": true is included only when an ML stub answered during the run; omitted when real ML answers)*

### Additional Endpoints & Contract Additions

- **`POST /api/ingest`**
  Receives a single sensor reading from external edge devices or emulator. Grouped automatically by `t` into full multi-sensor steps.
  - Body: `{"t": int, "node_id": str, "pressure_m": float, "flow_lps": float | null}`
  - Response (201): `{"status": "ingested", "node_id": str, "t": int}`

- **`GET /health`**
  Health probe returning uptime, DB status, ML bridge mode (`real` vs `stub`), active WS clients, and ML diagnostics.
  - Extended fields: `ml_avg_latency_ms` (float), `stub_calls` (int), `last_stub_reason` (str | null), `is_warming_up` (bool).

- **Scenario Catalog Ordering**:
  `/api/scenarios` serves the audited catalog scenarios from `data/replay/scenarios_catalog.json` FIRST (in catalog order), followed by mock scenarios.

- **Pipeline Window & Resolve Facts**:
  - `WINDOW_SIZE`: 45 steps (450s window @ 10s intervals), matching the trained ML model window.
  - `MIN_STEPS_FOR_DETECT`: 2 steps minimum before calling detector; stuck sensor health check requires 10 steps.
  - `RESOLVE_CLEAR_WINDOWS`: 12 consecutive non-anomaly windows required to resolve an active leak alert and stop loss ticks.
  - `start_time_of_day`: Derived automatically by `ml/api.py` from `t` in the first message of the window to index diurnal pressure baselines.
  - ML Dependencies: Real ML inference requires `numpy`, `joblib`, and `scikit-learn` from the Python environment that has them installed (with sys.path fallback to base Python site-packages).

- **`GET /api/history/runs`**
  Lists previous simulation and ingestion runs.

- **`GET /api/history/alerts?run_id=`**
  Lists historical alerts.

- **`GET /api/history/alerts/{alert_id}`**
  Returns complete alert timeline with localization and minute-by-minute cumulative loss ticks.

- **`GET /api/history/readings?run_id=&node_id=&limit=`**
  Queries raw telemetry readings (clamped limit up to 2000).

- **`DELETE /api/history?confirm=true`**
  Resets all historical records in the database.

---

## 4. Replay Scenario File Format

For ML/Data integration, scenarios exported into `/data/replay/*.json` will be automatically detected and loaded with higher priority than mock files.

### Schema
```json
{
  "scenario_id": "scenario_pipe_break_105",
  "leak_node": "105",
  "leak_size_lps": 5.5,
  "start_t": 60,
  "description": "Pipe rupture at commercial junction 105 starting at t=60s",
  "steps": [
    {
      "t": 0,
      "readings": [
        { "t": 0, "node_id": "105", "pressure_m": 52.4, "flow_lps": 25.1 },
        { "t": 0, "node_id": "115", "pressure_m": 50.1, "flow_lps": null }
      ]
    },
    {
      "t": 10,
      "readings": [
        { "t": 10, "node_id": "105", "pressure_m": 52.2, "flow_lps": 25.0 },
        { "t": 10, "node_id": "115", "pressure_m": 50.0, "flow_lps": null }
      ]
    }
  ]
}
```

---

## 5. Sensor Node Emulator (`backend/emulator.py`)

Plays scenario windows at 10-second intervals to test ingestion.

### HTTP Mode (Default)
```powershell
.venv\Scripts\python.exe -m backend.emulator --scenario scenario_105_leak --speed 5 --api http://127.0.0.1:8000
```

### MQTT Mode
```powershell
.venv\Scripts\python.exe -m backend.emulator --scenario scenario_105_leak --speed 5 --mqtt --mqtt-host 127.0.0.1 --mqtt-port 1883
```
- Topic format: `aquasentinel/nodes/<node_id>`
- If MQTT broker is unavailable, emulator logs a warning and falls back to HTTP POST `/api/ingest`.

---

## 6. Persistence & Storage (SQLite)

- **Database Path**: `backend/db/aquasentinel.db` (Ignored in git)
- **Engine**: SQLite with WAL mode (`PRAGMA journal_mode = WAL;`) and 5000 ms busy timeout.
- **Asynchronous Batch Writer**: Pipeline and WebSocket handlers never wait on disk I/O; writes go through an in-memory queue processed by a background worker thread.
- **Tables**:
  - `runs`: Replay and ingestion session metadata.
  - `readings`: High-throughput sensor telemetry.
  - `alerts`: Deduplicated leak events and anomaly scores.
  - `localizations`: Top-3 localized nodes and full probability distributions.
  - `losses`: Cumulative volume lost tracked per second.
- **Startup Protection**: Active alerts from an interrupted server shutdown are automatically marked `status = 'interrupted'` so loss counting does not continue while offline. Telemetry older than 7 days is automatically pruned on startup.
- **Inspect DB**:
```powershell
sqlite3 backend/db/aquasentinel.db "SELECT id, scenario_id, started_at FROM runs;"
```
- **Reset DB**:
```powershell
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/history?confirm=true"
```

---

## 7. Testing & Verification

### Running the Test Suite
```powershell
.venv\Scripts\python.exe -m pytest backend/tests/ -v -p no:cacheprovider
```

### Running the Soak Test (5 Minutes)
```powershell
.venv\Scripts\python.exe backend/tests/soak_test.py --duration 300
```
