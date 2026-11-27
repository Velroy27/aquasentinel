"""
AquaSentinel - Leak Size Estimator
Regresses leak flow rate in lps from sensor drop magnitudes,
enabling calculation of cumulative litres lost: litres_lost = est_leak_lps * (t - t_onset)
"""
import os
import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "size_estimator.joblib")

class LeakSizeEstimator:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.model = RandomForestRegressor(n_estimators=200, random_state=random_state, n_jobs=-1)
        
    def _extract_features(self, P_window):
        """
        Extract drop magnitude features from a window.
        P_window: shape (W, n_sensors)
        """
        W, n_sensors = P_window.shape
        pre_idx = max(2, int(W * 0.35))
        post_idx = max(pre_idx + 1, int(W * 0.60))
        
        pre_P = np.mean(P_window[:pre_idx], axis=0)
        post_P = np.mean(P_window[post_idx:], axis=0)
        
        drops = np.maximum(pre_P - post_P, 0.0)
        max_drop = float(np.max(drops))
        mean_drop = float(np.mean(drops))
        norm_drop = float(np.linalg.norm(drops))
        
        return np.concatenate([drops, [max_drop, mean_drop, norm_drop]])

    def fit_and_evaluate(self, scenarios_npz_path):
        data = np.load(scenarios_npz_path)
        is_test = data["is_test"]
        leak_flows = data["leak_flows"]
        noisy_P = data["noisy_pressures"]
        clean_P = data["clean_pressures"]
        
        X_train, y_train = [], []
        X_test, y_test = [], []
        
        rng = np.random.default_rng(self.random_state)
        
        for idx in range(len(leak_flows)):
            flow = float(leak_flows[idx])
            clean_scen = clean_P[idx]
            
            if is_test[idx]:
                feat = self._extract_features(noisy_P[idx])
                X_test.append(feat)
                y_test.append(flow)
            else:
                feat = self._extract_features(clean_scen)
                X_train.append(feat)
                y_train.append(flow)
                # Augment with noisy realizations
                for _ in range(15):
                    noisy_sample = clean_scen + rng.normal(0, 0.3, clean_scen.shape)
                    X_train.append(self._extract_features(noisy_sample))
                    y_train.append(flow)
                    
        X_train, y_train = np.array(X_train), np.array(y_train)
        X_test, y_test = np.array(X_test), np.array(y_test)
        
        print(f"Training LeakSizeEstimator on {len(X_train)} samples, evaluating on {len(X_test)} unseen test samples...")
        self.model.fit(X_train, y_train)
        
        preds = self.model.predict(X_test)
        mae = float(mean_absolute_error(y_test, preds))
        r2 = float(r2_score(y_test, preds))
        mean_actual = float(np.mean(y_test))
        
        metrics = {
            "mae_lps": round(mae, 2),
            "r2_score": round(r2, 4),
            "mean_actual_lps": round(mean_actual, 2),
            "relative_error_pct": round(100.0 * mae / (mean_actual + 1e-4), 1)
        }
        print(f"LeakSizeEstimator metrics on test set: MAE={mae:.2f} lps, R2={r2:.4f}, RelErr={metrics['relative_error_pct']}%")
        
        joblib.dump({"model": self.model, "metrics": metrics}, MODEL_PATH)
        print(f"Size estimator saved to {MODEL_PATH}")
        return metrics

    def predict(self, window):
        feat = self._extract_features(window).reshape(1, -1)
        est = float(self.model.predict(feat)[0])
        return max(0.0, round(est, 2))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="data/scenarios.npz")
    args = parser.parse_args()
    
    estimator = LeakSizeEstimator()
    res = estimator.fit_and_evaluate(args.scenarios)
    print("Size Estimator Results:", res)
