"""
AquaSentinel - Additional No-Leak Days Generator
Generates:
1. 10 new days with wider demand variation:
   - Days 1-6: added to tuning/validation
   - Days 7-10 (4 days): strictly untouched final evaluation days
2. 4 stress validation days (+15%, -15%, peak surges)
3. Combines 30 tuning & validation days into data/no_leak_days_all_tune.npz
4. Saves 4 untouched evaluation days into data/no_leak_days_untouched_eval.npz
"""
import os
import sys
import time
import concurrent.futures
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
DATA_DIR = os.path.join(BASE_DIR, "data")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
STEP = 10
DURATION = 86400

def simulate_day(spec):
    day_id, seed, base_scale, hourly_noise_sd = spec
    rng = np.random.default_rng(seed)
    
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = 0
    
    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            m_noisy = m * base_scale * (1.0 + float(rng.normal(0, hourly_noise_sd)))
            perturbed.append(max(0.0, m_noisy))
        pat.multipliers = perturbed
        
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    
    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()
    noisy_p = clean_p + rng.normal(0, NOISE_SD, clean_p.shape)
    
    return day_id, times, clean_p, noisy_p, base_scale

def generate_all():
    print("Generating additional no-leak days in parallel (4 workers)...", flush=True)
    t0 = time.time()
    
    # 1. 10 wide-variation days (seed 200000..200009)
    # Include explicit +15% and -15% stress days
    wide_specs = []
    rng_global = np.random.default_rng(20261127)
    scales = [1.15, 0.85] + list(1.0 + rng_global.normal(0, 0.08, 8))
    for i in range(10):
        wide_specs.append((f"new_wide_day_{i+1:02d}", 200000 + i, float(scales[i]), 0.04))
        
    # 2. 4 stress validation days (seed 300000..300003)
    stress_specs = [
        ("stress_val_day_01_plus15", 300000, 1.15, 0.05),
        ("stress_val_day_02_minus15", 300001, 0.85, 0.05),
        ("stress_val_day_03_plus12", 300002, 1.12, 0.06),
        ("stress_val_day_04_minus12", 300003, 0.88, 0.06),
    ]
    
    all_specs = wide_specs + stress_specs
    results = {}
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_day, sp): sp[0] for sp in all_specs}
        for future in concurrent.futures.as_completed(futures):
            day_id = futures[future]
            res_day_id, times, clean_p, noisy_p, scale = future.result()
            results[res_day_id] = (times, clean_p, noisy_p, scale)
            print(f"  Completed {res_day_id} (scale={scale:.3f}) [{time.time()-t0:.1f}s]", flush=True)
            
    # Load previously existing 14 tune days and 6 promoted eval days
    prev_tune = np.load(os.path.join(DATA_DIR, "no_leak_days_tune.npz"))
    prev_eval = np.load(os.path.join(DATA_DIR, "no_leak_days_eval.npz"))
    
    c_prev_tune = prev_tune["clean_pressures"]   # 14 days
    n_prev_tune = prev_tune["noisy_pressures"]
    c_prev_eval = prev_eval["clean_pressures"]   # 6 days
    n_prev_eval = prev_eval["noisy_pressures"]
    
    # 10 wide days: days 1-6 tune, days 7-10 untouched eval
    c_new_wide_tune = [results[f"new_wide_day_{i+1:02d}"][1] for i in range(6)]
    n_new_wide_tune = [results[f"new_wide_day_{i+1:02d}"][2] for i in range(6)]
    
    c_untouched_eval = [results[f"new_wide_day_{i+1:02d}"][1] for i in range(6, 10)]
    n_untouched_eval = [results[f"new_wide_day_{i+1:02d}"][2] for i in range(6, 10)]
    
    # 4 stress validation days
    c_stress_val = [results[sp[0]][1] for sp in stress_specs]
    n_stress_val = [results[sp[0]][2] for sp in stress_specs]
    
    # Combine all 30 tuning & validation days: 14 + 6 + 6 + 4 = 30
    all_tune_clean = np.concatenate([c_prev_tune, c_prev_eval, c_new_wide_tune, c_stress_val], axis=0)
    all_tune_noisy = np.concatenate([n_prev_tune, n_prev_eval, n_new_wide_tune, n_stress_val], axis=0)
    
    assert len(all_tune_clean) == 30, f"Expected 30 tuning/validation days, got {len(all_tune_clean)}"
    assert len(c_untouched_eval) == 4, f"Expected 4 untouched eval days, got {len(c_untouched_eval)}"
    
    # Save all 30 tuning/validation days
    all_tune_path = os.path.join(DATA_DIR, "no_leak_days_all_tune.npz")
    np.savez_compressed(
        all_tune_path,
        clean_pressures=all_tune_clean,
        noisy_pressures=all_tune_noisy,
        times=times,
        sensor_ids=np.array(SENSORS),
        n_days=30
    )
    print(f"Saved 30 tuning & validation days to {all_tune_path}")
    
    # Save 4 untouched final evaluation days
    untouched_path = os.path.join(DATA_DIR, "no_leak_days_untouched_eval.npz")
    np.savez_compressed(
        untouched_path,
        clean_pressures=np.array(c_untouched_eval),
        noisy_pressures=np.array(n_untouched_eval),
        times=times,
        sensor_ids=np.array(SENSORS),
        n_days=4
    )
    print(f"Saved 4 strictly UNTOUCHED final evaluation days to {untouched_path}")
    print(f"Finished day generation in {time.time()-t0:.1f}s.")
    return all_tune_path, untouched_path

if __name__ == "__main__":
    generate_all()
