"""
AquaSentinel - Sensor Placement Optimizer
Greedy sensor placement maximizing network leak coverage (or localization capability).
Compares optimized placement against 20 random sensor placements at budget N=8 (and N=12, N=16).
Matches baseline: 8 sensors -> ['208', '10', '15', '184', '601', '164', '60', '123'] (71.7% coverage).
"""
import os
import json
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
DATA_DIR = os.path.join(BASE_DIR, "data")

NOISE_SD = 0.3
THR = 3 * NOISE_SD # 0.9m
TRAIN_AREAS = [0.005, 0.010]

class SensorPlacementOptimizer:
    def __init__(self, random_state=42):
        self.random_state = random_state
        self.wn = wntr.network.WaterNetworkModel(NET_FILE)
        self.wn.options.hydraulic.demand_model = "PDD"
        self.junctions = list(self.wn.junction_name_list)
        self.cover_matrix = None
        self._compute_coverage_matrix()

    def _snapshot(self, leak_node=None, area=None):
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.time.duration = 0
        wn.options.time.hydraulic_timestep = 3600
        wn.options.time.report_timestep = 3600
        if leak_node and area:
            wn.get_node(leak_node).add_leak(wn, area=area, start_time=0)
        res = wntr.sim.WNTRSimulator(wn).run_sim()
        return res.node["pressure"].iloc[0][wn.junction_name_list]

    def _compute_coverage_matrix(self):
        cache_path = os.path.join(DATA_DIR, "placement_cover_matrix.npy")
        if os.path.exists(cache_path):
            self.cover_matrix = np.load(cache_path)
            print(f"Loaded coverage matrix from {cache_path}, shape: {self.cover_matrix.shape}")
            return
            
        print("Computing baseline snapshot and leak drop matrices for placement...")
        base = self._snapshot()
        
        drops = {}
        for a in TRAIN_AREAS:
            rows = []
            for j in self.junctions:
                drop_row = (base - self._snapshot(j, a)).values
                rows.append(drop_row)
            drops[a] = np.array(rows)
            
        cover = np.mean([drops[a] > THR for a in TRAIN_AREAS], axis=0) > 0.5
        self.cover_matrix = cover
        np.save(cache_path, self.cover_matrix)
        print(f"Saved coverage matrix to {cache_path}")

    def greedy_optimize(self, n_sensors):
        chosen, covered = [], np.zeros(len(self.junctions), bool)
        for _ in range(n_sensors):
            gain = [
                (self.cover_matrix[:, k] & ~covered).sum() if k not in chosen else -1
                for k in range(len(self.junctions))
            ]
            k = int(np.argmax(gain))
            chosen.append(k)
            covered |= self.cover_matrix[:, k]
        sensor_ids = [self.junctions[k] for k in chosen]
        coverage = float(covered.mean())
        return chosen, sensor_ids, coverage

    def evaluate_budgets(self, budgets=[8, 12, 16], n_random_trials=20):
        rng = np.random.default_rng(self.random_state)
        n_junctions = len(self.junctions)
        results = []
        
        for n in budgets:
            _, sensor_ids, opt_coverage = self.greedy_optimize(n)
            
            random_coverages = []
            for _ in range(n_random_trials):
                rand_indices = rng.choice(n_junctions, size=n, replace=False)
                covered_rand = self.cover_matrix[:, rand_indices].any(axis=1)
                random_coverages.append(float(covered_rand.mean()))
                
            rand_mean = float(np.mean(random_coverages))
            rand_std = float(np.std(random_coverages))
            
            print(f"Budget N={n:2d} sensors:")
            print(f"  Optimized: {sensor_ids[:8]}... Coverage = {opt_coverage*100:.1f}%")
            print(f"  Random (N=20 trials): Mean = {rand_mean*100:.1f}%, Std = {rand_std*100:.1f}%")
            
            results.append({
                "n_sensors": n,
                "optimized": round(opt_coverage, 3),
                "random_mean": round(rand_mean, 3),
                "random_std": round(rand_std, 3),
                "sensors": sensor_ids
            })
            
        out_path = os.path.join(DATA_DIR, "placement_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Saved placement results to {out_path}")
        return results

if __name__ == "__main__":
    optimizer = SensorPlacementOptimizer()
    res = optimizer.evaluate_budgets([8, 12, 16])
