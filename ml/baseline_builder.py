"""
AquaSentinel - Expected Pressure Baseline Builder
Builds the expected diurnal pressure baseline per sensor by time of day
using only the 14 no-leak tuning days.
Outputs:
- data/expected_baseline.npz
"""
import os
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
TUNE_FILE = os.path.join(DATA_DIR, "no_leak_days_tune.npz")
OUT_FILE = os.path.join(DATA_DIR, "expected_baseline.npz")
NOISE_SD = 0.3

def build_baseline(tune_path=TUNE_FILE, out_path=OUT_FILE):
    print(f"Loading 14 no-leak tuning days from {tune_path}...")
    data = np.load(tune_path)
    clean_p = data["clean_pressures"]   # shape: (14, 8641, 8)
    noisy_p = data["noisy_pressures"]   # shape: (14, 8641, 8)
    times = data["times"]               # shape: (8641,)
    sensor_ids = data["sensor_ids"]     # 8 sensors
    
    n_days, n_steps, n_sensors = clean_p.shape
    assert n_days == 14, f"Expected 14 tuning days, found {n_days}"
    
    # 1. Mean and std by time of day across the 14 tuning days
    expected_mean = np.mean(clean_p, axis=0) # shape (8641, 8)
    # Total variance = day-to-day demand variance + sensor measurement noise variance
    demand_var = np.var(clean_p, axis=0)
    total_std = np.sqrt(demand_var + (NOISE_SD ** 2)) # shape (8641, 8)
    
    # 2. Pairwise inter-sensor expected differences
    # expected_diff[t, i, j] = expected_mean[t, i] - expected_mean[t, j]
    diff_mean = np.zeros((n_steps, n_sensors, n_sensors), dtype=np.float32)
    diff_std = np.zeros((n_steps, n_sensors, n_sensors), dtype=np.float32)
    
    for i in range(n_sensors):
        for j in range(n_sensors):
            pair_diffs = clean_p[:, :, i] - clean_p[:, :, j] # shape: (14, 8641)
            diff_mean[:, i, j] = np.mean(pair_diffs, axis=0)
            diff_std[:, i, j] = np.sqrt(np.var(pair_diffs, axis=0) + 2 * (NOISE_SD ** 2))
            
    np.savez_compressed(
        out_path,
        times=times,
        sensor_ids=sensor_ids,
        expected_mean=expected_mean,
        expected_std=total_std,
        diff_mean=diff_mean,
        diff_std=diff_std,
        n_days=n_days
    )
    print(f"Expected pressure baseline saved to {out_path} ({n_days} days, {n_steps} steps, {n_sensors} sensors).")
    return out_path

if __name__ == "__main__":
    build_baseline()
