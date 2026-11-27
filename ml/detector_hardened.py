"""
AquaSentinel - Hardened Dual-Evidence Detector Pipeline
Features:
1. Sensor Health Check (masks stuck, dropout, drift faults)
2. Differential Residual from Expected Baseline with Common-Mode Rejection
3. Dual Evidence: CUSUM on standardized differential drop + Isolation Forest on spatial residuals
4. Spatial Consistency Check (requires corroborated drop or sustained high-magnitude drop)
5. Combined confidence score in [0, 1]
6. 30-minute event cooldown for clean event accounting
"""
import os
import sys
import numpy as np
import joblib
from sklearn.ensemble import IsolationForest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")
BASELINE_FILE = os.path.join(DATA_DIR, "expected_baseline.npz")
MODEL_PATH = os.path.join(ML_DIR, "detector_hardened.joblib")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
CONFIRM_WINDOW_STEPS = 9  # 90s confirmation window
COOLDOWN_STEPS = 180      # 30 minutes cooldown (180 * 10s = 1800s)

class SensorHealthChecker:
    @staticmethod
    def inspect(P_window, min_steps=10):
        """
        Inspect sensor health over a sliding window P_window of shape (W, 8).
        Checks:
        1. Stuck sensor: std < 0.03 m (sensor noise is normally ~0.3 m)
        2. Dropout: pressure <= 0.0 or > 150.0 m
        3. Drift: sudden massive residual deviation on single sensor while others normal
        Returns:
            healthy_mask: bool array of shape (8,)
            fault_flag: bool (True if any sensor is faulty)
            fault_details: dict mapping sensor_idx to fault type
        """
        W, n_sensors = P_window.shape
        healthy_mask = np.ones(n_sensors, dtype=bool)
        fault_details = {}
        
        for s in range(n_sensors):
            col = P_window[:, s]
            # Dropout check: flat zeros or extreme unphysical readings
            if np.mean(col == 0.0) > 0.40 or np.any(col > 200.0) or np.any(col < -30.0):
                healthy_mask[s] = False
                fault_details[s] = "dropout"
                continue
            
            # Stuck check (needs at least min_steps or 8 consecutive identical readings)
            if W >= min_steps:
                std_val = float(np.std(col))
                if std_val < 0.04:
                    healthy_mask[s] = False
                    fault_details[s] = "stuck"
                    continue
            if W >= 8 and float(np.std(col[-8:])) < 0.01:
                healthy_mask[s] = False
                fault_details[s] = "stuck"
                continue
                    
        return healthy_mask, (len(fault_details) > 0), fault_details

