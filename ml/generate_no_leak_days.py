"""
AquaSentinel - 20 No-Leak Days Generator
Generates 20 full 24-hour no-leak days with demand variation:
- 14 days for tuning/validation and time-of-day baseline building
- 6 days kept untouched for final false-alarm estimation
All simulations run at 10s step with 0.3 m Gaussian sensor noise.
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
DURATION = 86400 # 24 hours
SEED_BASE = 100000

def simulate_one_day(day_idx):
    """Worker function for simulating a single 24-hour no-leak day."""
    day_seed = SEED_BASE + day_idx
    rng = np.random.default_rng(day_seed)
    
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = 0
    
    # Day-level global demand scale ~ N(1.0, 0.04)
    day_scale = 1.0 + float(rng.normal(0, 0.04))
    
    # Add diurnal pattern shape variations (hourly perturbations ~ N(0, 0.02))
    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            m_noisy = m * day_scale * (1.0 + float(rng.normal(0, 0.02)))
            perturbed.append(max(0.0, m_noisy))
        pat.multipliers = perturbed
        
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    
    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()
    noisy_p = clean_p + rng.normal(0, NOISE_SD, clean_p.shape)
    
    return day_idx, times, clean_p, noisy_p, day_scale

def generate_20_days():
    os.makedirs(DATA_DIR, exist_ok=True)
    n_days = 20
    print(f"Generating {n_days} no-leak days in parallel (4 workers)...", flush=True)
    t0 = time.time()
    
    results = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_one_day, d): d for d in range(n_days)}
        for future in concurrent.futures.as_completed(futures):
            d = futures[future]
            try:
                day_idx, times, clean_p, noisy_p, scale = future.result()
                results[day_idx] = (times, clean_p, noisy_p, scale)
                print(f"  Day {day_idx+1:02d}/20 completed (scale={scale:.3f}) [{time.time()-t0:.1f}s]", flush=True)
            except Exception as e:
                print(f"  Error on day {d}: {e}", flush=True)
                raise e
                
    times = results[0][0]
    all_clean = [results[i][1] for i in range(n_days)]
    all_noisy = [results[i][2] for i in range(n_days)]
    day_ids = [f"no_leak_day_{i+1:02d}" for i in range(n_days)]
    scales = [results[i][3] for i in range(n_days)]
    
    all_clean = np.array(all_clean)
    all_noisy = np.array(all_noisy)
    
    # 1. 14 days for tuning / validation / expected baseline
    tune_clean = all_clean[:14]
    tune_noisy = all_noisy[:14]
    tune_ids = day_ids[:14]
    tune_path = os.path.join(DATA_DIR, "no_leak_days_tune.npz")
    np.savez_compressed(
        tune_path,
        day_ids=np.array(tune_ids),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=tune_clean,
        noisy_pressures=tune_noisy,
        demand_scales=np.array(scales[:14])
    )
    print(f"Saved 14 tuning days to {tune_path}")
    
    # 2. 6 untouched days for final evaluation
    eval_clean = all_clean[14:]
    eval_noisy = all_noisy[14:]
    eval_ids = day_ids[14:]
    eval_path = os.path.join(DATA_DIR, "no_leak_days_eval.npz")
    np.savez_compressed(
        eval_path,
        day_ids=np.array(eval_ids),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=eval_clean,
        noisy_pressures=eval_noisy,
        demand_scales=np.array(scales[14:])
    )
    print(f"Saved 6 untouched eval days to {eval_path}")
    
    # Combined file
    all_path = os.path.join(DATA_DIR, "no_leak_days_20.npz")
    np.savez_compressed(
        all_path,
        day_ids=np.array(day_ids),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=all_clean,
        noisy_pressures=all_noisy,
        demand_scales=np.array(scales)
    )
    print(f"All 20 days saved in {time.time()-t0:.1f}s.")
    return tune_path, eval_path

if __name__ == "__main__":
    generate_20_days()
