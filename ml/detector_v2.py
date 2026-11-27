"""
AquaSentinel - Detector V2 Pipeline (/ml/detector_v2.py)

Key Improvements over Detector V1 (Hardened):
1. Trimmed Residual Common-Mode Rejection:
   - Excludes Sensor 601 (near Pump 335) from the common-mode median calculation.
   - Excludes the top-2 highest raw drop sensors from the median to prevent multi-sensor leak depressions
     from artificially elevating the common-mode and cancelling the localized leak signal.
2. Hourly Data-Driven Adaptive CUSUM Slack k(h):
   - Fitted directly on the 30 tuning days from empirical residual variance.
   - Circularly smoothed across adjacent hours:
     sigma_smooth(h) = 0.25*sigma(h-1) + 0.50*sigma(h) + 0.25*sigma(h+1)
   - Direction: Quiet hours (low background variance) have lower k(h) to capture subtle drops (0.8-1.2m);
     pump-switching/peak hours (high variance) have higher k(h) to absorb benign transients.
3. Dual-Tier Adaptive Confirmation:
   - Immediate confirmation (2 steps) for clear localized drops (>= drop_high with spatial gradient >= grad_thresh).
   - Standard 3-step confirmation within 90s window for marginal drops (>= drop_low).
4. Physical Sensor Health Checker:
   - Masks stuck sensors (std < 0.04m) and dropouts so bad sensors do not trigger leak alerts.
"""

import os
import sys
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
BASELINE_FILE = os.path.join(DATA_DIR, "expected_baseline.npz")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
CONFIRM_WINDOW_STEPS = 9 # 90s confirmation window
COOLDOWN_STEPS = 180     # 30-min cooldown


class SensorHealthCheckerV2:
    @staticmethod
    def inspect(P_window, min_steps=10):
        W, n_sensors = P_window.shape
        healthy_mask = np.ones(n_sensors, dtype=bool)
        fault_flag = False
        fault_details = {}

        for s in range(n_sensors):
            col = P_window[:, s]
            # Dropout check
            if np.mean(col == 0.0) > 0.40 or np.any(col > 200.0) or np.any(col < -30.0):
                healthy_mask[s] = False
                fault_flag = True
                fault_details[s] = "dropout"
                continue

            # Stuck sensor check
            if W >= min_steps:
                std_val = float(np.std(col))
                if std_val < 0.04:
                    healthy_mask[s] = False
                    fault_flag = True
                    fault_details[s] = "stuck"

        return healthy_mask, fault_flag, fault_details


