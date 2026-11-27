"""
AquaSentinel - ML Core API (/ml/api.py)
Contract API implementation:
- detect(window) -> {"anomaly": bool, "score": float, "sensor_fault": bool}
- localize(window) -> {"probs": {node: prob}}
- estimate_size(window) -> float (lps)

Powered by:
- Expected Diurnal Pressure Baseline (data/expected_baseline.npz)
- Hardened Residual CUSUM + Isolation Forest Detector (ml/detector_hardened.joblib)
- Calibrated RF + ExtraTrees + Hydraulic-Prior Localizer (ml/localizer.joblib)
- Quantile Random Forest Size Estimator (ml/size_estimator.joblib)
"""
import os
import sys
import numpy as np
import joblib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ML_DIR = os.path.join(BASE_DIR, "ml")
DATA_DIR = os.path.join(BASE_DIR, "data")

BASELINE_PATH = os.path.join(DATA_DIR, "expected_baseline.npz")
DETECTOR_PATH = os.path.join(ML_DIR, "detector_hardened.joblib")
LOCALIZER_PATH = os.path.join(ML_DIR, "localizer.joblib")
SIZE_EST_PATH = os.path.join(ML_DIR, "size_estimator.joblib")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
SENSOR_INDEX = {s: i for i, s in enumerate(SENSORS)}

_detector = None
_localizer = None
_size_estimator = None

def _load_models():
    global _detector, _localizer, _size_estimator
    if _detector is None and os.path.exists(DETECTOR_PATH):
        try:
            _detector = joblib.load(DETECTOR_PATH)
        except Exception as e:
            print(f"Warning: could not load detector: {e}")
            _detector = None
            
    if _localizer is None and os.path.exists(LOCALIZER_PATH):
        try:
            _localizer = joblib.load(LOCALIZER_PATH)
        except Exception as e:
            print(f"Warning: could not load localizer: {e}")
            _localizer = None
            
    if _size_estimator is None and os.path.exists(SIZE_EST_PATH):
        try:
            _size_estimator = joblib.load(SIZE_EST_PATH)
        except Exception as e:
            print(f"Warning: could not load size estimator: {e}")
            _size_estimator = None

def _parse_window(window):
    """
    Parses various window representations into:
    - P: (W, 8) numpy array of sensor pressures
    - times: (W,) array of integer timestamps (seconds)
    """
    if isinstance(window, np.ndarray):
        if window.ndim == 2 and window.shape[1] == len(SENSORS):
            times = np.arange(len(window)) * 10
            return window.astype(float), times
        elif window.ndim == 1 and len(window) == len(SENSORS):
            return window.reshape(1, -1).astype(float), np.array([0])
            
    if isinstance(window, list) and len(window) > 0:
        first = window[0]
        # Case: list of lists
        if isinstance(first, (list, tuple, np.ndarray)):
            arr = np.array(window, dtype=float)
            if arr.ndim == 2 and arr.shape[1] == len(SENSORS):
                return arr, np.arange(len(arr)) * 10
                
        # Case: list of WebSocket stream messages [{"type": "readings", "t": ..., "readings": [...]}]
        if isinstance(first, dict) and "readings" in first:
            flat_readings = []
            for msg in window:
                t_msg = msg.get("t", 0)
                for r in msg.get("readings", []):
                    r_copy = dict(r)
                    if "t" not in r_copy:
                        r_copy["t"] = t_msg
                    flat_readings.append(r_copy)
            first = flat_readings[0] if flat_readings else {}
            window = flat_readings
            
        # Case: list of reading objects [{"t": ..., "node_id": ..., "pressure_m": ...}]
        if isinstance(first, dict) and "node_id" in first and "pressure_m" in first:
            by_t = {}
            for r in window:
                t = int(r.get("t", 0))
                n_id = str(r.get("node_id"))
                p_m = float(r.get("pressure_m", 0.0))
                if t not in by_t:
                    by_t[t] = {}
                by_t[t][n_id] = p_m
                
            sorted_t = sorted(by_t.keys())
            rows = []
            last_known = np.zeros(len(SENSORS))
            for t in sorted_t:
                row = np.copy(last_known)
                for s_id, s_idx in SENSOR_INDEX.items():
                    if s_id in by_t[t]:
                        row[s_idx] = by_t[t][s_id]
                last_known = row
                rows.append(row)
            return np.array(rows, dtype=float), np.array(sorted_t)
            
    # Fallback
    return np.zeros((12, len(SENSORS))), np.arange(12) * 10

def detect(window):
    """
    detect(window) -> {"anomaly": bool, "score": float, "sensor_fault": bool}
    Evaluates sliding window using hardened CUSUM + Isolation Forest with health checks.
    """
    _load_models()
    P, times = _parse_window(window)
    if len(P) < 2 or _detector is None:
        return {"anomaly": False, "score": 0.0, "sensor_fault": False}
        
    start_sec = int(times[0]) if len(times) > 0 else 0
    anomaly, score, sensor_fault = _detector.predict_window(P, start_time_of_day=start_sec)
    
    return {
        "anomaly": bool(anomaly),
        "score": float(round(score, 4)),
        "sensor_fault": bool(sensor_fault)
    }

def localize(window):
    """
    localize(window) -> {"probs": {node: prob}}
    Outputs calibrated probability distribution over all 92 network junctions.
    """
    _load_models()
    P, _ = _parse_window(window)
    if _localizer is None:
        return {"probs": {}}
        
    probs = _localizer.predict_probs(P)
    probs_dict = {
        _localizer.junctions[i]: float(round(probs[i], 4))
        for i in range(len(_localizer.junctions))
    }
    return {"probs": probs_dict}

def estimate_size(window):
    """
    estimate_size(window) -> float (lps)
    Returns estimated leak flow in litres per second.
    """
    _load_models()
    P, _ = _parse_window(window)
    if _size_estimator is None:
        return 0.0
        
    res = _size_estimator.predict(P)
    return float(res["est_lps"])
