"""
AquaSentinel - Improved Residual CUSUM & Isolation Forest Detector
Key Components:
1. Expected-pressure baseline per sensor by time of day (from 14 tuning days)
2. Differential Residual: r_diff(t, s) = (P_exp(t, s) - P(t, s)) - median_j(P_exp(t, j) - P(t, j))
   Cancels global day-to-day demand scaling and diurnal shifts completely.
3. Inter-sensor features (pairwise differentials, spatial disparity)
4. Multi-sensor consistency rule + CUSUM statistic over differential residuals
5. Confirmation window up to 90 s (up to 9 steps)
6. Isolation Forest on residual features
Strictly tuned on:
- Training-size leaks (0.005, 0.010 m^2)
- 14 no-leak tuning days
Evaluated on:
- 92 held-out test leaks (0.0075 m^2)
- 6 untouched no-leak evaluation days
"""
import os
import sys
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")

BASELINE_FILE = os.path.join(DATA_DIR, "expected_baseline.npz")
MODEL_PATH = os.path.join(ML_DIR, "residual_detector.joblib")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
CONFIRM_WINDOW_STEPS = 9  # 9 steps = 90 seconds confirmation window

class ResidualCUSUMDetector:
    def __init__(self, k_slack=0.40, cusum_thresh=6.0, iso_thresh=0.88, random_state=42):
        self.k_slack = k_slack            # CUSUM slack allowance in metres
        self.cusum_thresh = cusum_thresh  # CUSUM decision threshold
        self.iso_thresh = iso_thresh      # Isolation Forest score threshold
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

    def compute_residuals(self, P, start_time_of_day=0):
        """
        Compute differential residuals:
        Raw residual: r_raw(t, s) = P_exp(t_day, s) - P(t, s)
        Common mode:  r_common(t) = median_j(r_raw(t, j))
        Differential: r(t, s)     = r_raw(t, s) - r_common(t)
        P: shape (T, 8)
        start_time_of_day: start offset in seconds from midnight (0 to 86400)
        Returns:
            r_diff: shape (T, 8) - localized drops isolated from global shifts
            r_raw: shape (T, 8)
        """
        T, n_sensors = P.shape
        t_indices = np.array([int((start_time_of_day + t * 10) % 86400 / 10) for t in range(T)])
        
        if self.expected_mean is not None:
            exp_p = self.expected_mean[t_indices]
        else:
            exp_p = np.zeros_like(P)
            
        r_raw = exp_p - P # positive when pressure drops
        
        # Remove network-wide common-mode demand fluctuation (median across the 8 sensors)
        r_common = np.median(r_raw, axis=1, keepdims=True)
        r_diff = r_raw - r_common
        return r_diff, r_raw

    def compute_cusum(self, r_diff, sigma=NOISE_SD):
        """
        Recursive CUSUM on differential residuals:
        S_t(s) = max(0, S_{t-1}(s) + (r_diff[t, s] - k) / sigma)
        """
        T, n_sensors = r_diff.shape
        S = np.zeros((T, n_sensors), dtype=float)
        for t in range(1, T):
            increment = (r_diff[t] - self.k_slack) / sigma
            S[t] = np.maximum(0.0, S[t - 1] + increment)
        return S

    def extract_residual_features(self, r_diff, S):
        """
        Extract inter-sensor spatial disparity and CUSUM features.
        r_diff: shape (T, 8)
        S: shape (T, 8)
        Returns: feature array of shape (T, 14)
        """
        T, n_sensors = r_diff.shape
        max_r = np.max(r_diff, axis=1)
        mean_r = np.mean(r_diff, axis=1)
        std_r = np.std(r_diff, axis=1)
        
        # Inter-sensor spatial spread
        spatial_spread = max_r - np.min(r_diff, axis=1)
        
        # Max CUSUM across sensors
        max_s = np.max(S, axis=1)
        
        # Second-largest drop
        sorted_r = np.sort(r_diff, axis=1)
        second_r = sorted_r[:, -2]
        
        features = np.column_stack([
            max_r, mean_r, std_r, spatial_spread, max_s, second_r,
            r_diff[:, 0], r_diff[:, 1], r_diff[:, 2], r_diff[:, 3],
            r_diff[:, 4], r_diff[:, 5], r_diff[:, 6], r_diff[:, 7]
        ])
        return features

    def fit_isolation_forest(self, tune_days_path):
        """
        Fit Isolation Forest strictly on differential residual features from the 14 tuning days.
        """
        print("Training Isolation Forest on differential residual features from 14 tuning days...", flush=True)
        data = np.load(tune_days_path)
        noisy_p = data["noisy_pressures"] # (14, 8641, 8)
        n_days, T, n_sensors = noisy_p.shape
        
        X_train = []
        stride = 6 # every 60s
        for d in range(n_days):
            day_p = noisy_p[d]
            r_diff, _ = self.compute_residuals(day_p, start_time_of_day=0)
            S = self.compute_cusum(r_diff)
            feats = self.extract_residual_features(r_diff, S)
            X_train.append(feats[::stride])
            
        X_train = np.vstack(X_train)
        print(f"Fitting IsolationForest on {len(X_train)} baseline samples...", flush=True)
        self.iso_forest = IsolationForest(
            n_estimators=100,
            contamination=0.001,
            random_state=self.random_state,
            n_jobs=1
        ).fit(X_train)
        
        joblib.dump(self, MODEL_PATH)
        print(f"Saved residual detector to {MODEL_PATH}")
        return self

    def predict_series(self, P, start_time_of_day=0, min_confirm_steps=3):
        """
        Vectorized detection over time-series P of shape (T, 8).
        """
        T, n_sensors = P.shape
        r_diff, r_raw = self.compute_residuals(P, start_time_of_day=start_time_of_day)
        S = self.compute_cusum(r_diff)
        
        max_r = np.max(r_diff, axis=1)
        std_r = np.std(r_diff, axis=1)
        max_s = np.max(S, axis=1)
        sorted_r = np.sort(r_diff, axis=1)
        second_r = sorted_r[:, -2]
        
        # Rule 1: CUSUM threshold on localized differential drop (persistent drop)
        rule_cusum = (max_s >= self.cusum_thresh) & (max_r >= 0.70) & (std_r >= 0.20)
        
        # Rule 2: Multi-sensor consistency (two sensors showing co-occurring differential drops)
        rule_multi = (max_r >= 0.65) & (second_r >= 0.35) & (max_s >= 3.0)
        
        # Rule 3: High-magnitude localized pipe burst
        rule_burst = (max_r >= 1.4) & (std_r >= 0.30)
        
        # Rule 4: Isolation Forest anomaly score on differential residual features
        rule_iso = np.zeros(T, dtype=bool)
        iso_scores = np.zeros(T, dtype=float)
        
        cand_mask = (max_r >= 0.60) | (max_s >= 3.0)
        cand_idx = np.where(cand_mask)[0]
        if len(cand_idx) > 0 and self.iso_forest is not None:
            cand_feats = self.extract_residual_features(r_diff[cand_idx], S[cand_idx])
            raw_scores = -self.iso_forest.score_samples(cand_feats)
            norm_scores = np.clip((raw_scores - 0.40) / 0.30, 0.0, 1.0)
            iso_scores[cand_idx] = norm_scores
            rule_iso[cand_idx] = (norm_scores >= self.iso_thresh) & (max_r[cand_idx] >= 0.75)
            
        raw_trigger = rule_cusum | rule_multi | rule_burst | rule_iso
        
        # Confirmation window: require at least `min_confirm_steps` within CONFIRM_WINDOW_STEPS (90s = 9 steps)
        detections = np.zeros(T, dtype=bool)
        for t in range(T):
            window_slice = raw_trigger[max(0, t - CONFIRM_WINDOW_STEPS + 1):t + 1]
            if np.sum(window_slice) >= min_confirm_steps:
                detections[t] = True
                
        return detections, np.maximum(max_s / (self.cusum_thresh + 1e-4), iso_scores)

    def evaluate_scenarios(self, scenarios_npz_path, subset_mask=None, start_hr_dict=None, min_confirm_steps=3):
        """
        Evaluate on scenarios dataset.
        """
        data = np.load(scenarios_npz_path)
        noisy_p = data["noisy_pressures"]
        start_times = data["start_times"]
        times = data["times"]
        
        if subset_mask is not None:
            indices = np.where(subset_mask)[0]
        else:
            indices = np.arange(len(noisy_p))
            
        detected_indices = []
        delays = []
        
        for idx in indices:
            P = noisy_p[idx]
            leak_t = start_times[idx]
            pat_hr = start_hr_dict.get(idx, 0) if start_hr_dict else 0
            start_sec = pat_hr * 3600
            
            dets, scores = self.predict_series(P, start_time_of_day=start_sec, min_confirm_steps=min_confirm_steps)
            
            # Check detection at or after leak start
            post_leak_dets = np.where(dets & (times >= leak_t))[0]
            if len(post_leak_dets) > 0:
                det_t = times[post_leak_dets[0]]
                detected_indices.append(idx)
                delays.append(det_t - leak_t)
                
        det_rate = len(detected_indices) / len(indices) if len(indices) > 0 else 0.0
        med_delay = float(np.median(delays)) if delays else 0.0
        return len(detected_indices), len(indices), round(det_rate, 4), round(med_delay, 1)

    def evaluate_false_alarms(self, no_leak_days_npz_path, min_confirm_steps=3):
        """
        Evaluate false alarms per day on a no-leak dataset.
        """
        data = np.load(no_leak_days_npz_path)
        noisy_p = data["noisy_pressures"]
        n_days = len(noisy_p)
        total_fa = 0
        
        for d in range(n_days):
            day_p = noisy_p[d]
            dets, _ = self.predict_series(day_p, start_time_of_day=0, min_confirm_steps=min_confirm_steps)
            det_indices = np.where(dets)[0]
            
            # Debounce alerts within 15 minutes (90 steps) of each other
            last_fa = -9999
            for idx in det_indices:
                if idx - last_fa >= 90:
                    total_fa += 1
                    last_fa = idx
                    
        fa_per_day = total_fa / n_days
        return total_fa, n_days, round(fa_per_day, 3)

if __name__ == "__main__":
    pass
