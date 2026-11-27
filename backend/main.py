# AquaSentinel FastAPI Service & Integration Layer
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import math
import sys
import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from backend.config import (
    ALLOWED_REPLAY_SPEEDS,
    API_HOST,
    API_PORT,
    BACKEND_DIR,
    COVERAGE_BASE_RATIO,
    COVERAGE_MAX_RATIO,
    COVERAGE_PER_NODE_RATIO,
    COST_PER_NODE_RS,
    DATA_DIR,
    DATA_REPLAY_DIR,
    DB_PATH,
    EST_LOSS_SAVED_PER_LEAK_RS,
    INGEST_MAX_BODY_BYTES,
    LOG_FILE,
    LOGS_DIR,
    MOCK_DIR,
    WS_PING_INTERVAL_S,
    WS_QUEUE_MAXSIZE,
)
from backend.db import Database
from backend.ml_bridge import MLBridge
from backend.pipeline import Pipeline
from backend.replay import ReplayEngine

# ------------------------------------------------------------------------------
# LOGGING SETUP (ASCII ONLY, Windows cp1252 safe)
# ------------------------------------------------------------------------------
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("aquasentinel")
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ------------------------------------------------------------------------------
# APPLICATION STATE & SINGLETONS
# ------------------------------------------------------------------------------
db = Database()
ml_bridge = MLBridge()

_last_error: Optional[str] = None
_start_time = time.time()
is_warming_up: bool = True


# WebSocket Connection Manager with Bounded Client Queues
class WebSocketManager:
    def __init__(self):
        self._connections: dict[WebSocket, asyncio.Queue] = {}
        self._tasks: dict[WebSocket, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> asyncio.Queue:
        await websocket.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_MAXSIZE)
        async with self._lock:
            self._connections[websocket] = q
            task = asyncio.create_task(self._client_sender(websocket, q))
            self._tasks[websocket] = task
        logger.info("WebSocket client connected (total active: %d)", len(self._connections))
        return q

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            task = self._tasks.pop(websocket, None)
            if task and not task.done():
                task.cancel()
            self._connections.pop(websocket, None)
        logger.info("WebSocket client disconnected (remaining: %d)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._connections.items())

        for ws, q in clients:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.task_done()
                    q.put_nowait(message)
                except Exception:
                    pass

    async def _client_sender(self, websocket: WebSocket, q: asyncio.Queue) -> None:
        last_ping = time.time()
        try:
            while True:
                now = time.time()
                timeout = max(1.0, WS_PING_INTERVAL_S - (now - last_ping))
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=timeout)
                    await websocket.send_json(msg)
                    q.task_done()
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping", "t": int(time.time())})
                    last_ping = time.time()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Client sender terminating: %s", e)
        finally:
            await self.disconnect(websocket)

    def count(self) -> int:
        return len(self._connections)


ws_manager = WebSocketManager()

_network_file = MOCK_DIR / "network_net3.json"
_valid_nodes: set[str] = set()
if _network_file.exists():
    try:
        with open(_network_file, "r", encoding="utf-8") as f:
            _net_data = json.load(f)
            _valid_nodes = {n["id"] for n in _net_data.get("nodes", [])}
    except Exception as e:
        logger.warning("Could not pre-load network nodes: %s", e)

pipeline = Pipeline(
    db=db,
    ml_bridge=ml_bridge,
    broadcast_func=ws_manager.broadcast,
    valid_nodes=_valid_nodes,
)

replay_engine = ReplayEngine(pipeline=pipeline, db=db)


def _load_warmup_window() -> list[Any]:
    """Finds a scenario in data/replay or mock/replay to build a real warm-up window."""
    candidate_files = []
    if DATA_REPLAY_DIR.exists():
        candidate_files.extend(sorted(DATA_REPLAY_DIR.glob("*.json")))

    for f in candidate_files:
        if f.name == "scenarios_catalog.json":
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, list) and len(data) >= 10:
                return data[:45]
            elif isinstance(data, dict) and "steps" in data:
                return data["steps"][:45]
        except Exception:
            continue

    # Fallback synthetic Net3 window with all 8 sensors
    sensors = ["208", "10", "15", "184", "601", "164", "60", "123"]
    steps = []
    for step_idx in range(45):
        t = step_idx * 10
        readings = [{"t": t, "node_id": s, "pressure_m": 50.0, "flow_lps": None} for s in sensors]
        steps.append({"type": "readings", "t": t, "readings": readings})
    return steps


