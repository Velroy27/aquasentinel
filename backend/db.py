# AquaSentinel Persistence Layer (SQLite with background batch writer)
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
import json
import logging
import queue
import sqlite3
import threading
import time

from backend.config import DB_PATH, ENABLE_DB, READINGS_RETENTION_DAYS

logger = logging.getLogger("aquasentinel.db")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Optional[Path] = None, enable: Optional[bool] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.enabled = ENABLE_DB if enable is None else enable
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._is_ready = False
        
        if self.enabled:
            self.init_db()
            self._start_worker()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_db(self) -> None:
        if not self.enabled:
            return
        try:
            with self._get_connection() as conn:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        scenario_id TEXT,
                        speed REAL,
                        source TEXT,
                        started_at TEXT,
                        ended_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS readings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT,
                        t INTEGER,
                        node_id TEXT,
                        pressure_m REAL,
                        flow_lps REAL,
                        received_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS alerts (
                        id TEXT PRIMARY KEY,
                        run_id TEXT,
                        t INTEGER,
                        severity TEXT,
                        score REAL,
                        detector TEXT,
                        status TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS localizations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_id TEXT,
                        top3_json TEXT,
                        probs_json TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS losses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_id TEXT,
                        t INTEGER,
                        est_leak_lps REAL,
                        litres_lost_cum REAL,
                        created_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_readings_run_t ON readings(run_id, t);
                    CREATE INDEX IF NOT EXISTS idx_readings_node ON readings(node_id);
                    CREATE INDEX IF NOT EXISTS idx_alerts_run ON alerts(run_id);
                    CREATE INDEX IF NOT EXISTS idx_localizations_alert ON localizations(alert_id);
                    CREATE INDEX IF NOT EXISTS idx_losses_alert ON losses(alert_id);
                ''')
                # Prune old readings older than retention limit
                retention_cutoff = (datetime.now(timezone.utc) - timedelta(days=READINGS_RETENTION_DAYS)).isoformat()
                conn.execute("DELETE FROM readings WHERE received_at < ?;", (retention_cutoff,))
                # If an active alert exists without completion, mark it interrupted so litres do not leak across restarts
                conn.execute("UPDATE alerts SET status = 'interrupted' WHERE status = 'active';")
                conn.commit()
            self._is_ready = True
            logger.info("Database initialized at %s", self.db_path)
        except Exception as e:
            logger.error("Database initialization failed: %s", e, exc_info=True)
            self._is_ready = False

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="DBWriter")
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            # Batch process items
            batch = [item]
            while not self._queue.empty() and len(batch) < 100:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            try:
                self._execute_batch(batch)
            except Exception as e:
                logger.error("Failed to execute DB batch of %d items: %s", len(batch), e, exc_info=True)
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _execute_batch(self, batch: list[tuple[str, Any]]) -> None:
        if not self._is_ready:
            try:
                self.init_db()
            except Exception:
                return

        with self._get_connection() as conn:
            with conn:
                for action, data in batch:
                    try:
                        if action == "INSERT_RUN":
                            conn.execute(
                                "INSERT OR REPLACE INTO runs (id, scenario_id, speed, source, started_at, ended_at) "
                                "VALUES (?, ?, ?, ?, ?, ?);",
                                (data["id"], data.get("scenario_id"), data.get("speed", 1.0),
                                 data.get("source", "replay"), data.get("started_at", _utcnow_iso()), data.get("ended_at"))
                            )
                        elif action == "UPDATE_RUN_END":
                            conn.execute(
                                "UPDATE runs SET ended_at = ? WHERE id = ?;",
                                (data["ended_at"], data["id"])
                            )
                        elif action == "INSERT_READINGS":
                            run_id = data.get("run_id")
                            rcv = data.get("received_at", _utcnow_iso())
                            rows = [
                                (run_id, r.get("t", 0), str(r.get("node_id")), r.get("pressure_m"), r.get("flow_lps"), rcv)
                                for r in data.get("readings", [])
                            ]
                            conn.executemany(
                                "INSERT INTO readings (run_id, t, node_id, pressure_m, flow_lps, received_at) "
                                "VALUES (?, ?, ?, ?, ?, ?);",
                                rows
                            )
                        elif action == "INSERT_ALERT":
                            conn.execute(
                                "INSERT OR REPLACE INTO alerts (id, run_id, t, severity, score, detector, status, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                                (data["id"], data.get("run_id"), data.get("t", 0), data.get("severity"),
                                 data.get("score"), data.get("detector", "statistical"),
                                 data.get("status", "active"), data.get("created_at", _utcnow_iso()))
                            )
                        elif action == "UPDATE_ALERT_STATUS":
                            conn.execute(
                                "UPDATE alerts SET status = ? WHERE id = ?;",
                                (data["status"], data["id"])
                            )
                        elif action == "INSERT_LOCALIZATION":
                            conn.execute(
                                "INSERT INTO localizations (alert_id, top3_json, probs_json, created_at) "
                                "VALUES (?, ?, ?, ?);",
                                (data["alert_id"], json.dumps(data.get("top3", [])),
                                 json.dumps(data.get("probs", {})), data.get("created_at", _utcnow_iso()))
                            )
                        elif action == "INSERT_LOSS":
                            conn.execute(
                                "INSERT INTO losses (alert_id, t, est_leak_lps, litres_lost_cum, created_at) "
                                "VALUES (?, ?, ?, ?, ?);",
                                (data["alert_id"], data.get("t", 0), data.get("est_leak_lps", 0.0),
                                 data.get("litres_lost_cum", 0.0), data.get("created_at", _utcnow_iso()))
                            )
                        elif action == "CLEAR_ALL":
                            conn.executescript('''
                                DELETE FROM runs;
                                DELETE FROM readings;
                                DELETE FROM alerts;
                                DELETE FROM localizations;
                                DELETE FROM losses;
                            ''')
                    except Exception as err:
                        logger.error("Error executing DB item %s: %s", action, err)

    def enqueue(self, action: str, data: Any) -> bool:
        if not self.enabled:
            return True
        try:
            self._queue.put_nowait((action, data))
            return True
        except queue.Full:
            if action == "INSERT_READINGS":
                logger.warning("DB queue full: dropping oldest readings batch to preserve responsiveness")
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self._queue.put_nowait((action, data))
                    return True
                except Exception:
                    return False
            else:
                try:
                    self._queue.put((action, data), timeout=0.5)
                    return True
                except Exception as e:
                    logger.error("Failed to enqueue high priority DB item %s: %s", action, e)
                    return False

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

    def is_healthy(self) -> bool:
        if not self.enabled:
            return True
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1;").fetchone()
            return True
        except Exception:
            return False

    def get_runs(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 50;").fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to get runs: %s", e)
            return []

    def get_alerts(self, run_id: Optional[str] = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._get_connection() as conn:
                if run_id:
                    rows = conn.execute("SELECT * FROM alerts WHERE run_id = ? ORDER BY t ASC;", (run_id,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100;").fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to get alerts: %s", e)
            return []

    def get_alert_detail(self, alert_id: str) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return None
        try:
            with self._get_connection() as conn:
                alert_row = conn.execute("SELECT * FROM alerts WHERE id = ?;", (alert_id,)).fetchone()
                if not alert_row:
                    return None
                res = dict(alert_row)
                loc_row = conn.execute("SELECT * FROM localizations WHERE alert_id = ? ORDER BY id DESC LIMIT 1;", (alert_id,)).fetchone()
                if loc_row:
                    res["localization"] = {
                        "top3": json.loads(loc_row["top3_json"]),
                        "probs": json.loads(loc_row["probs_json"]),
                        "created_at": loc_row["created_at"]
                    }
                else:
                    res["localization"] = None

                loss_rows = conn.execute("SELECT t, est_leak_lps, litres_lost_cum, created_at FROM losses WHERE alert_id = ? ORDER BY t ASC;", (alert_id,)).fetchall()
                res["loss_timeline"] = [dict(lr) for lr in loss_rows]
                return res
        except Exception as e:
            logger.error("Failed to get alert detail for %s: %s", alert_id, e)
            return None

    def get_readings(self, run_id: Optional[str] = None, node_id: Optional[str] = None, limit: int = 500) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        clamped_limit = max(1, min(limit, 2000))
        try:
            with self._get_connection() as conn:
                query = "SELECT t, node_id, pressure_m, flow_lps, received_at FROM readings"
                params = []
                clauses = []
                if run_id:
                    clauses.append("run_id = ?")
                    params.append(run_id)
                if node_id:
                    clauses.append("node_id = ?")
                    params.append(str(node_id))
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " ORDER BY id DESC LIMIT ?;"
                params.append(clamped_limit)
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in reversed(rows)]
        except Exception as e:
            logger.error("Failed to get readings: %s", e)
            return []

    def clear_history(self) -> bool:
        if not self.enabled:
            return True
        try:
            self.enqueue("CLEAR_ALL", {})
            time.sleep(0.1)
            return True
        except Exception as e:
            logger.error("Failed to clear history: %s", e)
            return False

    def get_latest_state(self) -> dict[str, Any]:
        """Load the latest run's alert, localization and loss state on startup."""
        if not self.enabled:
            return {"alert": None, "localization": None, "loss": None}
        try:
            with self._get_connection() as conn:
                alert_row = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 1;").fetchone()
                if not alert_row:
                    return {"alert": None, "localization": None, "loss": None}
                
                alert_dict = {
                    "type": "alert",
                    "id": alert_row["id"],
                    "t": alert_row["t"],
                    "severity": alert_row["severity"],
                    "score": alert_row["score"],
                    "detector": alert_row["detector"]
                }

                loc_dict = None
                loc_row = conn.execute("SELECT * FROM localizations WHERE alert_id = ? ORDER BY id DESC LIMIT 1;", (alert_row["id"],)).fetchone()
                if loc_row:
                    loc_dict = {
                        "type": "localization",
                        "alert_id": alert_row["id"],
                        "top3": json.loads(loc_row["top3_json"]),
                        "probs": json.loads(loc_row["probs_json"])
                    }

                loss_dict = None
                loss_row = conn.execute("SELECT * FROM losses WHERE alert_id = ? ORDER BY id DESC LIMIT 1;", (alert_row["id"],)).fetchone()
                if loss_row:
                    loss_dict = {
                        "type": "loss",
                        "alert_id": alert_row["id"],
                        "est_leak_lps": loss_row["est_leak_lps"],
                        "litres_lost_cum": loss_row["litres_lost_cum"]
                    }

                return {
                    "alert": alert_dict,
                    "localization": loc_dict,
                    "loss": loss_dict
                }
        except Exception as e:
            logger.error("Failed to get latest state from DB: %s", e)
            return {"alert": None, "localization": None, "loss": None}
