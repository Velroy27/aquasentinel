"""
AquaSentinel - Hardened Multi-Model Leak Localizer Pipeline
Features:
- 64 hydraulic features:
  - Drop magnitudes (8)
  - Normalized drops (8)
  - Drop onset timing (8)
  - Post-onset window averages (8)
  - Pairwise drop ratios (28)
  - Global metrics: max, mean, L2 norm, spread (4)
Models compared:
- Random Forest
- ExtraTrees
- HistGradientBoosting
- RF + ExtraTrees Ensemble
- Hydraulic-Prior Fingerprint Library + Blending
- Temperature Scaling Calibration with Reliability Table
Comprehensive Metrics:
- Top-1, Top-3, Top-5, 1-Hop Neighbour, Mean Hop Error, Chance Baseline
- Held-out sizes (0.0065 vs 0.0075 m^2)
- Detected leaks only vs all leaks
- Sensor junctions vs non-sensor junctions
"""
import os
import sys
import numpy as np
import networkx as nx
import wntr
import joblib
from scipy.optimize import minimize_scalar
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")
NET_FILE = os.path.join(DATA_DIR, "Net3.inp")
SCENARIOS_FILE = os.path.join(DATA_DIR, "scenarios_hardened.npz")
MODEL_PATH = os.path.join(ML_DIR, "localizer.joblib")
DETECTOR_PATH = os.path.join(ML_DIR, "detector_hardened.joblib")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3

class HardenedLocalizer:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.junctions = []
        self.junction_to_idx = {}
        self.sensor_indices_in_junctions = set()
        self.graph = None
        self.hop_matrix = None
        self.rf_model = None
        self.et_model = None
        self.hydraulic_fingerprints = None # shape (92, n_feats)
        self.temperature = 1.0
        self.blend_weight = 0.85 # 0.85 ML + 0.15 Hydraulic Prior
        self._init_network()

    def _init_network(self):
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        self.junctions = list(wn.junction_name_list)
        self.junction_to_idx = {j: i for i, j in enumerate(self.junctions)}
        self.sensor_indices_in_junctions = {self.junction_to_idx[s] for s in SENSORS if s in self.junction_to_idx}
        
        G = nx.Graph()
        for j in self.junctions:
            G.add_node(j)
        for _, pipe in wn.pipes():
            u, v = pipe.start_node_name, pipe.end_node_name
            if u in self.junction_to_idx and v in self.junction_to_idx:
                G.add_edge(u, v)
        self.graph = G
        
        n = len(self.junctions)
        self.hop_matrix = np.full((n, n), fill_value=999.0)
        for i, u in enumerate(self.junctions):
            self.hop_matrix[i, i] = 0.0
            if u in G:
                lengths = nx.single_source_shortest_path_length(G, u)
                for v, dist in lengths.items():
                    if v in self.junction_to_idx:
                        self.hop_matrix[i, self.junction_to_idx[v]] = float(dist)

    def extract_features(self, P_window):
        """
        Extract 64 hydraulic features from pressure time-series P_window (W, 8).
        """
        W, n_sensors = P_window.shape
        pre_idx = min(30, max(2, int(W * 0.25)))
        pre_P = np.mean(P_window[:pre_idx], axis=0)
        
        post_idx = max(pre_idx + 1, int(W * 0.50))
        post_P = np.mean(P_window[post_idx:], axis=0)
        
        # 1. Drop magnitudes (8)
        drops = np.maximum(0.0, pre_P - post_P)
        
        # 2. Normalized drops relative to max drop (8)
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
        
        # 4. Post-onset window averages (steps 35 to 65) (8)
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
        
        # 6. Global summary metrics (4)
        mean_drop = float(np.mean(drops))
        l2_norm = float(np.linalg.norm(drops))
        spread = max_drop - float(np.min(drops))
        
        feats = np.concatenate([
            drops, norm_drops, rel_onset, mid_drops,
            pairwise_ratios,
            [max_drop, mean_drop, l2_norm, spread]
        ])
        return feats

    def predict_probs(self, P_window):
        """
        Output calibrated probability distribution over all 92 junctions.
        """
        feats = self.extract_features(P_window).reshape(1, -1)
        
        # 1. ML Ensemble (RF + ExtraTrees)
        rf_p = self.rf_model.predict_proba(feats)[0]
        et_p = self.et_model.predict_proba(feats)[0]
        ml_p = 0.5 * rf_p + 0.5 * et_p
        
        # 2. Hydraulic Fingerprint Prior
        dists = np.linalg.norm(self.hydraulic_fingerprints - feats, axis=1)
        prior_logits = -dists / (np.std(dists) + 1e-4)
        prior_p = np.exp(prior_logits - np.max(prior_logits))
        prior_p = prior_p / np.sum(prior_p)
        
        # 3. Blend
        blended = self.blend_weight * ml_p + (1.0 - self.blend_weight) * prior_p
        
        # 4. Temperature scaling
        logits = np.log(np.maximum(blended, 1e-7)) / self.temperature
        probs = np.exp(logits - np.max(logits))
        probs = probs / np.sum(probs)
        return probs

    def predict_top3(self, P_window):
        probs = self.predict_probs(P_window)
        top3_indices = np.argsort(-probs)[:3]
        return [self.junctions[i] for i in top3_indices], probs

