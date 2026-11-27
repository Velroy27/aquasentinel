"""
AquaSentinel - ML Core API (/ml/api.py)
Contract API implementation:
- detect(window) -> {"anomaly": bool, "score": float}
- localize(window) -> {"probs": {node: prob}}
- estimate_size(window) -> float (lps)

Uses:
- Time-of-day expected pressure baseline (data/expected_baseline.npz)
- Residual CUSUM + Isolation Forest detector (ml/residual_detector.joblib)
- Random Forest localizer (ml/localizer.joblib)
- Leak size estimator (ml/size_estimator.joblib)
"""
import os
import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML_DIR = os.path.join(BASE_DIR, "ml")
DATA_DIR = os.path.join(BASE_DIR, "data")

BASELINE_PATH = os.path.join(DATA_DIR, "expected_baseline.npz")
RESIDUAL_DETECTOR_PATH = os.path.join(ML_DIR, "residual_detector.joblib")
DETECTOR_PATH = os.path.join(ML_DIR, "detector.joblib")
LOCALIZER_PATH = os.path.join(ML_DIR, "localizer.joblib")
SIZE_EST_PATH = os.path.join(ML_DIR, "size_estimator.joblib")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
SENSOR_INDEX = {s: i for i, s in enumerate(SENSORS)}

_detector = None
_residual_detector = None
_localizer_data = None
_size_estimator_data = None
_baseline_data = None

def _load_models():
    global _detector, _residual_detector, _localizer_data, _size_estimator_data, _baseline_data
    if _residual_detector is None and os.path.exists(RESIDUAL_DETECTOR_PATH):
        try:
            _residual_detector = joblib.load(RESIDUAL_DETECTOR_PATH)
        except Exception:
            _residual_detector = None
            
    if _detector is None and os.path.exists(DETECTOR_PATH):
        try:
            _detector = joblib.load(DETECTOR_PATH)
        except Exception:
            _detector = None
            
    if _localizer_data is None and os.path.exists(LOCALIZER_PATH):
        try:
            _localizer_data = joblib.load(LOCALIZER_PATH)
        except Exception:
            _localizer_data = None
            
    if _size_estimator_data is None and os.path.exists(SIZE_EST_PATH):
        try:
            _size_estimator_data = joblib.load(SIZE_EST_PATH)
        except Exception:
            _size_estimator_data = None
            
    if _baseline_data is None and os.path.exists(BASELINE_PATH):
        try:
            _baseline_data = np.load(BASELINE_PATH)
        except Exception:
            _baseline_data = None

def _parse_window(window):
    """
    Parses various window representations into:
    - P: (N, 8) numpy array of sensor pressures
    - times: (N,) array of integer timestamps (seconds)
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
    detect(window) -> {"anomaly": bool, "score": float}
    Evaluates window using residual CUSUM detector against time-of-day baseline.
    """
    _load_models()
    P, times = _parse_window(window)
    if len(P) < 2:
        return {"anomaly": False, "score": 0.0}
        
    # Prefer Residual CUSUM Detector
    if _residual_detector is not None and _baseline_data is not None:
        start_t = int(times[0]) if len(times) > 0 else 0
        t_indices = np.array([int((start_t + i * 10) % 86400 / 10) for i in range(len(P))])
        exp_mean = _baseline_data["expected_mean"][t_indices]
        
        # Self-align baseline from initial steps if long window
        if len(P) >= 6:
            pre_len = max(2, int(len(P) * 0.35))
            offset = np.median(P[:pre_len] - exp_mean[:pre_len], axis=0)
            calibrated_exp = exp_mean + offset
        else:
            calibrated_exp = exp_mean
            
        r_raw = calibrated_exp - P
        r_diff = r_raw - np.median(r_raw, axis=1, keepdims=True)
        
        # CUSUM on differential residual
        S = np.zeros_like(r_diff)
        kz = 0.35
        for t in range(1, len(r_diff)):
            S[t] = np.maximum(0.0, S[t-1] + (r_diff[t] - kz) / 0.3)
            
        max_s = float(np.max(S[-1]))
        max_r = float(np.max(r_diff[-1]))
        std_r = float(np.std(r_diff[-1]))
        
        # Anomaly rules: persistent CUSUM or large localized burst
        is_anom = bool((max_s >= 6.0 and max_r >= 0.70 and std_r >= 0.20) or (max_r >= 1.4 and std_r >= 0.30))
        norm_score = float(np.clip(max_s / 10.0, 0.0, 1.0))
        if is_anom:
            norm_score = max(norm_score, 0.85)
            
        return {"anomaly": is_anom, "score": round(norm_score, 3)}
        
    # Fallback to rolling detector
    if _detector is not None:
        is_anom, score = _detector.score_window(P)
        return {"anomaly": bool(is_anom), "score": float(score)}
        
    return {"anomaly": False, "score": 0.0}

def localize(window):
    """
    localize(window) -> {"probs": {node: prob}}
    """
    _load_models()
    P, _ = _parse_window(window)
    if _localizer_data is None:
        return {"probs": {}}
        
    clf = _localizer_data["model"]
    junctions = _localizer_data["junctions"]
    
    W, n_sensors = P.shape
    pre_idx = max(2, int(W * 0.35))
    post_idx = max(pre_idx + 1, int(W * 0.60))
    pre_P = np.mean(P[:pre_idx], axis=0)
    post_P = np.mean(P[post_idx:], axis=0)
    
    drops = np.maximum(pre_P - post_P, 0.0)
    max_drop = float(np.max(drops))
    norm_drops = drops / (max_drop + 1e-4)
    
    onset_times = np.full(n_sensors, fill_value=float(W))
    thr = 0.45
    for s in range(n_sensors):
        for t in range(pre_idx, W):
            if pre_P[s] - P[t, s] > thr:
                onset_times[s] = float(t)
                break
    min_onset = float(np.min(onset_times))
    rel_onset_times = onset_times - min_onset
    
    feat = np.concatenate([drops, norm_drops, rel_onset_times, [max_drop, np.linalg.norm(drops)]]).reshape(1, -1)
    
    classes = clf.classes_
    raw_probs = clf.predict_proba(feat)[0]
    
    probs = {j: 0.0 for j in junctions}
    for c, p in zip(classes, raw_probs):
        probs[junctions[c]] = round(float(p), 4)
        
    return {"probs": probs}

def estimate_size(window):
    """
    estimate_size(window) -> float lps
    """
    _load_models()
    P, _ = _parse_window(window)
    if _size_estimator_data is None:
        return 0.0
        
    model = _size_estimator_data["model"]
    W, n_sensors = P.shape
    pre_idx = max(2, int(W * 0.35))
    post_idx = max(pre_idx + 1, int(W * 0.60))
    pre_P = np.mean(P[:pre_idx], axis=0)
    post_P = np.mean(P[post_idx:], axis=0)
    
    drops = np.maximum(pre_P - post_P, 0.0)
    max_drop = float(np.max(drops))
    mean_drop = float(np.mean(drops))
    norm_drop = float(np.linalg.norm(drops))
    
    feat = np.concatenate([drops, [max_drop, mean_drop, norm_drop]]).reshape(1, -1)
    est = float(model.predict(feat)[0])
    return max(0.0, round(est, 2))