class HardenedDetectorV2:
    def __init__(
        self,
        cusum_thresh=7.5,
        comb_thresh=0.60,
        decay=0.75,
        drop_high=1.35,
        drop_low=0.85,
        grad_thresh=0.25,
        random_state=42
    ):
        self.cusum_thresh = cusum_thresh
        self.comb_thresh = comb_thresh
        self.decay = decay
        self.drop_high = drop_high
        self.drop_low = drop_low
        self.grad_thresh = grad_thresh
        self.random_state = random_state

        self.expected_mean = None
        self.expected_std = None
        self.hourly_k = np.full(24, 0.75) # Default fallback
        self.raw_sigma_h = np.zeros(24)
        self.smooth_sigma_h = np.zeros(24)

        self._load_baseline()

    def _load_baseline(self):
        if os.path.exists(BASELINE_FILE):
            data = np.load(BASELINE_FILE)
            self.expected_mean = data["expected_mean"] # (8641, 8)
            self.expected_std = data["expected_std"]

    def fit_hourly_slack(self, tune_days_path, kappa=2.2, k_base=0.15):
        """
        Fit hour-varying slack k(h) directly from tuning day residual variance.
        Smoothed circularly across adjacent hours.
        """
        data = np.load(tune_days_path)
        noisy_p = data["noisy_pressures"] # (n_days, 8641, 8)
        n_days, T, n_sensors = noisy_p.shape

        hourly_residuals = {h: [] for h in range(24)}

        for d in range(n_days):
            P = noisy_p[d]
            r_diff, _ = self.compute_residuals(P, start_time_of_day=0)
            for t in range(0, T - 1):
                hour = int((t * 10 // 3600) % 24)
                # Healthy sensors excluding 601
                hourly_residuals[hour].extend(r_diff[t, :4]) # sensors 208, 10, 15, 184
                hourly_residuals[hour].extend(r_diff[t, 5:]) # sensors 164, 60, 123

        raw_sigmas = np.zeros(24)
        for h in range(24):
            arr = np.array(hourly_residuals[h])
            raw_sigmas[h] = float(np.std(arr)) if len(arr) > 0 else 0.30
        self.raw_sigma_h = raw_sigmas

        # Circular smoothing: 0.25 * left + 0.50 * center + 0.25 * right
        smooth_sigmas = np.zeros(24)
        for h in range(24):
            prev_h = (h - 1) % 24
            next_h = (h + 1) % 24
            smooth_sigmas[h] = 0.25 * raw_sigmas[prev_h] + 0.50 * raw_sigmas[h] + 0.25 * raw_sigmas[next_h]
        self.smooth_sigma_h = smooth_sigmas

        # Data-driven slack: k(h) = k_base + kappa * smooth_sigma(h)
        self.hourly_k = np.clip(k_base + kappa * smooth_sigmas, 0.40, 1.20)
        return self.hourly_k

    def compute_residuals(self, P, start_time_of_day=0, healthy_mask=None):
        """
        Trimmed common-mode residual:
        1. Exclude sensor 601 (near Pump 335) from the median.
        2. Exclude top-2 highest raw drop sensors so wide multi-sensor leak depressions
           do not artificially raise the common-mode and cancel the leak.
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

        r_diff = np.zeros_like(r_raw)

        # Sensor 601 index is 4
        common_candidates = [s for s in range(n_sensors) if healthy_mask[s] and s != 4]
        if len(common_candidates) == 0:
            common_candidates = [s for s in range(n_sensors) if healthy_mask[s]]

        for t in range(T):
            row_raw = r_raw[t]
            # Trim top-2 highest drops among common candidates if enough healthy sensors
            c_sensors = list(common_candidates)
            if len(c_sensors) >= 4:
                # Sort by drop magnitude
                sorted_c = sorted(c_sensors, key=lambda s: row_raw[s])
                # Remove top 2 drops
                trimmed_c = sorted_c[:-2]
                r_common = float(np.median(row_raw[trimmed_c]))
            else:
                r_common = float(np.median(row_raw[c_sensors])) if len(c_sensors) > 0 else 0.0

            r_diff[t] = row_raw - r_common

        # Zero out unhealthy sensors
        for s in range(n_sensors):
            if not healthy_mask[s]:
                r_diff[:, s] = 0.0

        return r_diff, r_raw

    def compute_cusum(self, r_diff, start_time_of_day=0, sigma=NOISE_SD):
        """
        Leaky CUSUM with hour-varying adaptive slack k(h).
        """
        T, n_sensors = r_diff.shape
        S = np.zeros((T, n_sensors), dtype=float)

        for t in range(1, T):
            sec = int(start_time_of_day + t * 10)
            hour = int((sec // 3600) % 24)
            k_val = self.hourly_k[hour]

            increment = (r_diff[t] - k_val) / sigma
            # Leaky accumulation with decay
            S[t] = np.maximum(0.0, self.decay * S[t - 1] + increment)

        return S

    def predict_window(self, P_window, start_time_of_day=0):
        """Predict on sliding window."""
        healthy_mask, fault_flag, _ = SensorHealthCheckerV2.inspect(P_window)
        r_diff, _ = self.compute_residuals(P_window, start_time_of_day=start_time_of_day, healthy_mask=healthy_mask)
        S = self.compute_cusum(r_diff, start_time_of_day=start_time_of_day)

        r_latest = r_diff[-1, healthy_mask]
        s_latest = S[-1, healthy_mask]

        if len(r_latest) == 0:
            return False, 0.0, fault_flag

        max_r = float(np.max(r_latest))
        max_s = float(np.max(s_latest))
        std_r = float(np.std(r_latest))

        sorted_r = np.sort(r_latest)
        second_r = float(sorted_r[-2]) if len(sorted_r) > 1 else 0.0

        score_cusum = float(1.0 / (1.0 + np.exp(-1.2 * (max_s - self.cusum_thresh))))

        # Spatial consistency check
        spatial_consistent = (
            (max_r >= self.drop_high and std_r >= self.grad_thresh) or
            (max_r >= self.drop_low and second_r >= (self.drop_low * 0.45)) or
            (max_r >= 1.60)
        )

        anomaly = (score_cusum >= self.comb_thresh) and (max_s >= 3.0) and spatial_consistent
        return anomaly, round(score_cusum, 4), fault_flag

    def predict_series(self, P, start_time_of_day=0):
        """
        Evaluate full sequence P (T, 8) with dual-tier confirmation.
        """
        T, n_sensors = P.shape
        healthy_mask, fault_flag, _ = SensorHealthCheckerV2.inspect(P)
        r_diff, _ = self.compute_residuals(P, start_time_of_day=start_time_of_day, healthy_mask=healthy_mask)
        S = self.compute_cusum(r_diff, start_time_of_day=start_time_of_day)

        r_h = r_diff[:, healthy_mask]
        s_h = S[:, healthy_mask]

        if r_h.shape[1] == 0:
            return np.zeros(T, dtype=bool), np.zeros(T, dtype=float), fault_flag

        max_r = np.max(r_h, axis=1)
        std_r = np.std(r_h, axis=1)
        max_s = np.max(s_h, axis=1)
        sorted_r = np.sort(r_h, axis=1)
        second_r = sorted_r[:, -2] if sorted_r.shape[1] > 1 else np.zeros(T)

        score_cusum = 1.0 / (1.0 + np.exp(-1.2 * (max_s - self.cusum_thresh)))

        # Tier 1: Strong drop trigger (drop >= drop_high with spatial variance)
        tier1_trigger = (score_cusum >= self.comb_thresh) & (max_s >= 3.0) & (
            ((max_r >= self.drop_high) & (std_r >= self.grad_thresh)) | (max_r >= 1.60)
        )

        # Tier 2: Moderate drop trigger (drop >= drop_low with corroboration)
        tier2_trigger = (score_cusum >= self.comb_thresh) & (max_s >= 3.0) & (
            (max_r >= self.drop_low) & (second_r >= (self.drop_low * 0.45))
        )

        confirmed = np.zeros(T, dtype=bool)
        for t in range(T):
            win_t1 = tier1_trigger[max(0, t - CONFIRM_WINDOW_STEPS + 1):t + 1]
            win_t2 = tier2_trigger[max(0, t - CONFIRM_WINDOW_STEPS + 1):t + 1]
            # Immediate 2-step confirmation for Tier 1; 3-step for Tier 2
            if np.sum(win_t1) >= 2 or np.sum(win_t2) >= 3:
                confirmed[t] = True

        return confirmed, score_cusum, fault_flag
