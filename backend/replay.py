# AquaSentinel Replay Engine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import asyncio
import json
import logging
import time

from backend.config import (
    ALLOWED_REPLAY_SPEEDS,
    DATA_REPLAY_DIR,
    MOCK_DIR,
    MOCK_REPLAY_DIR,
)
from backend.db import Database
from backend.pipeline import Pipeline

logger = logging.getLogger("aquasentinel.replay")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplayEngine:
    def __init__(self, pipeline: Pipeline, db: Database):
        self.pipeline = pipeline
        self.db = db
        self.current_task: Optional[asyncio.Task] = None
        self.current_run_id: Optional[str] = None
        self.current_scenario_id: Optional[str] = None
        self.current_speed: float = 1.0
        self.is_running: bool = False
        self._lock = asyncio.Lock()
        self.scenarios_cache: dict[str, dict[str, Any]] = {}
        self.load_all_scenarios()

    def load_all_scenarios(self) -> dict[str, dict[str, Any]]:
        scenarios: dict[str, dict[str, Any]] = {}

        # 1. Load audited replay traces using the adjacent catalog metadata.
        if DATA_REPLAY_DIR.exists():
            catalog_path = DATA_REPLAY_DIR / "scenarios_catalog.json"
            catalog: list[dict[str, Any]] = []
            if catalog_path.exists():
                try:
                    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                    if isinstance(raw_catalog, list):
                        catalog = [item for item in raw_catalog if isinstance(item, dict)]
                except Exception as e:
                    logger.warning("Failed to read replay catalog %s: %s", catalog_path.name, e)
            for item in catalog:
                filename = item.get("filename")
                if not isinstance(filename, str):
                    continue
                f = DATA_REPLAY_DIR / filename
                if not f.is_file():
                    logger.warning("Replay file missing from catalog: %s", filename)
                    continue
                sc = self._validate_and_load_file(f, item)
                if sc:
                    scenarios[sc["scenario_id"]] = sc

        # 2. Keep mock-only scenarios, including the no-leak baseline.
        if MOCK_REPLAY_DIR.exists():
            for f in sorted(MOCK_REPLAY_DIR.glob("*.json")):
                sc = self._validate_and_load_file(f)
                if sc:
                    scenarios.setdefault(sc["scenario_id"], sc)

        self.scenarios_cache = scenarios
        logger.info("Loaded %d replay scenarios", len(scenarios))
        return scenarios

    def _validate_and_load_file(
        self,
        file_path: Path,
        catalog_entry: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            metadata = catalog_entry or (data if isinstance(data, dict) else {})
            steps = data if isinstance(data, list) else metadata.get("steps")
            # Support catalog, scenario_id and id metadata forms.
            sc_id = metadata.get("scenario_id") or metadata.get("id")
            if not sc_id or not isinstance(sc_id, str):
                logger.warning("Skipping scenario file %s: missing scenario_id", file_path.name)
                return None

            if not isinstance(steps, list):
                logger.warning("Skipping scenario file %s: missing or invalid steps list", file_path.name)
                return None

            # Validate at least one step structure
            for s in steps:
                if not isinstance(s, dict) or "t" not in s or "readings" not in s:
                    logger.warning("Skipping scenario file %s: malformed step element", file_path.name)
                    return None

            return {
                "scenario_id": sc_id,
                "leak_node": metadata.get("leak_node"),
                "leak_size_lps": float(metadata.get("leak_size_lps", 0.0)),
                "start_t": int(metadata.get("start_t", 0)),
                "description": metadata.get("description", f"Replay scenario {sc_id}"),
                "steps": steps,
            }
        except Exception as e:
            logger.warning("Failed to load scenario file %s: %s", file_path.name, e)
            return None

    def get_scenarios_summary(self) -> list[dict[str, Any]]:
        """Matches CONTRACT.md: GET /api/scenarios -> [{'id','leak_node','leak_size_lps','start_t','description'}]"""
        if not self.scenarios_cache:
            self.load_all_scenarios()

        summary = []
        for sc in self.scenarios_cache.values():
            summary.append({
                "id": sc["scenario_id"],
                "leak_node": sc["leak_node"],
                "leak_size_lps": sc["leak_size_lps"],
                "start_t": sc["start_t"],
                "description": sc["description"]
            })
        return summary

    async def start(self, scenario_id: str, speed: float) -> dict[str, Any]:
        async with self._lock:
            # Refresh if scenario missing
            if scenario_id not in self.scenarios_cache:
                self.load_all_scenarios()

            if scenario_id not in self.scenarios_cache:
                raise KeyError(f"Unknown scenario_id: '{scenario_id}'")

            int_speed = int(speed) if isinstance(speed, (int, float)) and speed in ALLOWED_REPLAY_SPEEDS else None
            if int_speed is None:
                raise ValueError(f"Invalid speed {speed}. Allowed speeds: {ALLOWED_REPLAY_SPEEDS}")

            # Stop existing playback first
            if self.is_running:
                await self._stop_internal()

            # Reset pipeline state and prepare run
            self.pipeline.reset_state(clear_buffers=True)
            run_id = f"RUN-{scenario_id}-{int(time.time())}"
            self.current_run_id = run_id
            self.current_scenario_id = scenario_id
            self.current_speed = float(int_speed)
            self.is_running = True

            self.pipeline.set_current_run(run_id)
            self.db.enqueue("INSERT_RUN", {
                "id": run_id,
                "scenario_id": scenario_id,
                "speed": self.current_speed,
                "source": "replay",
                "started_at": _utcnow_iso()
            })

            scenario = self.scenarios_cache[scenario_id]
            self.current_task = asyncio.create_task(
                self._playback_loop(scenario, self.current_speed, run_id)
            )
            logger.info("REPLAY STARTED: scenario=%s speed=%.1fx run_id=%s",
                        scenario_id, self.current_speed, run_id)
            return {
                "status": "started",
                "run_id": run_id,
                "scenario_id": scenario_id,
                "speed": self.current_speed
            }

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            return await self._stop_internal()

    async def _stop_internal(self) -> dict[str, Any]:
        if not self.is_running and self.current_task is None:
            return {"status": "stopped"}

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Error awaiting replay task cancellation: %s", e)

        self.current_task = None
        self.is_running = False

        if self.current_run_id:
            self.db.enqueue("UPDATE_RUN_END", {
                "id": self.current_run_id,
                "ended_at": _utcnow_iso()
            })
            logger.info("REPLAY STOPPED: run_id=%s", self.current_run_id)

        self.pipeline.reset_state(clear_buffers=False)
        return {"status": "stopped"}

    async def _playback_loop(self, scenario: dict[str, Any], speed: float, run_id: str) -> None:
        steps = scenario.get("steps", [])
        if not steps:
            self.is_running = False
            return

        prev_t = 0
        try:
            for step in steps:
                step_t = int(step.get("t", 0))

                # If there is a gap between previous step and current step, tick loss every simulated second
                if step_t > prev_t:
                    delta_sim_seconds = step_t - prev_t
                    sleep_per_second = 1.0 / speed
                    for sec in range(1, delta_sim_seconds):
                        curr_sim_t = prev_t + sec
                        await self.pipeline.tick_simulated_loss(curr_sim_t)
                        await asyncio.sleep(sleep_per_second)

                # Process the actual step
                await self.pipeline.process_step(step, run_id=run_id)
                prev_t = step_t

                # Step pause (default 10s step / speed)
                step_duration_s = 10.0 / speed
                # Tick loss every simulated second within the step duration
                for sec in range(1, 10):
                    await asyncio.sleep(1.0 / speed)
                    await self.pipeline.tick_simulated_loss(step_t + sec)

            logger.info("Replay finished playback for scenario %s", scenario.get("scenario_id"))
        except asyncio.CancelledError:
            logger.info("Playback loop cancelled for run %s", run_id)
            raise
        except Exception as e:
            logger.error("Unexpected error in playback loop: %s", e, exc_info=True)
        finally:
            self.is_running = False
            if self.current_run_id == run_id:
                self.db.enqueue("UPDATE_RUN_END", {
                    "id": run_id,
                    "ended_at": _utcnow_iso()
                })
