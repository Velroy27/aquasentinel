"""
AquaSentinel - Leak Detector
Rolling z-score + Isolation Forest tuned to ignore normal diurnal demand peaks.
Evaluates:
- Detection rate on unseen test leak scenarios
- Median detection delay (seconds)
- False alarms per day on multi-day no-leak baseline
"""
import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detector.joblib")
NOISE_SD = 0.3
WINDOW_SIZE = 12       # 12 steps = 120 seconds rolling window
Z_THRESHOLD = -2.8     # Rolling z-score threshold for rapid pressure drop
DROP_THRESHOLD = 0.9   # 3 * NOISE_SD (0.9m) physical drop threshold

class LeakDetector:
    def __init__(self, window_size=WINDOW_SIZE, random_state=42):
        self.window_size = window_size
        self.random_state = random_state
        self.iso_forest = None
        self.feature_names = ["min_z", "mean_z", "max_drop", "spatial_disparity", "min_delta"]
        
    def _extract_features(self, window):
        """
        Extract temporal & spatial features from a (W, n_sensors) window.
        window: array of shape (W, n_sensors)
        """
        if len(window) < 3:
            return np.zeros(5)
            
        curr = window[-1]
        prev_w = window[:-1]
        mean_prev = np.mean(prev_w, axis=0)
        std_prev = np.std(prev_w, axis=0) + 1e-4
        
        # Sensor z-scores
        z_scores = (curr - mean_prev) / std_prev
        min_z = float(np.min(z_scores))
        mean_z = float(np.mean(z_scores))
        
        # Physical drops (positive means pressure dropped)
        drops = mean_prev - curr
        max_drop = float(np.max(drops))
        spatial_disparity = float(np.std(drops))
        
        # 1-step delta
        if len(window) >= 2:
            min_delta = float(np.min(window[-1] - window[-2]))
        else:
            min_delta = 0.0
            
        return np.array([min_z, mean_z, max_drop, spatial_disparity, min_delta])

    def fit(self, no_leak_days_data):
        """
        Train Isolation Forest on windows sampled from no-leak days.
        no_leak_days_data: shape (n_days, n_timesteps, n_sensors)
        """
        X_train = []
        n_days, n_steps, n_sensors = no_leak_days_data.shape
        step_stride = 6  # sample every 60s
        
        for d in range(n_days):
            day_data = no_leak_days_data[d]
            for t in range(self.window_size, n_steps, step_stride):
                w = day_data[t - self.window_size:t]
                feat = self._extract_features(w)
                X_train.append(feat)
                
        X_train = np.array(X_train)
        print(f"Training LeakDetector Isolation Forest on {len(X_train)} normal baseline windows...", flush=True)
        # Use n_jobs=1 to avoid process creation overhead during single-sample scoring
        self.iso_forest = IsolationForest(
            n_estimators=100,
            contamination=0.001,
            random_state=self.random_state,
            n_jobs=1
        ).fit(X_train)
        
        joblib.dump(self, MODEL_PATH)
        print(f"Detector model saved to {MODEL_PATH}", flush=True)
        return self

    def score_window(self, window):
        """
        Returns anomaly score between 0.0 and 1.0 and anomaly boolean.
        """
        feat = self._extract_features(window)
        min_z, mean_z, max_drop, spatial_disparity, min_delta = feat
        
        # Localized burst signature
        has_burst_signature = (min_z < Z_THRESHOLD) and (max_drop > DROP_THRESHOLD) and (spatial_disparity > 0.25)
        
        norm_score = 0.0
        if (max_drop > 0.5 * DROP_THRESHOLD or min_z < -2.2) and self.iso_forest is not None:
            raw_score = -self.iso_forest.score_samples(feat.reshape(1, -1))[0]
            norm_score = float(np.clip((raw_score - 0.40) / 0.30, 0.0, 1.0))
            
        is_anomaly = bool(has_burst_signature or (norm_score > 0.85 and max_drop > DROP_THRESHOLD))
        final_score = max(norm_score, 0.85 if has_burst_signature else 0.0)
        return is_anomaly, round(final_score, 3)

    def _process_timeseries(self, P):
        """
        Fast vectorized detection over a time-series array P of shape (T, 8).
        Returns boolean array of detections of shape (T,).
        """
        T, n_sensors = P.shape
        df = pd.DataFrame(P)
        rolling_mean = df.rolling(window=self.window_size).mean().values
        rolling_std = (df.rolling(window=self.window_size).std().values) + 1e-4
        
        # drops and z-scores
        drops = rolling_mean - P
        z_scores = -drops / rolling_std
        
        min_z = np.min(z_scores, axis=1)
        max_drop = np.max(drops, axis=1)
        spatial_disparity = np.std(drops, axis=1)
        
        # Burst signature: sudden localized drop
        burst_signature = (min_z < Z_THRESHOLD) & (max_drop > DROP_THRESHOLD) & (spatial_disparity > 0.25)
        
        # Check candidates that might need isolation forest
        candidate_mask = (max_drop > DROP_THRESHOLD) & (min_z < -2.2)
        candidate_indices = np.where(candidate_mask)[0]
        
        iso_anomaly = np.zeros(T, dtype=bool)
        if len(candidate_indices) > 0 and self.iso_forest is not None:
            cand_feats = []
            for idx in candidate_indices:
                w = P[max(0, idx - self.window_size):idx + 1]
                cand_feats.append(self._extract_features(w))
            cand_feats = np.array(cand_feats)
            raw_scores = -self.iso_forest.score_samples(cand_feats)
            norm_scores = np.clip((raw_scores - 0.40) / 0.30, 0.0, 1.0)
            iso_anomaly[candidate_indices] = norm_scores > 0.85
            
        anomalies = burst_signature | iso_anomaly
        # Require 2 consecutive steps
        consecutive_anomalies = np.zeros(T, dtype=bool)
        consec = 0
        for t in range(self.window_size, T):
            if anomalies[t]:
                consec += 1
                if consec >= 2:
                    consecutive_anomalies[t] = True
            else:
                consec = 0
                
        return consecutive_anomalies

    def evaluate(self, scenarios_npz_path, no_leak_days_npz_path):
        """
        Fast vectorized evaluation across scenarios and no-leak days.
        """
        scenarios = np.load(scenarios_npz_path)
        is_test = scenarios["is_test"]
        test_pressures = scenarios["noisy_pressures"][is_test]
        start_times = scenarios["start_times"][is_test]
        times = scenarios["times"]
        
        detected_count = 0
        detection_delays = []
        
        for idx in range(len(test_pressures)):
            P = test_pressures[idx]
            leak_start = start_times[idx]
            anom_mask = self._process_timeseries(P)
            
            # Find first anomaly at or after leak_start
            detected_indices = np.where(anom_mask & (times >= leak_start))[0]
            if len(detected_indices) > 0:
                first_t = times[detected_indices[0]]
                detected_count += 1
                detection_delays.append(first_t - leak_start)
                
        test_total = len(test_pressures)
        detection_rate = detected_count / test_total
        median_delay = float(np.median(detection_delays)) if detection_delays else 0.0
        
        # Evaluate on no-leak days
        no_leak = np.load(no_leak_days_npz_path)
        no_leak_noisy = no_leak["noisy_pressures"]
        n_days = len(no_leak_noisy)
        false_alarms = 0
        
        for d in range(n_days):
            day_data = no_leak_noisy[d]
            anom_mask = self._process_timeseries(day_data)
            anom_indices = np.where(anom_mask)[0]
            
            # Debounce alerts within 15 minutes (90 steps) of each other
            last_alert_idx = -9999
            for idx in anom_indices:
                if idx - last_alert_idx >= 90:
                    false_alarms += 1
                    last_alert_idx = idx
                    
        fa_per_day = false_alarms / n_days
        
        results = {
            "test_scenarios": test_total,
            "detected_count": detected_count,
            "detection_rate": round(detection_rate, 3),
            "median_delay_s": round(median_delay, 1),
            "mean_delay_s": round(float(np.mean(detection_delays)), 1) if detection_delays else 0.0,
            "no_leak_days": n_days,
            "total_false_alarms": false_alarms,
            "false_alarm_per_day": round(fa_per_day, 2)
        }
        return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="data/scenarios.npz")
    parser.add_argument("--no-leak", default="data/no_leak_days.npz")
    args = parser.parse_args()
    
    detector = LeakDetector()
    no_leak = np.load(args.no_leak)
    detector.fit(no_leak["noisy_pressures"])
    res = detector.evaluate(args.scenarios, args.no_leak)
    print("Detector Evaluation Results:", flush=True)
    print(res, flush=True)