class HardenedDetector:
    def __init__(self, k_slack=0.40, cusum_thresh=5.5, iso_thresh=0.75, comb_thresh=0.60, random_state=42):
        self.k_slack = k_slack
        self.cusum_thresh = cusum_thresh
        self.iso_thresh = iso_thresh
        self.comb_thresh = comb_thresh
        self.random_state = random_state
        self.expected_mean = None
        self.expected_std = None
        self.iso_forest = None
        self._load_baseline()

    def _load_baseline(self):
        if os.path.exists(BASELINE_FILE):
            data = np.load(BASELINE_FILE)
            self.expected_mean = data["expected_mean"]  # (8641, 8)
            self.expected_std = data["expected_std"]    # (8641, 8)

    def compute_residuals(self, P, start_time_of_day=0, healthy_mask=None):
        """
        Compute differential residuals:
        r_raw = P_exp - P
        r_common = median_s(r_raw[:, healthy])
        r_diff = r_raw - r_common
        Unhealthy sensors are zeroed out in r_diff so they don't produce false leak signals.
        """
        T, n_sensors = P.shape
        t_indices = np.array([int((start_time_of_day + t * 10) % 86400 / 10) for t in range(T)])
        
        if self.expected_mean is not None:
            exp_p = self.expected_mean[t_indices]
        else:
            exp_p = np.zeros_like(P)
            
        r_raw = exp_p - P
        
        if healthy_mask is None or np.sum(healthy_mask) == 0:
            healthy_mask = np.ones(n_sensors, dtype=bool)
            
        # Common-mode across healthy sensors
        healthy_raw = r_raw[:, healthy_mask]
        r_common = np.median(healthy_raw, axis=1, keepdims=True)
        r_diff = r_raw - r_common
        
        # Zero out unhealthy sensors
        for s in range(n_sensors):
            if not healthy_mask[s]:
                r_diff[:, s] = 0.0
                
        return r_diff, r_raw

    def compute_cusum(self, r_diff, sigma=NOISE_SD):
        T, n_sensors = r_diff.shape
        S = np.zeros((T, n_sensors), dtype=float)
        for t in range(1, T):
            increment = (r_diff[t] - self.k_slack) / sigma
            S[t] = np.maximum(0.0, S[t - 1] + increment)
        return S

    def extract_features(self, r_diff, S, healthy_mask=None):
        """
        Extract spatial and sequential residual features for Isolation Forest.
        """
        T, n_sensors = r_diff.shape
        if healthy_mask is None or np.sum(healthy_mask) == 0:
            healthy_mask = np.ones(n_sensors, dtype=bool)
            
        r_h = r_diff[:, healthy_mask]
        s_h = S[:, healthy_mask]
        
        max_r = np.max(r_h, axis=1)
        mean_r = np.mean(r_h, axis=1)
        std_r = np.std(r_h, axis=1)
        spatial_spread = max_r - np.min(r_h, axis=1)
        max_s = np.max(s_h, axis=1)
        
        # Second drop among healthy
        sorted_r = np.sort(r_h, axis=1)
        second_r = sorted_r[:, -2] if sorted_r.shape[1] > 1 else np.zeros(T)
        
        feats = np.column_stack([
            max_r, mean_r, std_r, spatial_spread, max_s, second_r,
            r_diff[:, 0], r_diff[:, 1], r_diff[:, 2], r_diff[:, 3],
            r_diff[:, 4], r_diff[:, 5], r_diff[:, 6], r_diff[:, 7]
        ])
        return feats

    def fit_isolation_forest(self, tune_days_path):
        print(f"Training Isolation Forest on healthy baseline features from {tune_days_path}...", flush=True)
        data = np.load(tune_days_path)
        noisy_p = data["noisy_pressures"]  # (n_days, 8641, 8)
        n_days, T, n_sensors = noisy_p.shape
        
        X_train = []
        stride = 6  # 60s
        for d in range(n_days):
            day_p = noisy_p[d]
            r_diff, _ = self.compute_residuals(day_p, start_time_of_day=0)
            S = self.compute_cusum(r_diff)
            feats = self.extract_features(r_diff, S)
            X_train.append(feats[::stride])
            
        X_train = np.vstack(X_train)
        print(f"Fitting IsolationForest on {len(X_train)} samples...", flush=True)
        self.iso_forest = IsolationForest(
            n_estimators=100,
            max_samples=256,
            contamination=0.001,
            random_state=self.random_state,
            n_jobs=1
        ).fit(X_train)
        
        # Compress=3 to keep model under 50MB (IsolationForest with 100 trees is ~1MB)
        joblib.dump(self, MODEL_PATH, compress=3)
        print(f"Saved hardened detector to {MODEL_PATH}")
        return self

    def predict_window(self, P_window, start_time_of_day=0):
        """
        Predict on a single sliding window (for API / real-time streaming).
        Returns:
            anomaly: bool
            score: float in [0, 1]
            sensor_fault: bool
        """
        healthy_mask, fault_flag, fault_details = SensorHealthChecker.inspect(P_window)
        r_diff, _ = self.compute_residuals(P_window, start_time_of_day=start_time_of_day, healthy_mask=healthy_mask)
        S = self.compute_cusum(r_diff)
        
        # Latest timestep features
        r_latest = r_diff[-1, healthy_mask]
        s_latest = S[-1, healthy_mask]
        
        if len(r_latest) == 0:
            return False, 0.0, fault_flag
            
        max_r = np.max(r_latest)
        max_s = np.max(s_latest)
        std_r = np.std(r_latest)
        
        # CUSUM confidence score [0, 1]
        score_cusum = float(1.0 / (1.0 + np.exp(-1.2 * (max_s - self.cusum_thresh))))
        
        # Isolation Forest score [0, 1]
        score_iso = 0.0
        if self.iso_forest is not None:
            feats = self.extract_features(r_diff[-1:], S[-1:], healthy_mask=healthy_mask)
            raw_iso = -self.iso_forest.score_samples(feats)[0]
            score_iso = float(np.clip((raw_iso - 0.40) / 0.30, 0.0, 1.0))
            
        # Spatial consistency check
        # Genuine leaks show:
        # 1. max drop >= 0.70 m with localized spatial gradient (std_r >= 0.20 m)
        # OR 2. two healthy sensors showing drop >= 0.35 m
        sorted_r = np.sort(r_latest)
        second_r = sorted_r[-2] if len(sorted_r) > 1 else 0.0
        
        spatial_consistent = (max_r >= 0.70 and std_r >= 0.20) or (max_r >= 0.60 and second_r >= 0.30) or (max_r >= 1.30)
        
        # Combined score
        if spatial_consistent:
            combined_score = float(0.60 * score_cusum + 0.40 * score_iso)
        else:
            combined_score = float(0.20 * score_cusum + 0.10 * score_iso)
            
        anomaly = (combined_score >= self.comb_thresh) and (max_s >= 3.0) and spatial_consistent
        return anomaly, round(combined_score, 4), fault_flag

    def predict_series(self, P, start_time_of_day=0, min_confirm_steps=3):
        """
        Run detection across full sequence P (T, 8).
        Applies health check, CUSUM, IF, spatial consistency, confirmation window, and cooldown.
        Returns:
            detections: bool array (T,)
            scores: float array (T,)
            sensor_fault: bool
        """
        T, n_sensors = P.shape
        healthy_mask, fault_flag, _ = SensorHealthChecker.inspect(P)
        r_diff, _ = self.compute_residuals(P, start_time_of_day=start_time_of_day, healthy_mask=healthy_mask)
        S = self.compute_cusum(r_diff)
        
        r_h = r_diff[:, healthy_mask]
        s_h = S[:, healthy_mask]
        
        if r_h.shape[1] == 0:
            return np.zeros(T, dtype=bool), np.zeros(T, dtype=float), fault_flag
            
        max_r = np.max(r_h, axis=1)
        std_r = np.std(r_h, axis=1)
        max_s = np.max(s_h, axis=1)
        sorted_r = np.sort(r_h, axis=1)
        second_r = sorted_r[:, -2] if sorted_r.shape[1] > 1 else np.zeros(T)
        
        # CUSUM score
        score_cusum = 1.0 / (1.0 + np.exp(-1.2 * (max_s - self.cusum_thresh)))
        
        # Isolation Forest score
        score_iso = np.zeros(T, dtype=float)
        cand_mask = (max_r >= 0.50) | (max_s >= 2.5)
        cand_idx = np.where(cand_mask)[0]
        if len(cand_idx) > 0 and self.iso_forest is not None:
            cand_feats = self.extract_features(r_diff[cand_idx], S[cand_idx], healthy_mask=healthy_mask)
            raw_scores = -self.iso_forest.score_samples(cand_feats)
            score_iso[cand_idx] = np.clip((raw_scores - 0.40) / 0.30, 0.0, 1.0)
            
        spatial_consistent = ((max_r >= 0.70) & (std_r >= 0.20)) | ((max_r >= 0.60) & (second_r >= 0.30)) | (max_r >= 1.30)
        
        combined_score = np.where(
            spatial_consistent,
            0.60 * score_cusum + 0.40 * score_iso,
            0.20 * score_cusum + 0.10 * score_iso
        )
        
        instant_trigger = (combined_score >= self.comb_thresh) & (max_s >= 3.0) & spatial_consistent
        
        # Confirmation window: require at least min_confirm_steps within CONFIRM_WINDOW_STEPS (90s = 9 steps)
        confirmed = np.zeros(T, dtype=bool)
        for t in range(T):
            win = instant_trigger[max(0, t - CONFIRM_WINDOW_STEPS + 1):t + 1]
            if np.sum(win) >= min_confirm_steps:
                confirmed[t] = True
                
        return confirmed, combined_score, fault_flag
