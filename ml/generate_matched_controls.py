"""
AquaSentinel - Matched Controls Generator
Simulates matched no-leak controls for every held-out test scenario:
- Exact same start time of day
- Exact same demand perturbation seed
- NO leak introduced
- 3 sensor noise seeds
Saved to: data/matched_controls.npz
"""
import os
import sys
import time
import concurrent.futures
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
NET_FILE = os.path.join(DATA_DIR, "Net3.inp")
OUT_FILE = os.path.join(DATA_DIR, "matched_controls.npz")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
SCENARIO_DURATION = 1200
STEP = 10
NOISE_SD = 0.3

START_HOURS = [2, 6, 9, 13, 16, 20]
TRAIN_SIZES = [0.002, 0.003, 0.004, 0.005, 0.006, 0.008, 0.010, 0.012]
TEST_SIZES = [0.0065, 0.0075]

def simulate_single_control(task_info):
    scen_id, j_node, area, start_hr, is_test, seed = task_info
    rng = np.random.default_rng(seed)
    
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = SCENARIO_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = start_hr * 3600
    
    # Exact same demand perturbation as the leak scenario
    scen_scale = 1.0 + float(rng.normal(0, 0.04))
    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            p_m = m * scen_scale * (1.0 + float(rng.normal(0, 0.02)))
            perturbed.append(max(0.0, p_m))
        pat.multipliers = perturbed
        
    # NO LEAK ADDED! (Matched control)
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    
    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()
    
    # Same 3 distinct noise seeds for sensor readings
    noisy_p_3seeds = []
    for s_idx in range(3):
        noise_rng = np.random.default_rng(seed + 10000 * (s_idx + 1))
        noisy_p = clean_p + noise_rng.normal(0, NOISE_SD, clean_p.shape)
        noisy_p_3seeds.append(noisy_p)
    noisy_p_3seeds = np.array(noisy_p_3seeds)
    
    return scen_id, start_hr, times, clean_p, noisy_p_3seeds

def build_and_run_controls():
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    target_junctions = wn.junction_name_list
    
    task_idx = 0
    # Training tasks offset
    for j_i, j_node in enumerate(target_junctions):
        for a_i, area in enumerate(TRAIN_SIZES):
            task_idx += 1
            
    # Held-out test control tasks
    test_control_tasks = []
    for j_i, j_node in enumerate(target_junctions):
        for a_i, area in enumerate(TEST_SIZES):
            if area == 0.0065:
                test_hours = [START_HOURS[j_i % 3], START_HOURS[(j_i + 3) % 6]]
            else:
                test_hours = [START_HOURS[(j_i + 1) % 6], START_HOURS[(j_i + 4) % 6]]
            for start_hr in test_hours:
                scen_id = f"test_node_{j_node}_a{int(area*10000):04d}_h{start_hr:02d}"
                seed = 500000 + task_idx
                test_control_tasks.append((scen_id, j_node, area, start_hr, True, seed))
                task_idx += 1
                
    print(f"Simulating {len(test_control_tasks)} matched control scenarios across 4 workers...", flush=True)
    t0 = time.time()
    
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_single_control, t): t[0] for t in test_control_tasks}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            completed += 1
            if completed % 50 == 0 or completed == len(test_control_tasks):
                print(f"  Completed {completed}/{len(test_control_tasks)} controls [{time.time()-t0:.1f}s]", flush=True)
                
    results.sort(key=lambda x: x[0])
    
    scen_ids = [r[0] for r in results]
    start_hours = [r[1] for r in results]
    times = results[0][2]
    clean_pressures = np.array([r[3] for r in results])
    noisy_3seeds = np.array([r[4] for r in results])
    
    np.savez_compressed(
        OUT_FILE,
        scen_ids=np.array(scen_ids),
        start_hours=np.array(start_hours),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=clean_pressures,
        noisy_pressures_3seeds=noisy_3seeds
    )
    print(f"Saved {len(scen_ids)} matched controls to {OUT_FILE} in {time.time()-t0:.1f}s.")
    return OUT_FILE

if __name__ == "__main__":
    build_and_run_controls()
