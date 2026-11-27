"""
AquaSentinel - Hardened Scenario & Disturbance Generator (Phase 1)
Generates:
1. Leak scenarios across 92 junctions:
   - 8 Training leak sizes: [0.002, 0.003, 0.004, 0.005, 0.006, 0.008, 0.010, 0.012 m2]
   - 2 Held-out test sizes: [0.0065, 0.0075 m2] (STRICTLY HELD OUT)
   - 6 Start times of day: [2:00, 6:00, 9:00, 13:00, 16:00, 20:00]
   - Per-scenario unique demand perturbations
   - 3 Noise seeds (0.3 m Gaussian) per scenario
   - 20 min windows (1200 s) at 10 s steps (121 steps), leak starts at t=300s
2. Non-leak disturbances (negative test examples):
   - Pump / valve step changes
   - Tank level shifts (+/- 1.5 m)
   - Sudden demand surge (fire flow 150-250 lps for 15-20 min)
   - Single sensor failures (stuck-at, drift, dropout)
"""
import os
import sys
import time
import json
import argparse
import concurrent.futures
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
DATA_DIR = os.path.join(BASE_DIR, "data")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
STEP = 10
SCENARIO_DURATION = 1200 # 20 mins
LEAK_START_T = 300       # t=300s (5 min baseline)

TRAIN_SIZES = [0.002, 0.003, 0.004, 0.005, 0.006, 0.008, 0.010, 0.012]
TEST_SIZES = [0.0065, 0.0075] # Held-out sizes
START_HOURS = [2, 6, 9, 13, 16, 20] # 6 times of day

def simulate_single_scenario(task_info):
    """
    Simulates one scenario in WNTR.
    Returns:
    - clean_p: (121, 8)
    - noisy_p_3seeds: (3, 121, 8)
    - leak_flow_lps: float
    """
    scen_id, j_node, area, start_hr, is_test, seed = task_info
    rng = np.random.default_rng(seed)
    
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = SCENARIO_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = start_hr * 3600
    
    # Per-scenario unique demand perturbation so no two scenarios share same pattern
    scen_scale = 1.0 + float(rng.normal(0, 0.04))
    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            p_m = m * scen_scale * (1.0 + float(rng.normal(0, 0.02)))
            perturbed.append(max(0.0, p_m))
        pat.multipliers = perturbed
        
    wn.get_node(j_node).add_leak(wn, area=area, start_time=LEAK_START_T)
    
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    
    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()
    
    # 3 distinct noise seeds for sensor readings
    noisy_p_3seeds = []
    for s_idx in range(3):
        noise_rng = np.random.default_rng(seed + 10000 * (s_idx + 1))
        noisy_p = clean_p + noise_rng.normal(0, NOISE_SD, clean_p.shape)
        noisy_p_3seeds.append(noisy_p)
    noisy_p_3seeds = np.array(noisy_p_3seeds) # (3, 121, 8)
    
    # Leak flow calculation
    leak_lps = 0.0
    if "leak_demand" in res.node:
        leak_lps = float(res.node["leak_demand"][j_node].iloc[-1] * 1000.0)
    if leak_lps <= 0.0:
        p_leak = max(0.0, float(res.node["pressure"][j_node].iloc[-1]))
        leak_lps = float(0.75 * area * np.sqrt(2 * 9.81 * p_leak) * 1000.0)
        
    return scen_id, j_node, area, start_hr, is_test, times, clean_p, noisy_p_3seeds, leak_lps

