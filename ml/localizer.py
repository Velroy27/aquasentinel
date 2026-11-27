"""
AquaSentinel - Leak Localizer
Features per sensor:
- Pressure-drop magnitude
- Drop onset timing
- Normalized drops relative to the sensor with the largest drop
Models compared:
- Random Forest
- Gradient Boosting
- k-NN on fingerprints
Selects the best model, outputs probability distribution over all 92 junctions.
Reports:
- Top-1 accuracy
- Top-3 accuracy
- Neighbour-junction accuracy (within 1 hop)
- Mean hop-distance error
- Chance baseline
"""
import os
import joblib
import numpy as np
import networkx as nx
import wntr
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.neighbors import KNeighborsClassifier

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "localizer.joblib")
NOISE_SD = 0.3

class LeakLocalizer:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.best_model = None
        self.best_model_name = None
        self.junctions = []
        self.junction_to_idx = {}
        self.graph = None
        self.hop_matrix = None
        self._init_network()

    def _init_network(self):
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        self.junctions = list(wn.junction_name_list)
        self.junction_to_idx = {j: i for i, j in enumerate(self.junctions)}
        
        # Build network graph for hop distances
        G = nx.Graph()
        for j in self.junctions:
            G.add_node(j)
        for pipe_name, pipe in wn.pipes():
            u = pipe.start_node_name
            v = pipe.end_node_name
            if u in self.junction_to_idx and v in self.junction_to_idx:
                G.add_edge(u, v)
        self.graph = G
        
        # Precompute all-pairs shortest hop distance matrix
        n = len(self.junctions)
        self.hop_matrix = np.full((n, n), fill_value=999.0)
        for i, u in enumerate(self.junctions):
            self.hop_matrix[i, i] = 0.0
            if u in G:
                lengths = nx.single_source_shortest_path_length(G, u)
                for v, dist in lengths.items():
                    if v in self.junction_to_idx:
                        self.hop_matrix[i, self.junction_to_idx[v]] = float(dist)

    def extract_features_from_window(self, P_window, times=None):
        """
        Extract features from a window of sensor readings.
        P_window: shape (W, n_sensors)
        Returns: feature vector of length 24 (or 26 with norms)
        """
        W, n_sensors = P_window.shape
        # Pre-leak baseline: first 30% of window
        pre_idx = max(2, int(W * 0.35))
        pre_P = np.mean(P_window[:pre_idx], axis=0)
        
        # Post-leak: last 40% of window
        post_idx = max(pre_idx + 1, int(W * 0.60))
        post_P = np.mean(P_window[post_idx:], axis=0)
        
        # 1. Drop magnitudes
        drops = pre_P - post_P
        drops = np.maximum(drops, 0.0) # non-negative drops
        
        # 2. Normalized drops relative to max drop sensor
        max_drop = float(np.max(drops))
        norm_drops = drops / (max_drop + 1e-4)
        
        # 3. Drop onset timing: time step when each sensor drops by > 1.5 * NOISE_SD
        onset_times = np.full(n_sensors, fill_value=float(W))
        thr = 1.5 * NOISE_SD
        for s in range(n_sensors):
            for t in range(pre_idx, W):
                if pre_P[s] - P_window[t, s] > thr:
                    onset_times[s] = float(t)
                    break
        min_onset = float(np.min(onset_times))
        rel_onset_times = onset_times - min_onset
        
        feat = np.concatenate([drops, norm_drops, rel_onset_times, [max_drop, np.linalg.norm(drops)]])
        return feat

    def prepare_dataset(self, scenarios_npz_path, augment_copies=20):
        data = np.load(scenarios_npz_path)
        leak_nodes = data["leak_nodes"]
        is_test = data["is_test"]
        clean_P = data["clean_pressures"]
        noisy_P = data["noisy_pressures"]
        times = data["times"]
        
        rng = np.random.default_rng(self.random_state)
        
        X_train, y_train = [], []
        X_test, y_test = [], []
        
        for idx in range(len(leak_nodes)):
            j_name = leak_nodes[idx]
            if j_name not in self.junction_to_idx:
                continue
            j_idx = self.junction_to_idx[j_name]
            clean_scen = clean_P[idx]
            
            if is_test[idx]:
                # Test set: evaluated on noisy realization
                feat = self.extract_features_from_window(noisy_P[idx], times)
                X_test.append(feat)
                y_test.append(j_idx)
            else:
                # Training set: augmented with noise realizations
                feat_base = self.extract_features_from_window(clean_scen, times)
                X_train.append(feat_base)
                y_train.append(j_idx)
                
                for _ in range(augment_copies):
                    noisy_sample = clean_scen + rng.normal(0, NOISE_SD, clean_scen.shape)
                    feat_noisy = self.extract_features_from_window(noisy_sample, times)
                    X_train.append(feat_noisy)
                    y_train.append(j_idx)
                    
        return np.array(X_train), np.array(y_train), np.array(X_test), np.array(y_test)

    def train_and_compare(self, scenarios_npz_path):
        X_tr, y_tr, X_te, y_te = self.prepare_dataset(scenarios_npz_path)
        print(f"Localizer dataset: {len(X_tr)} train samples, {len(X_te)} test samples across {len(self.junctions)} junctions.")
        
        candidates = {
            "RandomForest": RandomForestClassifier(n_estimators=300, max_depth=20, random_state=self.random_state, n_jobs=-1),
            "GradientBoosting": HistGradientBoostingClassifier(max_iter=200, random_state=self.random_state),
            "kNN": KNeighborsClassifier(n_neighbors=5, weights="distance", metric="cosine")
        }
        
        results = {}
        best_score = -1.0
        
        for name, clf in candidates.items():
            clf.fit(X_tr, y_tr)
            
            # Predict probabilities
            classes = clf.classes_
            probs_raw = clf.predict_proba(X_te)
            
            # Full proba array mapped to all 92 junctions
            n_samples = len(X_te)
            proba = np.zeros((n_samples, len(self.junctions)))
            for c_idx, c in enumerate(classes):
                proba[:, c] = probs_raw[:, c_idx]
                
            top_ranked = np.argsort(proba, axis=1)[:, ::-1]
            
            # 1. Top-1 accuracy
            top1 = float(np.mean(top_ranked[:, 0] == y_te))
            
            # 2. Top-3 accuracy
            top3 = float(np.mean([y_te[i] in top_ranked[i, :3] for i in range(n_samples)]))
            
            # 3. Neighbour accuracy (within 1 hop in graph)
            neighbor_acc = float(np.mean([self.hop_matrix[y_te[i], top_ranked[i, 0]] <= 1.0 for i in range(n_samples)]))
            
            # 4. Mean hop distance error
            hop_dists = [self.hop_matrix[y_te[i], top_ranked[i, 0]] for i in range(n_samples)]
            # filter disconnected components if any
            finite_hops = [h for h in hop_dists if h < 500]
            mean_hop_err = float(np.mean(finite_hops)) if finite_hops else 0.0
            
            score = top3 + neighbor_acc # composite metric
            results[name] = {
                "top1": round(top1, 4),
                "top3": round(top3, 4),
                "neighbour_acc": round(neighbor_acc, 4),
                "mean_hop_error": round(mean_hop_err, 2),
                "composite_score": round(score, 4)
            }
            print(f"Model {name}: Top-1={top1*100:.1f}%, Top-3={top3*100:.1f}%, Neighbour={neighbor_acc*100:.1f}%, MeanHopErr={mean_hop_err:.2f}")
            
            if score > best_score:
                best_score = score
                self.best_model = clf
                self.best_model_name = name
                
        # Chance baseline
        n_junc = len(self.junctions)
        chance_top1 = 1.0 / n_junc
        chance_top3 = 3.0 / n_junc
        # Chance neighbor
        avg_neighbors = float(np.mean([np.sum(self.hop_matrix[i] <= 1.0) for i in range(n_junc)]))
        chance_neighbor = avg_neighbors / n_junc
        
        comparison = {
            "models": results,
            "best_model": self.best_model_name,
            "best_metrics": results[self.best_model_name],
            "chance_baseline": {
                "top1": round(chance_top1, 4),
                "top3": round(chance_top3, 4),
                "neighbour_acc": round(chance_neighbor, 4)
            }
        }
        
        # Save best model
        joblib.dump({
            "model": self.best_model,
            "model_name": self.best_model_name,
            "junctions": self.junctions,
            "metrics": comparison
        }, MODEL_PATH)
        print(f"Best model ({self.best_model_name}) saved to {MODEL_PATH}")
        return comparison

    def predict_probs(self, window):
        feat = self.extract_features_from_window(window).reshape(1, -1)
        classes = self.best_model.classes_
        raw_probs = self.best_model.predict_proba(feat)[0]
        
        probs = {j: 0.0 for j in self.junctions}
        for c, p in zip(classes, raw_probs):
            probs[self.junctions[c]] = float(p)
        return probs

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="data/scenarios.npz")
    args = parser.parse_args()
    
    localizer = LeakLocalizer()
    res = localizer.train_and_compare(args.scenarios)
    print("Localizer Comparison Results:")
    print(res)
