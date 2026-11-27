# AquaSentinel ML Bridge & Stub Fallbacks
from pathlib import Path
from typing import Any, Optional
import asyncio
import concurrent.futures
import logging
import math
import sys

from backend.config import ML_TIMEOUT_S, REPO_ROOT

logger = logging.getLogger("aquasentinel.ml_bridge")

# Add repository root to sys.path to allow importing from /ml if present
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Real vs Stub flag
ML_MODE = "stub"
_real_detect = None
_real_localize = None
_real_estimate_size = None

try:
    from ml.api import detect as _ml_detect, localize as _ml_localize, estimate_size as _ml_est
    _real_detect = _ml_detect
    _real_localize = _ml_localize
    _real_estimate_size = _ml_est
    ML_MODE = "real"
    logger.info("Successfully loaded ML module from ml.api")
except ImportError:
    ML_MODE = "stub"
    logger.warning("using ML stubs")
except Exception as e:
    ML_MODE = "stub"
    logger.warning("Failed importing ml.api: %s; using ML stubs", e)


def stub_detect(window: list[Any]) -> dict[str, Any]:
    """Conservative fallback detector based on within-sensor pressure movement."""
    if not window:
        return {"anomaly": False, "score": 0.05}

    # Comparing pressure across different nodes mistakes normal elevation and
    # hydraulic head differences for a leak. Only compare readings from the
    # same node across the active time window.
    pressure_by_node: dict[str, list[float]] = {}
    for item in window:
        if isinstance(item, dict):
            readings = item.get("readings") if isinstance(item.get("readings"), list) else [item]
            for reading in readings:
                if not isinstance(reading, dict):
                    continue
                node_id = str(reading.get("node_id", ""))
                pressure = reading.get("pressure_m")
                if node_id and isinstance(pressure, (int, float)) and math.isfinite(pressure):
                    pressure_by_node.setdefault(node_id, []).append(float(pressure))

    node_ranges = [max(values) - min(values) for values in pressure_by_node.values() if len(values) > 1]
    if not node_ranges:
        return {"anomaly": False, "score": 0.05}

    largest_change = max(node_ranges)
    if largest_change >= 2.5:
        score = min(0.99, max(0.55, 0.50 + largest_change * 0.1))
        return {"anomaly": True, "score": round(score, 3)}
    return {"anomaly": False, "score": 0.08}


def stub_localize(window: list[Any]) -> dict[str, Any]:
    """Deterministic stub for localize(window) -> {'probs': {node: prob}}."""
    node_drops: dict[str, list[float]] = {}
    for item in window:
        if isinstance(item, dict):
            readings = item.get("readings", [item]) if "readings" in item else [item]
            for r in readings:
                nid = str(r.get("node_id", ""))
                p = r.get("pressure_m")
                if nid and isinstance(p, (int, float)) and not math.isnan(p):
                    node_drops.setdefault(nid, []).append(float(p))

    # Score nodes by lowest pressure or biggest delta
    scored: list[tuple[str, float]] = []
    for nid, plist in node_drops.items():
        if plist:
            drop = (sum(plist) / len(plist)) - min(plist)
            scored.append((nid, max(drop, 0.01)))

    if not scored:
        # Fallback default net3 nodes
        return {"probs": {"105": 0.70, "115": 0.20, "123": 0.10}}

    scored.sort(key=lambda x: x[1], reverse=True)
    total_score = sum(s[1] for s in scored) + 1e-6
    probs = {nid: round(s / total_score, 4) for nid, s in scored}
    return {"probs": probs}


def stub_estimate_size(window: list[Any]) -> float:
    """Deterministic stub for estimate_size(window) -> float lps."""
    if not window:
        return 5.0
    pressures: list[float] = []
    for item in window:
        if isinstance(item, dict):
            readings = item.get("readings", [item]) if "readings" in item else [item]
            for r in readings:
                p = r.get("pressure_m")
                if isinstance(p, (int, float)) and not math.isnan(p):
                    pressures.append(float(p))
    if pressures:
        drop = (sum(pressures) / len(pressures)) - min(pressures)
        return round(max(1.5, min(20.0, drop * 1.8)), 1)
    return 5.5


