# AquaSentinel Pipeline Engine
from collections import deque
from typing import Any, Callable, Coroutine, Optional
import asyncio
import logging
import time

from backend.config import (
    DEDUP_RESET_COUNT,
    SEVERITY_CRITICAL_THRESHOLD,
    SEVERITY_HIGH_THRESHOLD,
    SEVERITY_MEDIUM_THRESHOLD,
    WINDOW_SIZE,
)
from backend.db import Database
from backend.ml_bridge import MLBridge

logger = logging.getLogger("aquasentinel.pipeline")


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

        # State tracking
        self.current_run_id: Optional[str] = None
        self.active_alert_id: Optional[str] = None
        self.alert_t: Optional[int] = None
        self.est_leak_lps: float = 0.0
        self.non_anomaly_count: int = 0
        self.latest_t: int = 0

        # Cached latest messages for new WS clients
        self.cached_alert: Optional[dict[str, Any]] = None
        self.cached_localization: Optional[dict[str, Any]] = None
        self.cached_loss: Optional[dict[str, Any]] = None

        # Lock to ensure thread/task safety during concurrent ingest
        self._lock = asyncio.Lock()

        # Load initial state from DB if available
        self._load_initial_db_state()

    def _load_initial_db_state(self) -> None:
        try:
            init_state = self.db.get_latest_state()
            self.cached_alert = init_state.get("alert")
            self.cached_localization = init_state.get("localization")
            self.cached_loss = init_state.get("loss")
        except Exception as e:
            logger.error("Error loading initial state from DB: %s", e)

    def set_current_run(self, run_id: Optional[str]) -> None:
        self.current_run_id = run_id

    def reset_state(self, clear_buffers: bool = True) -> None:
        """Resets active leak state and optionally clears window buffers."""
        if self.active_alert_id and self.db:
            self.db.enqueue("UPDATE_ALERT_STATUS", {"id": self.active_alert_id, "status": "resolved"})

        self.active_alert_id = None
        self.alert_t = None
        self.est_leak_lps = 0.0
        self.non_anomaly_count = 0
        self.cached_loss = None

        if clear_buffers:
            self.node_buffers.clear()
            self.recent_steps.clear()
            self.cached_alert = None
            self.cached_localization = None
            self.cached_loss = None
        logger.info("Pipeline state reset")

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        if self.broadcast_func:
            try:
                await self.broadcast_func(msg)
            except Exception as e:
                logger.error("Error broadcasting message of type %s: %s", msg.get("type"), e)

    async def ingest_single_reading(self, reading: dict[str, Any], run_id: Optional[str] = None) -> None:
        """Handles ingestion from HTTP POST /api/ingest."""
        async with self._lock:
            try:
                t = int(reading.get("t", self.latest_t))
                self.latest_t = max(self.latest_t, t)
                active_run = run_id or self.current_run_id

                # Save reading to DB
                self.db.enqueue("INSERT_READINGS", {
                    "run_id": active_run,
                    "readings": [reading]
                })

                node_id = str(reading.get("node_id", ""))
                if node_id not in self.node_buffers:
                    self.node_buffers[node_id] = deque(maxlen=WINDOW_SIZE)
                self.node_buffers[node_id].append(reading)

                # Formulate a step for detection
                step_obj = {"t": t, "readings": [reading]}
                self.recent_steps.append(step_obj)

                # Broadcast readings message as contract defines:
                # 'readings': {'type': 'readings', 't': t, 'readings': [reading,...]}
                await self._broadcast({
                    "type": "readings",
                    "t": t,
                    "readings": [reading]
                })

                # Process pipeline logic if we have sufficient steps
                if len(self.recent_steps) >= min(WINDOW_SIZE, 3):
                    await self._evaluate_window(list(self.recent_steps), t, active_run)
                elif self.active_alert_id is not None:
                    await self._update_loss_tick(t)

            except Exception as e:
                logger.error("Error processing single reading: %s", e, exc_info=True)

    async def process_step(self, step: dict[str, Any], run_id: Optional[str] = None) -> None:
        """Handles ingestion from Replay engine."""
        async with self._lock:
            try:
                t = int(step.get("t", self.latest_t))
                self.latest_t = t
                readings = step.get("readings", [])
                active_run = run_id or self.current_run_id

                # Record readings batch to DB
                self.db.enqueue("INSERT_READINGS", {
                    "run_id": active_run,
                    "readings": readings
                })

                # Update per-node buffers
                for r in readings:
                    nid = str(r.get("node_id", ""))
                    if nid not in self.node_buffers:
                        self.node_buffers[nid] = deque(maxlen=WINDOW_SIZE)
                    self.node_buffers[nid].append(r)

                self.recent_steps.append(step)

                # Broadcast readings
                await self._broadcast({
                    "type": "readings",
                    "t": t,
                    "readings": readings
                })

                # Evaluate window
                await self._evaluate_window(list(self.recent_steps), t, active_run)

            except Exception as e:
                logger.error("Error processing replay step at t=%d: %s", step.get("t", -1), e, exc_info=True)

    async def _evaluate_window(self, window: list[Any], t: int, active_run: Optional[str]) -> None:
        """Runs detection, deduplication, localization, and loss calculation."""
        detect_res = await self.ml_bridge.detect(window)
        is_anomaly = detect_res.get("anomaly", False)
        score = float(detect_res.get("score", 0.0))

        if is_anomaly:
            self.non_anomaly_count = 0
            # Deduplication: ONLY ONE alert per leak event
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
                self.cached_alert = alert_msg
                logger.info("NEW ALERT created: id=%s t=%d severity=%s score=%.3f",
                            alert_id, t, severity, score)

                # Persist alert to DB
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

                # Localization
                loc_res = await self.ml_bridge.localize(window, self.valid_nodes)
                loc_msg = {
                    "type": "localization",
                    "alert_id": alert_id,
                    "top3": loc_res.get("top3", []),
                    "probs": loc_res.get("probs", {})
                }
                self.cached_localization = loc_msg
                self.db.enqueue("INSERT_LOCALIZATION", {
                    "alert_id": alert_id,
                    "top3": loc_msg["top3"],
                    "probs": loc_msg["probs"]
                })
                await self._broadcast(loc_msg)

                # Size estimation
                est_size = await self.ml_bridge.estimate_size(window)
                self.est_leak_lps = est_size

                # Initial loss message at t=alert_t: litres_lost_cum = 0.0
                initial_loss_msg = {
                    "type": "loss",
                    "alert_id": alert_id,
                    "est_leak_lps": self.est_leak_lps,
                    "litres_lost_cum": 0.0
                }
                self.cached_loss = initial_loss_msg
                self.db.enqueue("INSERT_LOSS", {
                    "alert_id": alert_id,
                    "t": t,
                    "est_leak_lps": self.est_leak_lps,
                    "litres_lost_cum": 0.0
                })
                await self._broadcast(initial_loss_msg)
            else:
                # Alert already active; update cumulative loss for current t
                await self._update_loss_tick(t)
        else:
            # Non-anomaly window
            if self.active_alert_id is not None:
                self.non_anomaly_count += 1
                await self._update_loss_tick(t)
                if self.non_anomaly_count >= DEDUP_RESET_COUNT:
                    logger.info("Leak resolved after %d consecutive non-anomaly windows: alert_id=%s",
                                self.non_anomaly_count, self.active_alert_id)
                    self.db.enqueue("UPDATE_ALERT_STATUS", {"id": self.active_alert_id, "status": "resolved"})
                    self.active_alert_id = None
                    self.alert_t = None
                    self.est_leak_lps = 0.0
                    self.non_anomaly_count = 0

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
        self.cached_loss = loss_msg
        self.db.enqueue("INSERT_LOSS", {
            "alert_id": self.active_alert_id,
            "t": t,
            "est_leak_lps": self.est_leak_lps,
            "litres_lost_cum": litres
        })
        await self._broadcast(loss_msg)

    async def tick_simulated_loss(self, t: int) -> None:
        """Called each simulated second by the replay or loss ticker."""
        async with self._lock:
            if self.active_alert_id is not None and self.alert_t is not None and t >= self.alert_t:
                await self._update_loss_tick(t)