def run_scenarios(subset_n=None):
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    junctions = wn.junction_name_list
    if subset_n is not None:
        target_junctions = junctions[:subset_n]
        suffix = f"_subset{subset_n}"
    else:
        target_junctions = junctions
        suffix = ""
        
    print(f"Building scenario tasks for {len(target_junctions)} junctions...", flush=True)
    tasks = []
    task_idx = 0
    
    # For each junction:
    # 1. Training sizes (8 sizes): cycle the 6 start hours
    for j_i, j_node in enumerate(target_junctions):
        for a_i, area in enumerate(TRAIN_SIZES):
            start_hr = START_HOURS[(j_i + a_i) % len(START_HOURS)]
            scen_id = f"train_node_{j_node}_a{int(area*10000):04d}_h{start_hr:02d}"
            seed = 400000 + task_idx
            tasks.append((scen_id, j_node, area, start_hr, False, seed))
            task_idx += 1
            
    # 2. Held-out test sizes (2 sizes: 0.0065, 0.0075):
    # To thoroughly evaluate across diurnal cycles, sample 3 start hours per test size
    for j_i, j_node in enumerate(target_junctions):
        for a_i, area in enumerate(TEST_SIZES):
            # 2 distinct times of day per test size -> all 6 start times covered across both test sizes
            if area == 0.0065:
                test_hours = [START_HOURS[j_i % 3], START_HOURS[(j_i + 3) % 6]]
            else:
                test_hours = [START_HOURS[(j_i + 1) % 6], START_HOURS[(j_i + 4) % 6]]
            for start_hr in test_hours:
                scen_id = f"test_node_{j_node}_a{int(area*10000):04d}_h{start_hr:02d}"
                seed = 500000 + task_idx
                tasks.append((scen_id, j_node, area, start_hr, True, seed))
                task_idx += 1
                
    print(f"Total scenarios to simulate: {len(tasks)} (Train: {len(target_junctions)*len(TRAIN_SIZES)}, Test: {len(tasks)-len(target_junctions)*len(TRAIN_SIZES)})", flush=True)
    t0 = time.time()
    
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_single_scenario, t): t[0] for t in tasks}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            completed += 1
            if completed % 100 == 0 or completed == len(tasks):
                print(f"  Completed {completed}/{len(tasks)} scenarios [{time.time()-t0:.1f}s]", flush=True)
                
    results.sort(key=lambda x: x[0])
    
    scen_ids = [r[0] for r in results]
    leak_nodes = [r[1] for r in results]
    leak_areas = [r[2] for r in results]
    start_hours = [r[3] for r in results]
    is_test = [r[4] for r in results]
    times = results[0][5]
    clean_pressures = np.array([r[6] for r in results])
    noisy_3seeds = np.array([r[7] for r in results]) # (N, 3, 121, 8)
    leak_flows = np.array([r[8] for r in results])
    
    manifest = []
    for i in range(len(results)):
        manifest.append({
            "id": scen_ids[i],
            "leak_node": leak_nodes[i],
            "leak_area_m2": leak_areas[i],
            "start_hr": start_hours[i],
            "is_test": is_test[i],
            "leak_flow_lps": round(float(leak_flows[i]), 2),
            "start_t": LEAK_START_T
        })
        
    out_npz = os.path.join(DATA_DIR, f"scenarios_hardened{suffix}.npz")
    np.savez_compressed(
        out_npz,
        scen_ids=np.array(scen_ids),
        leak_nodes=np.array(leak_nodes),
        leak_areas=np.array(leak_areas),
        start_hours=np.array(start_hours),
        is_test=np.array(is_test),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=clean_pressures,
        noisy_pressures_3seeds=noisy_3seeds,
        leak_flows=leak_flows
    )
    
    out_manifest = os.path.join(DATA_DIR, f"manifest_hardened{suffix}.json")
    with open(out_manifest, "w") as f:
        json.dump(manifest, f, indent=2)
        
    elapsed = time.time() - t0
    print(f"Saved {len(results)} hardened scenarios to {out_npz} and {out_manifest} in {elapsed:.1f}s.")
    return out_npz, out_manifest, elapsed

