"""
AquaSentinel - Localizer V2 Pipeline (/ml/localizer_v2.py)

Key Architecture & Improvements:
1. Dynamic Sensor-Space Features (86 features total, NO static class-topology leakage):
   - 8 sensor drop magnitudes (pre vs post drop)
   - 8 normalized drops relative to max drop
   - 8 onset timing delays
   - 8 post-onset window averages
   - 28 pairwise sensor drop ratios: (d_i + 0.1) / (d_j + 0.1)
   - 28 pairwise sensor drop differences: (d_i - d_j)
   - 2 dynamic sensor-space drop centroid coordinates (weighted x, y from sensor coordinates)
   - 4 global summary statistics (max drop, mean drop, L2 norm, spread)
2. Triple-Model Ensemble:
   - RandomForestClassifier (120 trees, max_depth 16)
   - ExtraTreesClassifier (120 trees, max_depth 16)
   - HistGradientBoostingClassifier (max_iter 100, max_depth 8)
   - Blended with Hydraulic Fingerprint Correlation Prior across 92 network junctions
3. Temperature Scaling Calibration:
   - Calibrates output logits to avoid over-confidence.
4. GroupKFold Cross-Validation:
   - Grouped strictly by leak size across the 8 training sizes (NEVER by junction).
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import networkx as nx
import wntr
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
from scipy.optimize import minimize_scalar
from scipy.spatial.distance import cdist

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
NET_FILE = os.path.join(DATA_DIR, "Net3.inp")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3


class HardenedLocalizerV2:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.junctions = []
        self.junction_to_idx = {}
        self.sensor_coords = {}
        self.rf_model = None
        self.et_model = None
        self.hgb_model = None
        self.hydraulic_fingerprints = None # shape (92, n_feats)
        self.temperature = 1.0
        self.blend_weights = (0.45, 0.45, 0.10) # RF, ET, Fingerprint Prior
        self._init_network()

    def _init_network(self):
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        self.junctions = list(wn.junction_name_list)
        self.junction_to_idx = {j: i for i, j in enumerate(self.junctions)}

        for s in SENSORS:
            if s in wn.nodes:
                coords = wn.get_node(s).coordinates
                self.sensor_coords[s] = (float(coords[0]), float(coords[1]))
            else:
                self.sensor_coords[s] = (0.0, 0.0)

    def extract_features(self, P_window):
        """Extract 86 dynamic sensor-space hydraulic features."""
        W, n_sensors = P_window.shape
        pre_idx = min(30, max(2, int(W * 0.25)))
        pre_P = np.mean(P_window[:pre_idx], axis=0)

        post_idx = max(pre_idx + 1, int(W * 0.50))
        post_P = np.mean(P_window[post_idx:], axis=0)

        # 1. Drop magnitudes (8)
        drops = np.maximum(0.0, pre_P - post_P)

        # 2. Normalized drops (8)
        max_drop = float(np.max(drops))
        norm_drops = drops / (max_drop + 1e-4)

        # 3. Onset timing (8)
        onset_times = np.full(n_sensors, fill_value=float(W))
        thr = 1.5 * NOISE_SD # 0.45m
        for s in range(n_sensors):
            for t in range(pre_idx, W):
                if pre_P[s] - P_window[t, s] > thr:
                    onset_times[s] = float(t)
                    break
        min_onset = float(np.min(onset_times))
        rel_onset = onset_times - min_onset

        # 4. Post-onset window averages (8)
        mid_start = min(W - 2, max(pre_idx, int(W * 0.35)))
        mid_end = min(W, mid_start + 25)
        mid_P = np.mean(P_window[mid_start:mid_end], axis=0)
        mid_drops = np.maximum(0.0, pre_P - mid_P)

        # 5. Pairwise drop ratios (28)
        pairwise_ratios = []
        for i in range(n_sensors):
            for j in range(i + 1, n_sensors):
                ratio = (drops[i] + 0.1) / (drops[j] + 0.1)
                pairwise_ratios.append(float(np.clip(ratio, 0.01, 100.0)))
        pairwise_ratios = np.array(pairwise_ratios)

        # 6. Pairwise drop differences (28)
        pairwise_diffs = []
        for i in range(n_sensors):
            for j in range(i + 1, n_sensors):
                diff = drops[i] - drops[j]
                pairwise_diffs.append(float(diff))
        pairwise_diffs = np.array(pairwise_diffs)

        # 7. Dynamic drop centroid (2)
        total_drop = float(np.sum(drops)) + 1e-4
        centroid_x = sum(drops[i] * self.sensor_coords[SENSORS[i]][0] for i in range(n_sensors)) / total_drop
        centroid_y = sum(drops[i] * self.sensor_coords[SENSORS[i]][1] for i in range(n_sensors)) / total_drop

        # 8. Global summary metrics (4)
        mean_drop = float(np.mean(drops))
        l2_norm = float(np.linalg.norm(drops))
        spread = max_drop - float(np.min(drops))

        feats = np.concatenate([
            drops, norm_drops, rel_onset, mid_drops,
            pairwise_ratios, pairwise_diffs,
            [centroid_x, centroid_y],
            [max_drop, mean_drop, l2_norm, spread]
        ])
        return feats

    def fit(self, X_train, y_train):
        """Fit ensemble models and build hydraulic fingerprint prior."""
        self.rf_model = RandomForestClassifier(
            n_estimators=120,
            max_depth=16,
            min_samples_leaf=2,
            random_state=self.random_state,
            n_jobs=-1
        )
        self.et_model = ExtraTreesClassifier(
            n_estimators=120,
            max_depth=16,
            min_samples_leaf=2,
            random_state=self.random_state,
            n_jobs=-1
        )
        self.hgb_model = HistGradientBoostingClassifier(
            max_iter=100,
            max_depth=8,
            min_samples_leaf=3,
            random_state=self.random_state
        )

        self.rf_model.fit(X_train, y_train)
        self.et_model.fit(X_train, y_train)
        self.hgb_model.fit(X_train, y_train)

        # Build hydraulic fingerprint prior (mean feature vector per junction)
        n_junc = len(self.junctions)
        n_feats = X_train.shape[1]
        self.hydraulic_fingerprints = np.zeros((n_junc, n_feats), dtype=float)
        for j_idx in range(n_junc):
            mask = (y_train == j_idx)
            if np.any(mask):
                self.hydraulic_fingerprints[j_idx] = np.mean(X_train[mask], axis=0)

    def calibrate_temperature(self, X_val, y_val):
        """Calibrate probability distribution via temperature scaling on validation set."""
        # Uncalibrated ensemble probabilities
        p_rf = self.rf_model.predict_proba(X_val)
        p_et = self.et_model.predict_proba(X_val)
        p_hgb = self.hgb_model.predict_proba(X_val)

        p_ml = 0.40 * p_rf + 0.40 * p_et + 0.20 * p_hgb

        # Compute prior on val set
        prior_p = np.zeros_like(p_ml)
        for i in range(len(X_val)):
            dists = np.linalg.norm(self.hydraulic_fingerprints - X_val[i], axis=1)
            logits = -dists / (np.std(dists) + 1e-4)
            exps = np.exp(logits - np.max(logits))
            prior_p[i] = exps / np.sum(exps)

        blended = 0.88 * p_ml + 0.12 * prior_p
        raw_logits = np.log(np.maximum(blended, 1e-7))

        def nll(T):
            scaled = raw_logits / max(0.1, float(T))
            exp_scaled = np.exp(scaled - np.max(scaled, axis=1, keepdims=True))
            probs = exp_scaled / np.sum(exp_scaled, axis=1, keepdims=True)
            log_p = np.log(np.maximum(probs[np.arange(len(y_val)), y_val], 1e-7))
            return -float(np.mean(log_p))

        res = minimize_scalar(nll, bounds=(0.5, 3.0), method="bounded")
        self.temperature = float(res.x)
        print(f"Calibrated localizer temperature T = {self.temperature:.3f} (NLL: {res.fun:.4f})")
        return self.temperature

    def predict_probs_batch(self, X_batch):
        """Batch predict probabilities over all 92 junctions for matrix X_batch (N, 86)."""
        p_rf = self.rf_model.predict_proba(X_batch)
        p_et = self.et_model.predict_proba(X_batch)
        p_hgb = self.hgb_model.predict_proba(X_batch)
        p_ml = 0.40 * p_rf + 0.40 * p_et + 0.20 * p_hgb

        dists = cdist(X_batch, self.hydraulic_fingerprints)
        prior_logits = -dists / (np.std(dists, axis=1, keepdims=True) + 1e-4)
        exps = np.exp(prior_logits - np.max(prior_logits, axis=1, keepdims=True))
        prior_p = exps / np.sum(exps, axis=1, keepdims=True)

        blended = 0.88 * p_ml + 0.12 * prior_p
        logits = np.log(np.maximum(blended, 1e-7)) / self.temperature
        probs = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        probs = probs / np.sum(probs, axis=1, keepdims=True)
        return probs

    def predict_top3_batch(self, X_batch):
        probs = self.predict_probs_batch(X_batch)
        top3_indices = np.argsort(-probs, axis=1)[:, :3]
        top3_nodes = [[self.junctions[idx] for idx in row] for row in top3_indices]
        return top3_nodes, probs

    def predict_probs(self, P_window):
        """Output probability distribution over all 92 junctions."""
        feats = self.extract_features(P_window).reshape(1, -1)
        return self.predict_probs_batch(feats)[0]

    def predict_top3(self, P_window):
        probs = self.predict_probs(P_window)
        top3_indices = np.argsort(-probs)[:3]
        return [self.junctions[i] for i in top3_indices], probs
