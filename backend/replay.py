# AquaSentinel Replay Engine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import asyncio
import json
import logging
import math
import time

from backend.config import (
    ALLOWED_REPLAY_SPEEDS,
    DATA_REPLAY_DIR,
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

        catalog_aliases: list[tuple[str, dict[str, Any]]] = []
        catalog_files: set[Path] = set()
        if DATA_REPLAY_DIR.is_dir():
            catalog_path = DATA_REPLAY_DIR / "scenarios_catalog.json"
            if catalog_path.is_file():
                try:
                    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                    if not isinstance(raw_catalog, list):
                        logger.warning("Skipping replay catalog %s: expected a list", catalog_path.name)
                        raw_catalog = []
                except Exception as e:
                    logger.warning("Failed to read replay catalog %s: %s", catalog_path.name, e)
                    raw_catalog = []

                # First pass preserves canonical catalog order and protects IDs
                # from collisions with filename aliases.
                for item in raw_catalog:
                    if not isinstance(item, dict):
                        logger.warning("Skipping non-object entry in replay catalog %s", catalog_path.name)
                        continue
                    filename = item.get("filename")
                    if not isinstance(filename, str) or not filename.strip() or Path(filename).name != filename:
                        logger.warning("Skipping replay catalog entry with invalid filename: %r", filename)
                        continue
                    file_path = DATA_REPLAY_DIR / filename
                    catalog_files.add(file_path)
                    if not file_path.is_file():
                        logger.warning("Replay file missing from catalog: %s", filename)
                        continue
                    try:
                        file_path.resolve().relative_to(DATA_REPLAY_DIR.resolve())
                    except ValueError:
                        logger.warning("Skipping replay file outside data directory: %s", filename)
                        continue

                    scenario = self._validate_and_load_file(file_path, item)
                    if not scenario:
                        continue
                    scenario_id = scenario["scenario_id"]
                    if scenario_id in scenarios:
                        logger.warning("Duplicate replay scenario ID in catalog; keeping first: %s", scenario_id)
                        continue
                    scenarios[scenario_id] = scenario
                    alias = file_path.stem
                    if alias != scenario_id:
                        catalog_aliases.append((alias, scenario))

                canonical_ids = set(scenarios)
                for alias, scenario in catalog_aliases:
                    if alias not in canonical_ids:
                        scenarios.setdefault(alias, scenario)

            # Preserve additional audited JSON traces not listed in the catalog.
            for file_path in sorted(DATA_REPLAY_DIR.glob("*.json")):
                if file_path.name == "scenarios_catalog.json" or file_path in catalog_files:
                    continue
                scenario = self._validate_and_load_file(file_path)
                if scenario:
                    scenarios.setdefault(scenario["scenario_id"], scenario)

        # Mock replay files are fallback scenarios and never replace audited entries.
        if MOCK_REPLAY_DIR.is_dir():
            for file_path in sorted(MOCK_REPLAY_DIR.glob("*.json")):
                scenario = self._validate_and_load_file(file_path)
                if scenario:
                    scenarios.setdefault(scenario["scenario_id"], scenario)

        self.scenarios_cache = scenarios
        logger.info("Loaded %d replay scenarios", len(scenarios))
        return scenarios

    def _validate_and_load_file(
        self,
        file_path: Path,
        catalog_entry: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                steps = data
                data_metadata: dict[str, Any] = {}
            elif isinstance(data, dict):
                steps = data.get("steps")
                data_metadata = data
            else:
                logger.warning("Skipping scenario file %s: expected an object or steps list", file_path.name)
                return None

            metadata = catalog_entry if catalog_entry is not None else data_metadata
            if catalog_entry is not None:
                scenario_id = metadata.get("scenario_id") or metadata.get("id")
            else:
                scenario_id = metadata.get("scenario_id") or metadata.get("id") or file_path.stem
            if not isinstance(scenario_id, str) or not scenario_id.strip():
                logger.warning("Skipping scenario file %s: missing or invalid scenario ID", file_path.name)
                return None
            if not isinstance(steps, list) or not steps:
                logger.warning("Skipping scenario file %s: missing or invalid steps list", file_path.name)
                return None

            previous_t: Optional[int] = None
            for step in steps:
                if not isinstance(step, dict) or "t" not in step or not isinstance(step.get("readings"), list):
                    logger.warning("Skipping scenario file %s: malformed step element", file_path.name)
                    return None
                step_t = step["t"]
                if isinstance(step_t, bool) or not isinstance(step_t, (int, float)) or not math.isfinite(step_t) or int(step_t) != step_t or step_t < 0:
                    logger.warning("Skipping scenario file %s: invalid step time", file_path.name)
                    return None
                if previous_t is not None and step_t < previous_t:
                    logger.warning("Skipping scenario file %s: step times are out of order", file_path.name)
                    return None
                if any(not isinstance(reading, dict) for reading in step["readings"]):
                    logger.warning("Skipping scenario file %s: invalid reading in step", file_path.name)
                    return None
                previous_t = int(step_t)

            leak_size = metadata.get("leak_size_lps", 0.0)
            start_t = metadata.get("start_t", 0)
            if isinstance(leak_size, bool) or not isinstance(leak_size, (int, float)) or not math.isfinite(leak_size) or leak_size < 0:
                logger.warning("Skipping scenario file %s: invalid leak_size_lps", file_path.name)
                return None
            if isinstance(start_t, bool) or not isinstance(start_t, (int, float)) or not math.isfinite(start_t) or int(start_t) != start_t or start_t < 0:
                logger.warning("Skipping scenario file %s: invalid start_t", file_path.name)
                return None
            description = metadata.get("description", f"Replay scenario {scenario_id}")
            if not isinstance(description, str):
                logger.warning("Skipping scenario file %s: invalid description", file_path.name)
                return None
            leak_node = metadata.get("leak_node")

            return {
                "scenario_id": scenario_id,
                "leak_node": str(leak_node) if leak_node else None,
                "leak_size_lps": float(leak_size),
                "start_t": int(start_t),
                "description": description,
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
        seen_ids = set()
        for sc_id, sc in self.scenarios_cache.items():
            actual_id = sc["scenario_id"]
            if actual_id in seen_ids:
                continue
            seen_ids.add(actual_id)
            summary.append({
                "id": actual_id,
                "leak_node": sc["leak_node"],
                "leak_size_lps": sc["leak_size_lps"],
                "start_t": sc["start_t"],
                "description": sc["description"]
            })
        return summary

    async def start(self, scenario_id: str, speed: float) -> dict[str, Any]:
        async with self._lock:
            if scenario_id not in self.scenarios_cache:
                self.load_all_scenarios()

            if scenario_id not in self.scenarios_cache:
                raise KeyError(f"Unknown scenario_id: '{scenario_id}'")

            int_speed = int(speed) if isinstance(speed, (int, float)) and speed in ALLOWED_REPLAY_SPEEDS else None
            if int_speed is None:
                raise ValueError(f"Invalid speed {speed}. Allowed speeds: {ALLOWED_REPLAY_SPEEDS}")

            if self.is_running:
                await self._stop_internal()

            self.pipeline.reset_state(clear_buffers=True)
            run_id = f"RUN-{scenario_id}-{int(time.time())}"
            self.current_run_id = run_id
            self.current_scenario_id = scenario_id
            self.current_speed = float(int_speed)
            self.is_running = True

            self.pipeline.set_current_run(run_id, speed=self.current_speed)
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

        try:
            sleep_per_second = 1.0 / speed
            for index, step in enumerate(steps):
                step_t = int(step.get("t", 0))
                await self.pipeline.process_step(step, run_id=run_id)

                next_t = int(steps[index + 1].get("t", step_t + 10)) if index + 1 < len(steps) else step_t + 10
                # Advance simulated time between samples without duplicating ticks
                # when a scenario has gaps larger than its usual 10 second cadence.
                for tick_t in range(step_t + 1, max(step_t + 1, next_t)):
                    await asyncio.sleep(sleep_per_second)
                    await self.pipeline.tick_simulated_loss(tick_t)

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
            # Ensure pipeline alert state is reset / loss ticks stop when replay ends or stops
            self.pipeline.reset_state(clear_buffers=False)
