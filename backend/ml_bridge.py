# AquaSentinel ML Bridge & Stub Fallbacks
from collections import deque
from pathlib import Path
from typing import Any, Optional
import asyncio
import concurrent.futures
import logging
import math
import os
import sys
import time

from backend.config import ML_TIMEOUT_S, REPO_ROOT, WARMUP_TIMEOUT_S

logger = logging.getLogger("aquasentinel.ml_bridge")

# Add system site-packages if needed to locate numpy/joblib/sklearn
base_sp = Path(sys.base_prefix) / "Lib" / "site-packages"
if base_sp.exists() and str(base_sp) not in sys.path:
    sys.path.append(str(base_sp))

# Add repository root to sys.path to allow importing from /ml
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Optional numpy import for type checking
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Real vs Stub flag
_initial_mode = "stub"
ML_MODE = _initial_mode
_real_detect = None
_real_localize = None
_real_estimate_size = None

try:
    from ml.api import detect as _ml_detect, localize as _ml_localize, estimate_size as _ml_est
    _real_detect = _ml_detect
    _real_localize = _ml_localize
    _real_estimate_size = _ml_est
    _initial_mode = "real"
    logger.info("Successfully loaded ML module from ml.api")
except ImportError as e:
    _initial_mode = "stub"
    logger.warning("ImportError importing ml.api: %s; using ML stubs", e)
except Exception as e:
    _initial_mode = "stub"
    logger.warning("Failed importing ml.api: %s; using ML stubs", e)

ML_MODE = _initial_mode


def _to_python_bool(val: Any) -> bool:
    if hasattr(val, "item"):
        val = val.item()
    return bool(val)


def _to_python_float(val: Any, default: float = 0.0) -> float:
    if hasattr(val, "item"):
        val = val.item()
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _is_valid_scalar_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return True
    if HAS_NUMPY and isinstance(val, np.bool_):
        return True
    return False