# ------------------------------------------------------------------------------
# LIFECYCLE
# ------------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global is_warming_up
    logger.info("AquaSentinel Backend starting up")

    # Start warm-up before accepting replays
    warm_window = _load_warmup_window()
    try:
        await ml_bridge.warmup(warm_window)
    except Exception as e:
        logger.error("Warm-up call encountered error: %s", e)
    finally:
        is_warming_up = False

    yield

    logger.info("AquaSentinel Backend shutting down")
    await replay_engine.stop()
    db.close()


app = FastAPI(
    title="AquaSentinel Backend",
    description="Leak detection, localization, and sensor placement service for EPANET Net3",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------------------
# INPUT SAFETY & ERROR HANDLERS
# ------------------------------------------------------------------------------
@app.middleware("http")
async def limit_body_size_middleware(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/ingest":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > INGEST_MAX_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"error": "Payload too large", "max_bytes": INGEST_MAX_BODY_BYTES}
            )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    cleaned_errors = []
    for err in exc.errors():
        cleaned_errors.append({
            "loc": [str(x) for x in err.get("loc", [])],
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", ""))
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Unprocessable Entity", "details": cleaned_errors},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    global _last_error
    _last_error = f"{type(exc).__name__}: {str(exc)}"
    logger.error("Unhandled exception processing %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal Server Error"},
    )


# ------------------------------------------------------------------------------
# PYDANTIC SCHEMAS
# ------------------------------------------------------------------------------
class SensorReadingModel(BaseModel):
    t: int = Field(..., ge=0, description="Timestamp in simulated seconds")
    node_id: str = Field(..., min_length=1, description="EPANET junction ID")
    pressure_m: float = Field(..., description="Pressure in metres")
    flow_lps: Optional[float] = Field(None, description="Flow rate in L/s or null")

    @field_validator("pressure_m")
    @classmethod
    def validate_pressure(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("pressure_m must be a finite number")
        return v

    @field_validator("flow_lps")
    @classmethod
    def validate_flow(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (math.isnan(v) or math.isinf(v)):
            raise ValueError("flow_lps must be a finite number or null")
        return v


class ReplayStartModel(BaseModel):
    scenario_id: str = Field(..., min_length=1)
    speed: float = Field(..., description="Playback speed multiplier (1, 5, or 20)")

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, v: float) -> float:
        if int(v) not in ALLOWED_REPLAY_SPEEDS or not math.isclose(v, int(v)):
            raise ValueError(f"Speed must be one of {ALLOWED_REPLAY_SPEEDS}")
        return float(int(v))


# ------------------------------------------------------------------------------
# WEBSOCKET STREAM
# ------------------------------------------------------------------------------
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    q = await ws_manager.connect(websocket)
    try:
        if pipeline.cached_alert:
            await websocket.send_json(pipeline.cached_alert)
        if pipeline.cached_localization:
            await websocket.send_json(pipeline.cached_localization)
        if pipeline.cached_loss:
            await websocket.send_json(pipeline.cached_loss)

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket client exception: %s", e)
    finally:
        await ws_manager.disconnect(websocket)


# ------------------------------------------------------------------------------
# REST ENDPOINTS (CONTRACT.MD)
# ------------------------------------------------------------------------------
@app.get("/api/network")
async def get_network():
    net_path = MOCK_DIR / "network_net3.json"
    if not net_path.exists():
        raise HTTPException(status_code=500, detail="Network configuration file not found")
    with open(net_path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/scenarios")
async def get_scenarios():
    return replay_engine.get_scenarios_summary()


@app.post("/api/replay/start")
async def start_replay(body: ReplayStartModel):
    if is_warming_up:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ML warming up"}
        )
    try:
        res = await replay_engine.start(scenario_id=body.scenario_id, speed=body.speed)
        return res
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/replay/stop")
async def stop_replay():
    return await replay_engine.stop()


@app.get("/api/results")
async def get_results():
    data_results = DATA_DIR / "results.json"
    if data_results.exists():
        try:
            with open(data_results, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to parse data/results.json: %s; falling back to mock", e)

    mock_results = MOCK_DIR / "results.json"
    if mock_results.exists():
        with open(mock_results, "r", encoding="utf-8") as f:
            return json.load(f)

    raise HTTPException(status_code=500, detail="Results data not found")


@app.get("/api/placement")
async def get_placement(n: int = Query(default=8, ge=1, le=92)):
    data_placement = DATA_DIR / "placement.json"
    if data_placement.exists():
        try:
            with open(data_placement, "r", encoding="utf-8") as f:
                data = json.load(f)
                if str(n) in data:
                    return data[str(n)]
        except Exception:
            pass

    mock_placement = MOCK_DIR / "placement.json"
    if mock_placement.exists():
        with open(mock_placement, "r", encoding="utf-8") as f:
            data = json.load(f)
            if str(n) in data:
                return data[str(n)]
            if data:
                max_key = max(int(k) for k in data.keys())
                max_item = data[str(max_key)]
                return {
                    "sensors": max_item["sensors"][:n],
                    "coverage": round(min(0.99, max_item["coverage"] + 0.01 * (n - max_key)), 3)
                }

    return {"sensors": ["208", "10", "15", "184", "601", "164", "60", "123"][:n], "coverage": 0.88}


@app.get("/api/cost")
async def get_cost(nodes: int = Query(default=8, ge=1)):
    """
    GET /api/cost?nodes=8 -> {'hardware_rs','per_node_rs':1500,'est_loss_avoided_rs'}
    hardware_rs = nodes * 1500
    (ESP32 450 + transducer 650 + enclosure/power/connectors 400)
    """
    hardware_rs = nodes * COST_PER_NODE_RS
    coverage_factor = min(COVERAGE_MAX_RATIO, COVERAGE_BASE_RATIO + COVERAGE_PER_NODE_RATIO * nodes)
    est_loss_avoided_rs = int(nodes * EST_LOSS_SAVED_PER_LEAK_RS * coverage_factor)

    return {
        "hardware_rs": hardware_rs,
        "per_node_rs": COST_PER_NODE_RS,
        "est_loss_avoided_rs": est_loss_avoided_rs
    }


# ------------------------------------------------------------------------------
# ADDITIONAL ENDPOINTS (OUTSIDE CONTRACT.MD)
# ------------------------------------------------------------------------------
@app.post("/api/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_reading(reading: SensorReadingModel):
    if is_warming_up:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ML warming up"}
        )
    await pipeline.ingest_single_reading(reading.model_dump())
    return {"status": "ingested", "node_id": reading.node_id, "t": reading.t}


@app.get("/health")
async def health_check():
    db_ok = db.is_healthy()
    uptime_s = round(time.time() - _start_time, 1)

    return {
        "status": "ok" if db_ok and not is_warming_up else "degraded",
        "uptime_s": uptime_s,
        "ml_mode": ml_bridge.get_mode(),
        "ml_avg_latency_ms": ml_bridge.get_avg_latency_ms(),
        "stub_calls": ml_bridge.stub_calls,
        "last_stub_reason": ml_bridge.last_stub_reason,
        "is_warming_up": is_warming_up,
        "db_ok": db_ok,
        "replay_state": {
            "is_running": replay_engine.is_running,
            "scenario_id": replay_engine.current_scenario_id,
            "speed": replay_engine.current_speed,
        },
        "active_ws_clients": ws_manager.count(),
        "last_error": _last_error,
    }


@app.get("/api/history/runs")
async def get_history_runs():
    return db.get_runs()


@app.get("/api/history/alerts")
async def get_history_alerts(run_id: Optional[str] = Query(None)):
    return db.get_alerts(run_id=run_id)


@app.get("/api/history/alerts/{alert_id}")
async def get_history_alert_detail(alert_id: str):
    detail = db.get_alert_detail(alert_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")
    return detail


@app.get("/api/history/readings")
async def get_history_readings(
    run_id: Optional[str] = Query(None),
    node_id: Optional[str] = Query(None),
    limit: int = Query(default=500, ge=1, le=2000),
):
    return db.get_readings(run_id=run_id, node_id=node_id, limit=limit)


@app.delete("/api/history")
async def clear_history(confirm: bool = Query(default=False)):
    if not confirm:
        raise HTTPException(status_code=400, detail="Missing parameter confirm=true")
    success = db.clear_history()
    return {"status": "cleared" if success else "failed"}