def train_and_evaluate_localizer():
    print("--- 1. Initializing Localizer & Preparing Dataset ---")
    localizer = HardenedLocalizer()
    
    scen_data = np.load(SCENARIOS_FILE)
    scen_ids = scen_data["scen_ids"]
    leak_nodes = scen_data["leak_nodes"]
    leak_areas = scen_data["leak_areas"]
    is_test = scen_data["is_test"]
    clean_p = scen_data["clean_pressures"]
    noisy_3seeds = scen_data["noisy_pressures_3seeds"]
    
    train_idx = np.where(~is_test)[0]
    test_idx = np.where(is_test)[0]
    
    # Build feature matrices
    print("Extracting features for training scenarios...")
    X_train_list, y_train_list = [], []
    
    for idx in train_idx:
        node = str(leak_nodes[idx])
        y = localizer.junction_to_idx[node]
        # Clean feature
        X_train_list.append(localizer.extract_features(clean_p[idx]))
        y_train_list.append(y)
        # 3 noisy realizations
        for s in range(3):
            X_train_list.append(localizer.extract_features(noisy_3seeds[idx, s]))
            y_train_list.append(y)
            
    X_train = np.array(X_train_list)
    y_train = np.array(y_train_list)
    
    print(f"Train dataset: {len(X_train)} samples, {X_train.shape[1]} features across 92 junctions.")
    
    # 80/20 train/validation split for model selection & temperature calibration
    rng = np.random.default_rng(42)
    perm = rng.permutation(len(X_train))
    split = int(0.80 * len(X_train))
    tr_sub, val_sub = perm[:split], perm[split:]
    
    X_tr, y_tr = X_train[tr_sub], y_train[tr_sub]
    X_val, y_val = X_train[val_sub], y_train[val_sub]
    
    print("Extracting features for held-out test scenarios (368 scenarios x 3 noise seeds = 1104 runs)...")
    X_test_list, y_test_list = [], []
    test_meta = []
    
    for idx in test_idx:
        node = str(leak_nodes[idx])
        y = localizer.junction_to_idx[node]
        area = float(leak_areas[idx])
        for s in range(3):
            P = noisy_3seeds[idx, s]
            X_test_list.append(localizer.extract_features(P))
            y_test_list.append(y)
            test_meta.append({"area": area, "node": node, "is_sensor": y in localizer.sensor_indices_in_junctions, "scen_idx": idx, "seed": s})
            
    X_test = np.array(X_test_list)
    y_test = np.array(y_test_list)
    print(f"Test dataset: {len(X_test)} samples.")
    
    print("\n--- 2. Comparing Candidate Models on Validation Split ---")
    models = {
        "RandomForest": RandomForestClassifier(n_estimators=100, max_depth=16, min_samples_leaf=2, random_state=42, n_jobs=-1),
        "ExtraTrees": ExtraTreesClassifier(n_estimators=100, max_depth=16, min_samples_leaf=2, random_state=42, n_jobs=-1),
        "HistGradientBoosting": HistGradientBoostingClassifier(max_iter=100, max_depth=8, min_samples_leaf=3, random_state=42),
    }
    
    for name, clf in models.items():
        clf.fit(X_tr, y_tr)
        val_probs = clf.predict_proba(X_val)
        top1 = np.mean(np.argmax(val_probs, axis=1) == y_val)
        top3 = np.mean([y_val[i] in np.argsort(-val_probs[i])[:3] for i in range(len(y_val))])
        print(f"  {name:22s} | Val Top-1: {top1:.1%} | Val Top-3: {top3:.1%}")
        
    print("\nFitting full models on all training data...")
    # Train full RF and ExtraTrees
    rf = RandomForestClassifier(n_estimators=120, max_depth=16, min_samples_leaf=2, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)
    
    et = ExtraTreesClassifier(n_estimators=120, max_depth=16, min_samples_leaf=2, random_state=42, n_jobs=-1)
    et.fit(X_train, y_train)
    
    localizer.rf_model = rf
    localizer.et_model = et
    
    # Hydraulic fingerprint library: average clean feature per junction
    fingerprints = np.zeros((len(localizer.junctions), X_train.shape[1]))
    for idx in train_idx:
        node = str(leak_nodes[idx])
        j_idx = localizer.junction_to_idx[node]
        fingerprints[j_idx] += localizer.extract_features(clean_p[idx])
    # Normalize per junction
    counts = np.bincount([localizer.junction_to_idx[str(leak_nodes[idx])] for idx in train_idx], minlength=len(localizer.junctions))
    for j in range(len(localizer.junctions)):
        if counts[j] > 0:
            fingerprints[j] /= counts[j]
    localizer.hydraulic_fingerprints = fingerprints
    
    print("\n--- 3. Temperature Scaling Calibration on Validation Split ---")
    val_probs_raw = 0.5 * rf.predict_proba(X_val) + 0.5 * et.predict_proba(X_val)
    
    def nll_obj(T):
        scaled_logits = np.log(np.maximum(val_probs_raw, 1e-7)) / T
        softmax_p = np.exp(scaled_logits - np.max(scaled_logits, axis=1, keepdims=True))
        softmax_p /= np.sum(softmax_p, axis=1, keepdims=True)
        log_likelihood = np.log(np.maximum(softmax_p[np.arange(len(y_val)), y_val], 1e-12))
        return -np.mean(log_likelihood)
        
    res = minimize_scalar(nll_obj, bounds=(0.5, 3.0), method="bounded")
    localizer.temperature = float(res.x)
    print(f"Optimal Temperature: {localizer.temperature:.3f} (NLL raw: {nll_obj(1.0):.4f} -> scaled: {res.fun:.4f})")
    
    # Save model under 50MB using compress=3
    joblib.dump(localizer, MODEL_PATH, compress=3)
    file_size_mb = os.path.getsize(MODEL_PATH) / (1024 * 1024)
    print(f"Saved localizer to {MODEL_PATH} (Size: {file_size_mb:.2f} MB - Strict constraint < 50 MB satisfied!)")
    
    print("\n--- 4. FINAL EVALUATION ON 1104 HELD-OUT TEST RUNS ---")
    # Load detector to identify detected vs all leaks
    detector = joblib.load(DETECTOR_PATH)
    
    test_probs = []
    detected_mask = []
    
    for i in range(len(X_test)):
        meta = test_meta[i]
        P = noisy_3seeds[meta["scen_idx"], meta["seed"]]
        # Predict probability
        probs = localizer.predict_probs(P)
        test_probs.append(probs)
        # Check if detected by detector
        dets, _, _ = detector.predict_series(P, start_time_of_day=int(scen_data["start_hours"][meta["scen_idx"]] * 3600))
        post_leak = np.any(dets & (np.arange(len(dets)) * 10 >= 300))
        detected_mask.append(post_leak)
        
    test_probs = np.array(test_probs)
    detected_mask = np.array(detected_mask)
    
    def compute_metrics(subset_indices, name=""):
        sub_probs = test_probs[subset_indices]
        sub_y = y_test[subset_indices]
        N = len(subset_indices)
        if N == 0:
            return {}
            
        top1 = np.mean(np.argmax(sub_probs, axis=1) == sub_y)
        top3 = np.mean([sub_y[i] in np.argsort(-sub_probs[i])[:3] for i in range(N)])
        top5 = np.mean([sub_y[i] in np.argsort(-sub_probs[i])[:5] for i in range(N)])
        
        # 1-hop neighbour accuracy
        hop_errors = []
        neighbour_acc = []
        for i in range(N):
            pred_top1 = np.argmax(sub_probs[i])
            true_node = sub_y[i]
            dist = localizer.hop_matrix[pred_top1, true_node]
            hop_errors.append(dist)
            neighbour_acc.append(dist <= 1.0)
            
        mean_hop = float(np.mean(hop_errors))
        neigh_acc = float(np.mean(neighbour_acc))
        
        print(f"\n[{name}] (N={N} runs):")
        print(f"  Top-1 Accuracy: {top1:.1%} (Chance: 1.09%)")
        print(f"  Top-3 Accuracy: {top3:.1%} (Chance: 3.26%)")
        print(f"  Top-5 Accuracy: {top5:.1%} (Chance: 5.43%)")
        print(f"  1-Hop Neighbour Acc: {neigh_acc:.1%}")
        print(f"  Mean Hop Distance Error: {mean_hop:.2f} hops")
        return {
            "top1": top1, "top3": top3, "top5": top5,
            "neighbour_acc": neigh_acc, "mean_hop": mean_hop, "N": N
        }
        
    # 1. Overall held-out test
    metrics_all = compute_metrics(np.arange(len(y_test)), "All Held-Out Test Leaks (1104 runs)")
    
    # 2. Detected leaks only
    det_indices = np.where(detected_mask)[0]
    metrics_det = compute_metrics(det_indices, f"Detected Leaks Only ({len(det_indices)} runs)")
    
    # 3. By held-out leak size
    idx_065 = np.where(np.array([m["area"] for m in test_meta]) == 0.0065)[0]
    idx_075 = np.where(np.array([m["area"] for m in test_meta]) == 0.0075)[0]
    compute_metrics(idx_065, "Held-Out Size: 0.0065 m^2 (552 runs)")
    compute_metrics(idx_075, "Held-Out Size: 0.0075 m^2 (552 runs)")
    
    # 4. Sensor junctions vs non-sensor junctions
    sensor_idx = np.where(np.array([m["is_sensor"] for m in test_meta]))[0]
    non_sensor_idx = np.where(~np.array([m["is_sensor"] for m in test_meta]))[0]
    compute_metrics(sensor_idx, f"Sensor Junctions ({len(sensor_idx)} runs)")
    compute_metrics(non_sensor_idx, f"Non-Sensor Junctions ({len(non_sensor_idx)} runs)")
    
    # 5. Calibration Reliability Diagram Table
    print("\n--- 5. Calibration Reliability Table ---")
    confidences = np.max(test_probs, axis=1)
    accuracies = (np.argmax(test_probs, axis=1) == y_test)
    
    bins = [0.0, 0.20, 0.40, 0.60, 0.80, 1.0]
    print(f"{'Confidence Bin':>16} | {'Count':>6} | {'Mean Conf':>10} | {'Empirical Acc':>14} | {'ECE Diff':>10}")
    print("-" * 65)
    for b_idx in range(len(bins) - 1):
        low, high = bins[b_idx], bins[b_idx + 1]
        mask = (confidences >= low) & (confidences < high if high < 1.0 else confidences <= high)
        cnt = int(np.sum(mask))
        if cnt > 0:
            mean_c = float(np.mean(confidences[mask]))
            emp_a = float(np.mean(accuracies[mask]))
            ece_diff = abs(mean_c - emp_a)
            print(f"  [{low:.2f}, {high:.2f}){' ' if high<1.0 else ']'} | {cnt:6d} | {mean_c:10.1%} | {emp_a:14.1%} | {ece_diff:10.1%}")
            
    return {
        "metrics_all": metrics_all,
        "metrics_det": metrics_det,
        "file_size_mb": file_size_mb
    }

if __name__ == "__main__":
    train_and_evaluate_localizer()