def _is_valid_scalar_float(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return not (math.isnan(val) or math.isinf(val))
    if HAS_NUMPY and isinstance(val, (np.floating, np.integer)):
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    return False


ML_SENSORS: set[str] = {"208", "10", "15", "184", "601", "164", "60", "123"}


def stub_detect(window: list[Any]) -> dict[str, Any]:
    """Deterministic stub for detect(window) -> {'anomaly': bool, 'score': float, 'sensor_fault': bool}."""
    if os.getenv("AQUASENTINEL_ALLOW_STUB_ALERTS") != "1":
        return {"anomaly": False, "score": 0.0, "sensor_fault": False}

    if not window:
        return {"anomaly": False, "score": 0.05, "sensor_fault": False}

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
                if node_id and _is_valid_scalar_float(pressure):
                    pressure_by_node.setdefault(node_id, []).append(_to_python_float(pressure))

    node_ranges = [max(values) - min(values) for values in pressure_by_node.values() if len(values) > 1]
    if node_ranges:
        largest_change = max(node_ranges)
        if largest_change >= 2.5:
            score = min(0.99, max(0.55, 0.50 + largest_change * 0.1))
            return {"anomaly": True, "score": round(score, 3), "sensor_fault": False}

    # For mock single readings (e.g. unit tests without node_id or on non-network mock nodes):
    # support legacy low-pressure threshold if node is not an ML sensor that normally operates at low head.
    nodes_present = set(pressure_by_node.keys())
    if not (nodes_present & ML_SENSORS):
        all_pressures: list[float] = []
        for values in pressure_by_node.values():
            all_pressures.extend(values)
        if not all_pressures:
            for item in window:
                if isinstance(item, dict):
                    readings = item.get("readings") if isinstance(item.get("readings"), list) else [item]
                    for r in readings:
                        if isinstance(r, dict):
                            p = r.get("pressure_m")
                            if _is_valid_scalar_float(p):
                                all_pressures.append(_to_python_float(p))
        if all_pressures:
            min_p = min(all_pressures)
            if min_p < 48.0:
                score = min(0.99, max(0.55, 0.50 + (48.0 - min_p) * 0.05))
                return {"anomaly": True, "score": round(score, 3), "sensor_fault": False}

    return {"anomaly": False, "score": 0.08, "sensor_fault": False}


def stub_localize(window: list[Any]) -> dict[str, Any]:
    """Deterministic stub for localize(window) -> {'probs': {node: prob}}."""
    node_drops: dict[str, list[float]] = {}
    for item in window:
        if isinstance(item, dict):
            readings = item.get("readings", [item]) if "readings" in item else [item]
            for r in readings:
                if isinstance(r, dict):
                    nid = str(r.get("node_id", ""))
                    p = r.get("pressure_m")
                    if nid and _is_valid_scalar_float(p):
                        node_drops.setdefault(nid, []).append(_to_python_float(p))

    scored: list[tuple[str, float]] = []
    for nid, plist in node_drops.items():
        if plist:
            drop = (sum(plist) / len(plist)) - min(plist)
            scored.append((nid, max(drop, 0.01)))

    if not scored:
        return {"probs": {"105": 0.70, "115": 0.20, "123": 0.10}}

    scored.sort(key=lambda x: x[1], reverse=True)
    total_score = sum(s[1] for s in scored) + 1e-6
    probs = {nid: round(s / total_score, 4) for nid, s in scored}
    return {"probs": probs}


def stub_estimate_size(window: list[Any]) -> float:
    """Deterministic stub for estimate_size(window) -> float lps."""
    if not window:
        return 5.5
    pressures: list[float] = []
    for item in window:
        if isinstance(item, dict):
            readings = item.get("readings", [item]) if "readings" in item else [item]
            for r in readings:
                if isinstance(r, dict):
                    p = r.get("pressure_m")
                    if _is_valid_scalar_float(p):
                        pressures.append(_to_python_float(p))
    if pressures:
        drop = (sum(pressures) / len(pressures)) - min(pressures)
        return round(max(1.5, min(20.0, drop * 1.8)), 1)
    return 5.5


class MLBridge:
    def __init__(self, thread_pool_size: int = 4):
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=thread_pool_size, thread_name_prefix="MLBridge"
        )
        self.ml_mode: str = _initial_mode
        self.forced_stub: bool = (_initial_mode == "stub")
        self.stub_calls: int = 0
        self.stub_calls_run: int = 0
        self.consecutive_real_calls: int = 0
        self.last_stub_reason: Optional[str] = None
        self.latencies: deque[float] = deque(maxlen=20)
        self.last_result_was_stub: bool = False

    def force_stub_mode(self, reason: str = "forced_stub_mode") -> None:
        self.forced_stub = True
        self.ml_mode = "stub"
        self._record_stub(reason, force_stub_mode=True)

    def set_real_mode(self) -> None:
        if _real_detect is not None:
            self.forced_stub = False
            self.ml_mode = "real"

    def get_mode(self) -> str:
        return self.ml_mode

    def reset_run_stubs(self) -> None:
        self.stub_calls_run = 0

    def get_avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return round(sum(self.latencies) / len(self.latencies), 1)

    def _record_stub(self, reason: str, force_stub_mode: bool = False) -> None:
        self.stub_calls += 1
        self.stub_calls_run += 1
        self.last_stub_reason = reason
        self.last_result_was_stub = True
        self.consecutive_real_calls = 0
        if force_stub_mode or _initial_mode == "stub" or self.ml_mode == "stub":
            self.ml_mode = "stub"
        else:
            self.ml_mode = "degraded"
        logger.warning("Using ML stub: %s (total stub calls: %d)", reason, self.stub_calls)

    def _record_success(self) -> None:
        self.consecutive_real_calls += 1
        if self.consecutive_real_calls >= 20 and self.ml_mode == "degraded":
            self.ml_mode = "real"
            logger.info("ML mode recovered from degraded to real after %d consecutive successful calls", self.consecutive_real_calls)

    async def warmup(self, warm_window: list[Any]) -> bool:
        """Runs warm-up calls for detect, localize, and estimate_size."""
        if self.forced_stub or _real_detect is None:
            logger.info("ML is in stub mode, skipping real warm-up")
            return True

        logger.info("Starting ML warm-up with timeout %.1fs...", WARMUP_TIMEOUT_S)
        loop = asyncio.get_running_loop()

        try:
            # 1. Warm detect
            t0 = time.perf_counter()
            await asyncio.wait_for(
                loop.run_in_executor(self._executor, _real_detect, warm_window),
                timeout=WARMUP_TIMEOUT_S
            )
            d_ms = (time.perf_counter() - t0) * 1000.0
            logger.info("Warm-up detect completed in %.1f ms", d_ms)

            # 2. Warm localize
            t0 = time.perf_counter()
            await asyncio.wait_for(
                loop.run_in_executor(self._executor, _real_localize, warm_window),
                timeout=WARMUP_TIMEOUT_S
            )
            l_ms = (time.perf_counter() - t0) * 1000.0
            logger.info("Warm-up localize completed in %.1f ms", l_ms)

            # 3. Warm estimate_size
            t0 = time.perf_counter()
            await asyncio.wait_for(
                loop.run_in_executor(self._executor, _real_estimate_size, warm_window),
                timeout=WARMUP_TIMEOUT_S
            )
            s_ms = (time.perf_counter() - t0) * 1000.0
            logger.info("Warm-up estimate_size completed in %.1f ms", s_ms)

            self.ml_mode = "real"
            logger.info("ML warm-up successfully finished; ready for real inference")
            return True

        except asyncio.TimeoutError:
            logger.error("ML warm-up timed out after %.1f s; continuing in stub mode", WARMUP_TIMEOUT_S)
            self.force_stub_mode(reason="warm-up timed out")
            return False
        except Exception as e:
            logger.error("ML warm-up failed with error: %s; continuing in stub mode", e, exc_info=True)
            self.force_stub_mode(reason=f"warm-up exception: {e}")
            return False

    async def detect(self, window: list[Any]) -> dict[str, Any]:
        """Runs detection with timeout, fallback, numpy conversion, and schema validation."""
        self.last_result_was_stub = False
        raw_res = None

        if not self.forced_stub and _real_detect is not None:
            t0 = time.perf_counter()
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_detect, window),
                    timeout=ML_TIMEOUT_S
                )
                lat_ms = (time.perf_counter() - t0) * 1000.0
                self.latencies.append(lat_ms)
            except asyncio.TimeoutError:
                self._record_stub("detect call timed out")
            except Exception as e:
                self._record_stub(f"detect raised exception: {e}")
        else:
            self._record_stub(self.last_stub_reason or "stub mode active: detect not available")

        if raw_res is None or not self._validate_detect_schema(raw_res):
            if raw_res is not None and not self.last_result_was_stub:
                self._record_stub("detect returned contract-violating payload")
            raw_res = stub_detect(window)
        else:
            self._record_success()

        return self._normalize_detect(raw_res)

    async def localize(self, window: list[Any], valid_nodes: Optional[set[str]] = None) -> dict[str, Any]:
        """Runs localization with timeout, fallback, node filtering, and top3 sorting."""
        self.last_result_was_stub = False
        raw_res = None

        if not self.forced_stub and _real_localize is not None:
            t0 = time.perf_counter()
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_localize, window),
                    timeout=ML_TIMEOUT_S
                )
                lat_ms = (time.perf_counter() - t0) * 1000.0
                self.latencies.append(lat_ms)
            except asyncio.TimeoutError:
                self._record_stub("localize call timed out")
            except Exception as e:
                self._record_stub(f"localize raised exception: {e}")
        else:
            self._record_stub(self.last_stub_reason or "stub mode active: localize not available")

        if raw_res is None or not self._validate_localize_schema(raw_res):
            if raw_res is not None and not self.last_result_was_stub:
                self._record_stub("localize returned contract-violating payload")
            raw_res = stub_localize(window)
        else:
            self._record_success()

        return self._normalize_localize(raw_res, valid_nodes)

    async def estimate_size(self, window: list[Any]) -> float:
        """Runs size estimation with timeout and fallback."""
        self.last_result_was_stub = False
        raw_res = None

        if not self.forced_stub and _real_estimate_size is not None:
            t0 = time.perf_counter()
            try:
                loop = asyncio.get_running_loop()
                raw_res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _real_estimate_size, window),
                    timeout=ML_TIMEOUT_S
                )
                lat_ms = (time.perf_counter() - t0) * 1000.0
                self.latencies.append(lat_ms)
            except asyncio.TimeoutError:
                self._record_stub("estimate_size call timed out")
            except Exception as e:
                self._record_stub(f"estimate_size raised exception: {e}")
        else:
            self._record_stub(self.last_stub_reason or "stub mode active: estimate_size not available")

        if raw_res is None or not _is_valid_scalar_float(raw_res):
            if raw_res is not None and not self.last_result_was_stub:
                self._record_stub("estimate_size returned invalid float")
            raw_res = stub_estimate_size(window)
        else:
            self._record_success()

        return max(0.1, round(_to_python_float(raw_res), 2))

    def _validate_detect_schema(self, res: Any) -> bool:
        if not isinstance(res, dict):
            return False
        if "anomaly" not in res or not _is_valid_scalar_bool(res["anomaly"]):
            return False
        if "score" not in res or not _is_valid_scalar_float(res["score"]):
            return False
        if "sensor_fault" in res and not _is_valid_scalar_bool(res["sensor_fault"]):
            return False
        return True

    def _validate_localize_schema(self, res: Any) -> bool:
        if not isinstance(res, dict) or "probs" not in res:
            return False
        probs = res.get("probs")
        if not isinstance(probs, dict):
            return False
        for k, v in probs.items():
            if not _is_valid_scalar_float(v):
                return False
        return True

    def _normalize_detect(self, res: dict[str, Any]) -> dict[str, Any]:
        score = _to_python_float(res.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        anomaly = _to_python_bool(res.get("anomaly", False))
        sensor_fault = _to_python_bool(res.get("sensor_fault", False))
        normalized = {
            "anomaly": anomaly,
            "score": round(score, 4),
            "sensor_fault": sensor_fault
        }
        if "detail" in res and isinstance(res["detail"], (dict, list, str)):
            normalized["detail"] = res["detail"]
        return normalized

    def _normalize_localize(self, res: dict[str, Any], valid_nodes: Optional[set[str]] = None) -> dict[str, Any]:
        raw_probs = res.get("probs", {})
        cleaned_probs: dict[str, float] = {}

        for k, v in raw_probs.items():
            str_k = str(k)
            if valid_nodes is not None and len(valid_nodes) > 0 and str_k not in valid_nodes:
                continue
            if _is_valid_scalar_float(v):
                val = _to_python_float(v)
                if val >= 0:
                    cleaned_probs[str_k] = val

        if not cleaned_probs:
            fallback_node = next(iter(valid_nodes)) if valid_nodes else "105"
            cleaned_probs = {fallback_node: 1.0}

        total = sum(cleaned_probs.values())
        if total > 0:
            norm_probs = {k: round(v / total, 4) for k, v in cleaned_probs.items()}
        else:
            norm_probs = {k: round(1.0 / len(cleaned_probs), 4) for k in cleaned_probs}

        sorted_items = sorted(norm_probs.items(), key=lambda x: x[1], reverse=True)[:3]
        top3 = [{"node_id": k, "prob": prob} for k, prob in sorted_items]

        return {
            "top3": top3,
            "probs": norm_probs
        }