class MLBridge:
    def __init__(self, thread_pool_size: int = 4):
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=thread_pool_size, thread_name_prefix="MLBridge"
        )

    def get_mode(self) -> str:
        return ML_MODE

    async def detect(self, window: list[Any]) -> dict[str, Any]:
        """Runs detection with timeout, fallback, and schema validation."""
        raw_res = None
        if ML_MODE == "real" and _real_detect:
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_detect, window),
                    timeout=ML_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                logger.warning("Real ML detect timed out after %.1f s; using stub", ML_TIMEOUT_S)
            except Exception as e:
                logger.warning("Real ML detect raised error: %s; using stub", e)

        if raw_res is None or not self._validate_detect_schema(raw_res):
            if raw_res is not None:
                logger.warning("Real ML detect returned contract-violating payload; using stub")
            raw_res = stub_detect(window)

        return self._normalize_detect(raw_res)

    async def localize(self, window: list[Any], valid_nodes: Optional[set[str]] = None) -> dict[str, Any]:
        """Runs localization with timeout, fallback, node filtering, and top3 sorting."""
        raw_res = None
        if ML_MODE == "real" and _real_localize:
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_localize, window),
                    timeout=ML_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                logger.warning("Real ML localize timed out after %.1f s; using stub", ML_TIMEOUT_S)
            except Exception as e:
                logger.warning("Real ML localize raised error: %s; using stub", e)

        if raw_res is None or not self._validate_localize_schema(raw_res):
            if raw_res is not None:
                logger.warning("Real ML localize returned contract-violating payload; using stub")
            raw_res = stub_localize(window)

        return self._normalize_localize(raw_res, valid_nodes)

    async def estimate_size(self, window: list[Any]) -> float:
        """Runs size estimation with timeout and fallback."""
        raw_res = None
        if ML_MODE == "real" and _real_estimate_size:
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_estimate_size, window),
                    timeout=ML_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                logger.warning("Real ML estimate_size timed out after %.1f s; using stub", ML_TIMEOUT_S)
            except Exception as e:
                logger.warning("Real ML estimate_size raised error: %s; using stub", e)

        if raw_res is None or not isinstance(raw_res, (int, float)) or math.isnan(raw_res) or math.isinf(raw_res):
            if raw_res is not None:
                logger.warning("Real ML estimate_size returned invalid float; using stub")
            raw_res = stub_estimate_size(window)

        return max(0.1, round(float(raw_res), 2))

    def _validate_detect_schema(self, res: Any) -> bool:
        if not isinstance(res, dict):
            return False
        if "anomaly" not in res or not isinstance(res["anomaly"], bool):
            return False
        score = res.get("score")
        if not isinstance(score, (int, float)) or math.isnan(score) or math.isinf(score):
            return False
        return True

    def _validate_localize_schema(self, res: Any) -> bool:
        if not isinstance(res, dict) or "probs" not in res:
            return False
        probs = res.get("probs")
        if not isinstance(probs, dict):
            return False
        for k, v in probs.items():
            if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                return False
        return True

    def _normalize_detect(self, res: dict[str, Any]) -> dict[str, Any]:
        score = float(res.get("score", 0.0))
        if math.isnan(score) or math.isinf(score):
            score = 0.0
        score = max(0.0, min(1.0, score))
        return {
            "anomaly": bool(res.get("anomaly", False)),
            "score": round(score, 4)
        }

    def _normalize_localize(self, res: dict[str, Any], valid_nodes: Optional[set[str]] = None) -> dict[str, Any]:
        raw_probs = res.get("probs", {})
        cleaned_probs: dict[str, float] = {}

        for k, v in raw_probs.items():
            str_k = str(k)
            if valid_nodes is not None and str_k not in valid_nodes:
                continue
            try:
                val = float(v)
                if not math.isnan(val) and not math.isinf(val) and val >= 0:
                    cleaned_probs[str_k] = val
            except (ValueError, TypeError):
                continue

        if not cleaned_probs:
            fallback_node = next(iter(valid_nodes)) if valid_nodes else "105"
            cleaned_probs = {fallback_node: 1.0}

        # Normalize probabilities to sum to 1.0
        total = sum(cleaned_probs.values())
        if total > 0:
            norm_probs = {k: round(v / total, 4) for k, v in cleaned_probs.items()}
        else:
            norm_probs = {k: round(1.0 / len(cleaned_probs), 4) for k in cleaned_probs}

        # Build top3 sorted descending
        sorted_items = sorted(norm_probs.items(), key=lambda x: x[1], reverse=True)[:3]
        top3 = [{"node_id": k, "prob": prob} for k, prob in sorted_items]

        return {
            "top3": top3,
            "probs": norm_probs
        }