def generate_disturbances():
    """
    Generates non-leak disturbances as negative test examples:
    1. Pump / valve step changes (10 scenarios)
    2. Tank level shifts (+/- 1.5 m) (10 scenarios)
    3. Sudden customer demand surge (fire flow 150-250 lps) (10 scenarios)
    4. Sensor failures: stuck-at, drift, dropout (10 scenarios)
    Total: 40 disturbance scenarios
    """
    print("Generating 40 non-leak disturbance scenarios...", flush=True)
    t0 = time.time()
    rng = np.random.default_rng(600000)
    
    scen_ids = []
    types = []
    noisy_pressures = []
    descriptions = []
    
    # 1. Pump / valve disturbances (10 scenarios)
    for i in range(10):
        s_id = f"disturb_pump_valve_{i+1:02d}"
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.time.duration = SCENARIO_DURATION
        wn.options.time.hydraulic_timestep = STEP
        wn.options.time.report_timestep = STEP
        # Shift pattern to morning or evening
        wn.options.time.pattern_start = (8 if i % 2 == 0 else 18) * 3600
        
        # Trigger pump 10 status switch at t=300s
        act = wntr.network.controls.ControlAction(wn.get_link("10"), "status", 0 if i % 2 == 0 else 1)
        cond = wntr.network.controls.SimTimeCondition(wn, "==", 300)
        ctrl = wntr.network.controls.Control(cond, act)
        wn.add_control(f"ctrl_pump_{i}", ctrl)
        
        sim = wntr.sim.WNTRSimulator(wn)
        res = sim.run_sim()
        p = res.node["pressure"][SENSORS].values
        p_noisy = p + rng.normal(0, NOISE_SD, p.shape)
        
        scen_ids.append(s_id)
        types.append("pump_valve_switch")
        noisy_pressures.append(p_noisy)
        descriptions.append(f"Pump 10 status switch at t=300s (action={act})")
        
    # 2. Tank level shift disturbances (10 scenarios)
    for i in range(10):
        s_id = f"disturb_tank_shift_{i+1:02d}"
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.time.duration = SCENARIO_DURATION
        wn.options.time.hydraulic_timestep = STEP
        wn.options.time.report_timestep = STEP
        wn.options.time.pattern_start = 13 * 3600
        
        # Shift initial tank level by +/- 1.5m
        shift = float(rng.uniform(-1.5, 1.5))
        tank = wn.get_node("1")
        tank.init_level = max(1.0, tank.init_level + shift)
        
        sim = wntr.sim.WNTRSimulator(wn)
        res = sim.run_sim()
        p = res.node["pressure"][SENSORS].values
        p_noisy = p + rng.normal(0, NOISE_SD, p.shape)
        
        scen_ids.append(s_id)
        types.append("tank_level_shift")
        noisy_pressures.append(p_noisy)
        descriptions.append(f"Tank 1 initial level shift of {shift:+.2f}m")
        
    # 3. Sudden demand surges (fire flow style at random customer junctions) (10 scenarios)
    wn_base = wntr.network.WaterNetworkModel(NET_FILE)
    juncs = wn_base.junction_name_list
    for i in range(10):
        s_id = f"disturb_fire_flow_{i+1:02d}"
        target_j = juncs[(i * 7 + 3) % len(juncs)]
        surge_lps = float(rng.uniform(150.0, 250.0)) # 150 to 250 lps
        surge_m3s = surge_lps / 1000.0
        
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.time.duration = SCENARIO_DURATION
        wn.options.time.hydraulic_timestep = STEP
        wn.options.time.report_timestep = STEP
        wn.options.time.pattern_start = 9 * 3600
        
        # Increase base demand to simulate fire flow surge
        wn.get_node(target_j).demand_timeseries_list[0].base_value += surge_m3s
        
        sim = wntr.sim.WNTRSimulator(wn)
        res = sim.run_sim()
        p = res.node["pressure"][SENSORS].values
        p_noisy = p + rng.normal(0, NOISE_SD, p.shape)
        
        scen_ids.append(s_id)
        types.append("fire_flow_demand_surge")
        noisy_pressures.append(p_noisy)
        descriptions.append(f"Fire flow demand surge of {surge_lps:.1f} lps at junction {target_j}")
        
    # 4. Sensor failure scenarios (10 scenarios: stuck, drift, dropout)
    # Generate baseline normal pressure first
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = SCENARIO_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    base_p = res.node["pressure"][SENSORS].values
    
    # Failures on sensor index 0, 1, 2, 3...
    for i in range(10):
        s_id = f"disturb_sensor_fault_{i+1:02d}"
        fault_sensor_idx = i % 8
        fault_type = ["stuck_at", "drift", "dropout"][i % 3]
        
        p_corrupt = base_p.copy() + rng.normal(0, NOISE_SD, base_p.shape)
        
        if fault_type == "stuck_at":
            # Sensor freezes at a fixed reading from t=300s onwards
            stuck_val = float(p_corrupt[30, fault_sensor_idx])
            p_corrupt[30:, fault_sensor_idx] = stuck_val
            desc = f"Sensor {SENSORS[fault_sensor_idx]} stuck at {stuck_val:.2f}m from t=300s"
        elif fault_type == "drift":
            # Sensor drifts upward or downward by 0.05m per step from t=300s
            drift_rate = 0.05 if i % 2 == 0 else -0.05
            drift_steps = np.arange(len(p_corrupt) - 30) * drift_rate
            p_corrupt[30:, fault_sensor_idx] += drift_steps
            desc = f"Sensor {SENSORS[fault_sensor_idx]} drifting ({drift_rate:+.2f} m/step) from t=300s"
        else: # dropout
            # Sensor reading drops to 0.0 from t=300s onwards
            p_corrupt[30:, fault_sensor_idx] = 0.0
            desc = f"Sensor {SENSORS[fault_sensor_idx]} dropout to 0.0m from t=300s"
            
        scen_ids.append(s_id)
        types.append(f"sensor_fault_{fault_type}")
        noisy_pressures.append(p_corrupt)
        descriptions.append(desc)
        
    out_disturb = os.path.join(DATA_DIR, "disturbances.npz")
    np.savez_compressed(
        out_disturb,
        scen_ids=np.array(scen_ids),
        disturbance_types=np.array(types),
        descriptions=np.array(descriptions),
        noisy_pressures=np.array(noisy_pressures),
        times=res.node["pressure"].index.values.astype(int),
        sensor_ids=np.array(SENSORS)
    )
    print(f"Saved {len(scen_ids)} disturbance scenarios to {out_disturb} in {time.time()-t0:.1f}s.")
    return out_disturb

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", type=int, default=None)
    parser.add_argument("--disturbances-only", action="store_true")
    args = parser.parse_args()
    
    if args.disturbances_only:
        generate_disturbances()
    else:
        run_scenarios(subset_n=args.subset)
        if args.subset is None:
            generate_disturbances()
