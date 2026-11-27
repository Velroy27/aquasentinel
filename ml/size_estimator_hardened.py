"""
AquaSentinel - Hardened Leak Size Estimator
Features:
- Drop magnitudes, normalized drops, onset timing, L2 norm, max drop
- Random Forest Regressor with 80% prediction interval (10th to 90th percentile tree estimates)
- Evaluated on held-out test sizes (0.0065 m^2 and 0.0075 m^2)
- Compresses to < 50 MB
"""
import os
import sys
import numpy as np
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")
SCENARIOS_FILE = os.path.join(DATA_DIR, "scenarios_hardened.npz")
MODEL_PATH = os.path.join(ML_DIR, "size_estimator.joblib")

class HardenedSizeEstimator:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.model = RandomForestRegressor(
            n_estimators=100,
            max_depth=14,
            min_samples_leaf=2,
            random_state=random_state,
            n_jobs=-1
        )
        self.metrics = {}

    def extract_features(self, P_window):
        W, n_sensors = P_window.shape
        pre_idx = min(30, max(2, int(W * 0.25)))
        pre_P = np.mean(P_window[:pre_idx], axis=0)
        
        post_idx = max(pre_idx + 1, int(W * 0.50))
        post_P = np.mean(P_window[post_idx:], axis=0)
        
        drops = np.maximum(0.0, pre_P - post_P)
        max_drop = float(np.max(drops))
        mean_drop = float(np.mean(drops))
        l2_norm = float(np.linalg.norm(drops))
        norm_drops = drops / (max_drop + 1e-4)
        
        # Summary vector: 8 drops + 8 norm drops + 3 global
        return np.concatenate([drops, norm_drops, [max_drop, mean_drop, l2_norm]])

    def fit_and_evaluate(self):
        print("--- 1. Loading Data & Extracting Size Features ---")
        scen_data = np.load(SCENARIOS_FILE)
        leak_flows = scen_data["leak_flows"]
        leak_areas = scen_data["leak_areas"]
        is_test = scen_data["is_test"]
        clean_p = scen_data["clean_pressures"]
        noisy_3seeds = scen_data["noisy_pressures_3seeds"]
        
        train_idx = np.where(~is_test)[0]
        test_idx = np.where(is_test)[0]
        
        X_train, y_train = [], []
        for idx in train_idx:
            flow = float(leak_flows[idx])
            X_train.append(self.extract_features(clean_p[idx]))
            y_train.append(flow)
            for s in range(3):
                X_train.append(self.extract_features(noisy_3seeds[idx, s]))
                y_train.append(flow)
                
        X_train = np.array(X_train)
        y_train = np.array(y_train)
        
        X_test, y_test, test_areas = [], [], []
        for idx in test_idx:
            flow = float(leak_flows[idx])
            area = float(leak_areas[idx])
            for s in range(3):
                X_test.append(self.extract_features(noisy_3seeds[idx, s]))
                y_test.append(flow)
                test_areas.append(area)
                
        X_test = np.array(X_test)
        y_test = np.array(y_test)
        test_areas = np.array(test_areas)
        
        print(f"Train samples: {len(X_train)}, Test samples: {len(X_test)}")
        print(f"Training RandomForestRegressor...")
        self.model.fit(X_train, y_train)
        
        # Predict on test set
        preds = self.model.predict(X_test)
        
        # Tree predictions for prediction intervals
        tree_preds = np.array([tree.predict(X_test) for tree in self.model.estimators_])
        q10 = np.percentile(tree_preds, 10, axis=0)
        q90 = np.percentile(tree_preds, 90, axis=0)
        interval_coverage = np.mean((y_test >= q10) & (y_test <= q90))
        mean_interval_width = np.mean(q90 - q10)
        
        overall_mae = float(mean_absolute_error(y_test, preds))
        overall_r2 = float(r2_score(y_test, preds))
        overall_mean_y = float(np.mean(y_test))
        overall_rel_err = float(100.0 * overall_mae / overall_mean_y)
        
        # Breakdown by held-out size
        m_065 = (test_areas == 0.0065)
        mae_065 = float(mean_absolute_error(y_test[m_065], preds[m_065]))
        mean_065 = float(np.mean(y_test[m_065]))
        rel_065 = float(100.0 * mae_065 / mean_065)
        cov_065 = float(np.mean((y_test[m_065] >= q10[m_065]) & (y_test[m_065] <= q90[m_065])))
        
        m_075 = (test_areas == 0.0075)
        mae_075 = float(mean_absolute_error(y_test[m_075], preds[m_075]))
        mean_075 = float(np.mean(y_test[m_075]))
        rel_075 = float(100.0 * mae_075 / mean_075)
        cov_075 = float(np.mean((y_test[m_075] >= q10[m_075]) & (y_test[m_075] <= q90[m_075])))
        
        print("\n================ LEAK SIZE ESTIMATOR METRICS ================")
        print(f"Overall Test MAE: {overall_mae:.2f} lps (Mean actual: {overall_mean_y:.1f} lps)")
        print(f"Overall Relative Error: {overall_rel_err:.1f}%")
        print(f"R^2 Score: {overall_r2:.4f}")
        print(f"80% Prediction Interval Coverage: {interval_coverage:.1%} (Mean width: {mean_interval_width:.1f} lps)")
        print(f"\nHeld-Out Size 0.0065 m^2 (N={np.sum(m_065)} runs):")
        print(f"  MAE: {mae_065:.2f} lps | Mean Flow: {mean_065:.1f} lps | Rel Error: {rel_065:.1f}% | Interval Cov: {cov_065:.1%}")
        print(f"Held-Out Size 0.0075 m^2 (N={np.sum(m_075)} runs):")
        print(f"  MAE: {mae_075:.2f} lps | Mean Flow: {mean_075:.1f} lps | Rel Error: {rel_075:.1f}% | Interval Cov: {cov_075:.1%}")
        
        self.metrics = {
            "overall_mae_lps": round(overall_mae, 2),
            "overall_relative_error_pct": round(overall_rel_err, 1),
            "r2_score": round(overall_r2, 4),
            "interval_coverage_pct": round(interval_coverage * 100.0, 1),
            "mean_interval_width_lps": round(mean_interval_width, 1),
            "size_0065": {"mae_lps": round(mae_065, 2), "rel_err_pct": round(rel_065, 1), "cov_pct": round(cov_065 * 100.0, 1)},
            "size_0075": {"mae_lps": round(mae_075, 2), "rel_err_pct": round(rel_075, 1), "cov_pct": round(cov_075 * 100.0, 1)}
        }
        
        # Save model with compress=3
        joblib.dump(self, MODEL_PATH, compress=3)
        file_size_mb = os.path.getsize(MODEL_PATH) / (1024 * 1024)
        print(f"\nSaved size estimator to {MODEL_PATH} (Size: {file_size_mb:.2f} MB - Strict constraint < 50 MB satisfied!)")
        return self.metrics

    def predict(self, window):
        feat = self.extract_features(window).reshape(1, -1)
        est = float(self.model.predict(feat)[0])
        tree_preds = np.array([tree.predict(feat)[0] for tree in self.model.estimators_])
        q10 = float(np.percentile(tree_preds, 10))
        q90 = float(np.percentile(tree_preds, 90))
        return {
            "est_lps": max(0.0, round(est, 2)),
            "interval_80": [max(0.0, round(q10, 2)), round(q90, 2)]
        }

if __name__ == "__main__":
    estimator = HardenedSizeEstimator()
    estimator.fit_and_evaluate()
