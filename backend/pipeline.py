# AquaSentinel Pipeline Engine
from collections import deque
from typing import Any, Callable, Coroutine, Optional
import asyncio
import logging
import math
import time

from backend.config import (
    MIN_STEPS_FOR_DETECT,
    RESOLVE_CLEAR_WINDOWS,
    SEVERITY_CRITICAL_THRESHOLD,
    SEVERITY_HIGH_THRESHOLD,
    SEVERITY_MEDIUM_THRESHOLD,
    WINDOW_SIZE,
)
from backend.db import Database
from backend.ml_bridge import MLBridge

logger = logging.getLogger("aquasentinel.pipeline")

ML_SENSORS: set[str] = {"208", "10", "15", "184", "601", "164", "60", "123"}


def score_to_severity(score: float) -> str:
    if score >= SEVERITY_CRITICAL_THRESHOLD:
        return "critical"
    if score >= SEVERITY_HIGH_THRESHOLD:
        return "high"
    if score >= SEVERITY_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


class Pipeline:
    def __init__(
        self,
        db: Database,
        ml_bridge: MLBridge,
        broadcast_func: Optional[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = None,
        valid_nodes: Optional[set[str]] = None,
    ):
        self.db = db
        self.ml_bridge = ml_bridge
        self.broadcast_func = broadcast_func
        self.valid_nodes = valid_nodes or set()

        # Sliding buffers bounded to WINDOW_SIZE
        self.node_buffers: dict[str, deque[dict[str, Any]]] = {}
        self.recent_steps: deque[dict[str, Any]] = deque(maxlen=WINDOW_SIZE)

        # Pending ingest buffer for grouping single readings by t
        self.pending_ingest_t: Optional[int] = None
        self.pending_ingest_readings: list[dict[str, Any]] = []

        # State tracking
        self.current_run_id: Optional[str] = None
        self.current_speed: float = 1.0
        self.active_alert_id: Optional[str] = None
        self.alert_t: Optional[int] = None
        self.est_leak_lps: float = 0.0
        self.non_anomaly_count: int = 0
        self.latest_t: int = 0
        self.fault_active: bool = False
        self._step_evaluated_t: Optional[int] = None

        # Stub tracking per run
        self.run_had_stub: bool = False

        # Throttling tracking
        self.window_eval_counter: int = 0
        self.last_detect_res: Optional[dict[str, Any]] = None
        self._last_throttle_log_t: float = 0.0

        # Cached latest messages for new WS clients
        self.cached_alert: Optional[dict[str, Any]] = None
        self.cached_localization: Optional[dict[str, Any]] = None
        self.cached_loss: Optional[dict[str, Any]] = None

        self._lock = asyncio.Lock()
        self._load_initial_db_state()

    def _load_initial_db_state(self) -> None:
        try:
            init_state = self.db.get_latest_state()
            self.cached_alert = init_state.get("alert")
            self.cached_localization = init_state.get("localization")
            self.cached_loss = init_state.get("loss")
        except Exception as e:
            logger.error("Error loading initial state from DB: %s", e)

    def set_current_run(self, run_id: Optional[str], speed: float = 1.0) -> None:
        self.current_run_id = run_id
        self.current_speed = max(0.1, speed)
        self.run_had_stub = (self.ml_bridge.get_mode() == "stub")
        self.ml_bridge.reset_run_stubs()

    def reset_state(self, clear_buffers: bool = True) -> None:
        """Resets active leak state and clears window buffers."""
        if self.active_alert_id and self.db:
            self.db.enqueue("UPDATE_ALERT_STATUS", {"id": self.active_alert_id, "status": "resolved"})

        self.active_alert_id = None
        self.alert_t = None
        self.est_leak_lps = 0.0
        self.non_anomaly_count = 0
        self.cached_alert = None
        self.cached_localization = None
        self.cached_loss = None
        self.run_had_stub = (self.ml_bridge.get_mode() == "stub")
        self.ml_bridge.reset_run_stubs()
        self.window_eval_counter = 0
        self.last_detect_res = None

        self.fault_active = False
        self._step_evaluated_t = None
        self.pending_ingest_t = None
        self.pending_ingest_readings.clear()

        if clear_buffers:
            self.node_buffers.clear()
            self.recent_steps.clear()
        logger.info("Pipeline state reset")

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        if self.broadcast_func:
            try:
                await self.broadcast_func(msg)
            except Exception as e:
                logger.error("Error broadcasting message of type %s: %s", msg.get("type"), e)

    async def flush_pending_ingest(self, active_run: Optional[str] = None) -> None:
        """Flushes any buffered pending readings into a step and evaluates it."""
        if self.pending_ingest_t is not None and self.pending_ingest_readings:
            step_obj = {
                "type": "readings",
                "t": self.pending_ingest_t,
                "readings": list(self.pending_ingest_readings)
            }
            self.recent_steps.append(step_obj)
            await self._broadcast(step_obj)
            run = active_run or self.current_run_id
            if len(self.recent_steps) >= MIN_STEPS_FOR_DETECT:
                await self._evaluate_window(list(self.recent_steps), self.pending_ingest_t, run)
                self._step_evaluated_t = self.pending_ingest_t
            elif self.active_alert_id is not None:
                await self._update_loss_tick(self.pending_ingest_t)
            self.pending_ingest_t = None
            self.pending_ingest_readings.clear()

    async def ingest_single_reading(self, reading: dict[str, Any], run_id: Optional[str] = None) -> None:
        """Handles ingestion from HTTP POST /api/ingest by grouping readings by t into full steps."""
        async with self._lock:
            try:
                node_id = str(reading.get("node_id", ""))
                if self.valid_nodes and node_id not in self.valid_nodes:
                    return

                t = int(reading.get("t", self.latest_t))
                if self.latest_t - t > 100:
                    logger.info("Detected time rewind (incoming t=%d vs latest_t=%d); resetting buffers and alert state", t, self.latest_t)
                    self.reset_state(clear_buffers=True)
                    self.latest_t = t
                else:
                    self.latest_t = max(self.latest_t, t)

                active_run = run_id or self.current_run_id

                self.db.enqueue("INSERT_READINGS", {
                    "run_id": active_run,
                    "readings": [reading]
                })

                if node_id not in self.node_buffers:
                    self.node_buffers[node_id] = deque(maxlen=WINDOW_SIZE)
                self.node_buffers[node_id].append(reading)

                # Broadcast reading immediately so WebSocket clients receive live telemetry
                await self._broadcast({
                    "type": "readings",
                    "t": t,
                    "readings": [reading]
                })

                # If a newer t arrives and previous step hasn't been evaluated yet, evaluate it
                if self.recent_steps and t > self.recent_steps[-1]["t"]:
                    prev_t = self.recent_steps[-1]["t"]
                    if self._step_evaluated_t != prev_t and len(self.recent_steps) >= MIN_STEPS_FOR_DETECT:
                        await self._evaluate_window(list(self.recent_steps), prev_t, active_run)
                        self._step_evaluated_t = prev_t

                # Grouping by t for the ML sliding window buffer (self.recent_steps):
                if self.recent_steps and self.recent_steps[-1]["t"] == t:
                    step_readings = self.recent_steps[-1]["readings"]
                    idx = next((i for i, r in enumerate(step_readings) if str(r.get("node_id")) == node_id), -1)
                    if idx >= 0:
                        step_readings[idx] = reading
                    else:
                        step_readings.append(reading)
                else:
                    self.recent_steps.append({
                        "type": "readings",
                        "t": t,
                        "readings": [reading]
                    })

                # Evaluate when all 8 sensors for t are in (or non-ML mock sensors, or when a newer t arrives)
                step_nodes = {str(r.get("node_id")) for r in self.recent_steps[-1]["readings"]}
                is_full_step = (len(step_nodes) >= 8) or bool(step_nodes - ML_SENSORS)
                if is_full_step and self._step_evaluated_t != t:
                    if len(self.recent_steps) >= MIN_STEPS_FOR_DETECT:
                        await self._evaluate_window(list(self.recent_steps), t, active_run)
                    elif self.active_alert_id is not None:
                        await self._update_loss_tick(t)
                    self._step_evaluated_t = t
                elif self.active_alert_id is not None and self._step_evaluated_t == t:
                    await self._update_loss_tick(t)

            except Exception as e:
                logger.error("Error processing single reading: %s", e, exc_info=True)

    async def process_step(self, step: dict[str, Any], run_id: Optional[str] = None) -> None:
        """Handles ingestion from Replay engine."""
        async with self._lock:
            try:
                # If there were pending single ingest readings from another source, flush first
                if self.pending_ingest_t is not None and self.pending_ingest_readings:
                    await self.flush_pending_ingest(active_run=run_id)

                t = int(step.get("t", self.latest_t))
                self.latest_t = t
                readings = step.get("readings", [])
                active_run = run_id or self.current_run_id

                self.db.enqueue("INSERT_READINGS", {
                    "run_id": active_run,
                    "readings": readings
                })

                for r in readings:
                    nid = str(r.get("node_id", ""))
                    if nid not in self.node_buffers:
                        self.node_buffers[nid] = deque(maxlen=WINDOW_SIZE)
                    self.node_buffers[nid].append(r)

                step_formatted = {
                    "type": "readings",
                    "t": t,
                    "readings": readings
                }
                self.recent_steps.append(step_formatted)

                await self._broadcast({
                    "type": "readings",
                    "t": t,
                    "readings": readings
                })

                if len(self.recent_steps) >= MIN_STEPS_FOR_DETECT:
                    await self._evaluate_window(list(self.recent_steps), t, active_run)
                elif self.active_alert_id is not None:
                    await self._update_loss_tick(t)

            except Exception as e:
                logger.error("Error processing replay step at t=%d: %s", step.get("t", -1), e, exc_info=True)

    async def _evaluate_window(self, window: list[Any], t: int, active_run: Optional[str]) -> None:
        """Runs detection with speed guard throttling, sensor fault handling, deduplication, localization, and loss calculation."""
        if len(window) < MIN_STEPS_FOR_DETECT:
            return

        # Speed Guard check
        avg_latency = self.ml_bridge.get_avg_latency_ms()
        interval_ms = (10.0 / max(0.1, self.current_speed)) * 1000.0
        n_skip = 1
        if avg_latency > interval_ms:
            n_skip = min(5, max(1, math.ceil(avg_latency / interval_ms)))

        if n_skip > 1:
            self.window_eval_counter += 1
            if (self.window_eval_counter % n_skip != 0) and (self.last_detect_res is not None):
                detect_res = self.last_detect_res
            else:
                now_mono = time.monotonic()
                if now_mono - self._last_throttle_log_t > 5.0:
                    logger.warning(
                        "ML average latency %.1f ms exceeds interval %.1f ms; throttling evaluation to every %d windows",
                        avg_latency, interval_ms, n_skip
                    )
                    self._last_throttle_log_t = now_mono
                detect_res = await self.ml_bridge.detect(window)
                self.last_detect_res = detect_res
        else:
            detect_res = await self.ml_bridge.detect(window)
            self.last_detect_res = detect_res

        if self.ml_bridge.last_result_was_stub:
            self.run_had_stub = True

        is_anomaly = detect_res.get("anomaly", False)
        score = float(detect_res.get("score", 0.0))
        is_fault = detect_res.get("sensor_fault", False)

        # Sensor fault handling
        if is_fault:
            if not self.fault_active:
                self.fault_active = True
                detail = detect_res.get("detail", {"sensor_fault": True, "score": score})
                fault_msg = {
                    "type": "fault",
                    "t": t,
                    "detail": detail
                }
                logger.warning("SENSOR FAULT detected at t=%d: %s", t, detail)
                self.db.enqueue("INSERT_ALERT", {
                    "id": f"FLT-{int(time.time() * 1000)}",
                    "run_id": active_run,
                    "t": t,
                    "severity": "medium",
                    "score": score,
                    "detector": "sensor_health",
                    "status": "sensor_fault"
                })
                await self._broadcast(fault_msg)
            # When detector reports sensor fault, do not create leak alert, localization, or loss
            return
        else:
            self.fault_active = False

        if is_anomaly:
            self.non_anomaly_count = 0
            if self.active_alert_id is None:
                alert_id = f"ALT-{int(time.time() * 1000)}"
                severity = score_to_severity(score)
                self.active_alert_id = alert_id
                self.alert_t = t

                alert_msg = {
                    "type": "alert",
                    "id": alert_id,
                    "t": t,
                    "severity": severity,
                    "score": score,
                    "detector": "statistical"
                }
                if self.run_had_stub:
                    alert_msg["stub"] = True

                self.cached_alert = alert_msg
                logger.info("NEW ALERT created: id=%s t=%d severity=%s score=%.3f",
                            alert_id, t, severity, score)

                self.db.enqueue("INSERT_ALERT", {
                    "id": alert_id,
                    "run_id": active_run,
                    "t": t,
                    "severity": severity,
                    "score": score,
                    "detector": "statistical",
                    "status": "active"
                })
                await self._broadcast(alert_msg)

                loc_res = await self.ml_bridge.localize(window, self.valid_nodes)
                if self.ml_bridge.last_result_was_stub:
                    self.run_had_stub = True

                loc_msg = {
                    "type": "localization",
                    "alert_id": alert_id,
                    "top3": loc_res.get("top3", []),
                    "probs": loc_res.get("probs", {})
                }
                if self.run_had_stub:
                    loc_msg["stub"] = True

                self.cached_localization = loc_msg
                self.db.enqueue("INSERT_LOCALIZATION", {
                    "alert_id": alert_id,
                    "top3": loc_msg["top3"],
                    "probs": loc_msg["probs"]
                })
                await self._broadcast(loc_msg)

                est_size = await self.ml_bridge.estimate_size(window)
                if self.ml_bridge.last_result_was_stub:
                    self.run_had_stub = True
                self.est_leak_lps = est_size

                initial_loss_msg = {
                    "type": "loss",
                    "alert_id": alert_id,
                    "est_leak_lps": self.est_leak_lps,
                    "litres_lost_cum": 0.0
                }
                if self.run_had_stub:
                    initial_loss_msg["stub"] = True

                self.cached_loss = initial_loss_msg
                self.db.enqueue("INSERT_LOSS", {
                    "alert_id": alert_id,
                    "t": t,
                    "est_leak_lps": self.est_leak_lps,
                    "litres_lost_cum": 0.0
                })
                await self._broadcast(initial_loss_msg)
            else:
                await self._update_loss_tick(t)
        else:
            if self.active_alert_id is not None:
                self.non_anomaly_count += 1
                if self.non_anomaly_count >= RESOLVE_CLEAR_WINDOWS:
                    logger.info("Leak resolved after %d consecutive non-anomaly windows: alert_id=%s",
                                self.non_anomaly_count, self.active_alert_id)
                    self.db.enqueue("UPDATE_ALERT_STATUS", {"id": self.active_alert_id, "status": "resolved"})
                    self.active_alert_id = None
                    self.alert_t = None
                    self.est_leak_lps = 0.0
                    self.non_anomaly_count = 0
                    self.cached_loss = None
                else:
                    await self._update_loss_tick(t)

    async def _update_loss_tick(self, t: int) -> None:
        if self.active_alert_id is None or self.alert_t is None:
            return

        dt = max(0, t - self.alert_t)
        litres = max(0.0, round(self.est_leak_lps * dt, 1))

        loss_msg = {
            "type": "loss",
            "alert_id": self.active_alert_id,
            "est_leak_lps": self.est_leak_lps,
            "litres_lost_cum": litres
        }
        if self.run_had_stub:
            loss_msg["stub"] = True

        self.cached_loss = loss_msg
        self.db.enqueue("INSERT_LOSS", {
            "alert_id": self.active_alert_id,
            "t": t,
            "est_leak_lps": self.est_leak_lps,
            "litres_lost_cum": litres
        })
        await self._broadcast(loss_msg)

    async def tick_simulated_loss(self, t: int) -> None:
        async with self._lock:
            if self.active_alert_id is not None and self.alert_t is not None and t >= self.alert_t:
                await self._update_loss_tick(t)
