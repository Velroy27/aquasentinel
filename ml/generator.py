"""
AquaSentinel - Extended-Period Scenario Generator
Extends baseline steady-state snapshot into dynamic time-series scenarios with diurnal demand patterns.
Generates:
- Training scenarios (areas 0.005, 0.010 m^2)
- Held-out test scenarios (area 0.0075 m^2)
- Multi-day no-leak baseline scenarios with diurnal demand variation for false alarm testing
- 10s sensor readings with 0.3 m Gaussian noise
"""
import os
import sys
import json
import time
import argparse
import numpy as np
import wntr

SEED = 42
RNG = np.random.default_rng(SEED)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
DATA_DIR = os.path.join(BASE_DIR, "data")
SCENARIO_DIR = os.path.join(DATA_DIR, "scenarios")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
TRAIN_AREAS = [0.005, 0.010]
TEST_AREA = 0.0075

SCENARIO_DURATION = 1200   # 20 minutes
STEP = 10                  # 10 seconds
LEAK_START_T = 300         # Leak starts at t=300s (5 min baseline)

def get_base_network(duration=SCENARIO_DURATION, step=STEP, pattern_start=0):
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = duration
    wn.options.time.hydraulic_timestep = step
    wn.options.time.report_timestep = step
    wn.options.time.pattern_start = pattern_start
    return wn

def simulate_scenario(leak_node=None, area=None, start_t=LEAK_START_T, duration=SCENARIO_DURATION, step=STEP, pattern_start=0, demand_scale=1.0, rng=None):
    if rng is None:
        rng = RNG
    wn = get_base_network(duration=duration, step=step, pattern_start=pattern_start)
    
    # Apply global demand scaling if varied
    if demand_scale != 1.0:
        for pat in wn.patterns.values():
            pat.multipliers = [m * demand_scale for m in pat.multipliers]
            
    if leak_node is not None and area is not None and area > 0:
        wn.get_node(leak_node).add_leak(wn, area=area, start_time=start_t)
        
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()
    
    p_df = res.node["pressure"][SENSORS]
    clean_p = p_df.values.copy()
    noisy_p = clean_p + rng.normal(0, NOISE_SD, clean_p.shape)
    times = p_df.index.values.astype(int)
    
    leak_flow_lps = 0.0
    if leak_node is not None and area is not None and area > 0 and "leak_demand" in res.node:
        leak_flow_series = res.node["leak_demand"][leak_node]
        # max or post-onset mean leak flow in lps
        leak_flow_lps = float(leak_flow_series.iloc[-1] * 1000.0) # m3/s to lps
        if leak_flow_lps <= 0.0:
            # Fallback for nodes with near-zero PDD head: standard orifice equation
            # Q = C_d * A * sqrt(2 * g * max(P, 0))
            p_leak = max(0.0, float(res.node["pressure"][leak_node].iloc[-1]))
            leak_flow_lps = float(0.75 * area * np.sqrt(2 * 9.81 * p_leak) * 1000.0)
            
    return times, clean_p, noisy_p, leak_flow_lps

def generate_dataset(subset_n=None, out_dir=DATA_DIR):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(SCENARIO_DIR, exist_ok=True)
    
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    junctions = wn.junction_name_list
    
    if subset_n is not None and subset_n > 0:
        target_junctions = junctions[:subset_n]
        suffix = f"_subset{subset_n}"
    else:
        target_junctions = junctions
        suffix = ""
        
    print(f"Generating scenarios for {len(target_junctions)} junctions (Train: {TRAIN_AREAS}, Test: {TEST_AREA})...")
    
    all_areas = [(a, False) for a in TRAIN_AREAS] + [(TEST_AREA, True)]
    
    manifest = []
    scen_ids = []
    leak_nodes = []
    leak_areas = []
    leak_flows = []
    start_times = []
    is_test_list = []
    clean_pressures = []
    noisy_pressures = []
    
    scenario_count = 0
    t0 = time.time()
    
    # We cycle pattern_start through 4 hours of the day (8:00 morning peak, 13:00 midday, 19:00 evening peak, 2:00 night low)
    peak_hours = [8, 13, 19, 2]
    
    for j_idx, j_node in enumerate(target_junctions):
        for a_idx, (area, is_test) in enumerate(all_areas):
            scen_id = f"scen_{'test' if is_test else 'train'}_node_{j_node}_a{int(area*10000):04d}"
            pat_hr = peak_hours[(j_idx + a_idx) % len(peak_hours)]
            pattern_start = pat_hr * 3600
            
            # Seed per scenario for reproducibility
            scen_rng = np.random.default_rng(SEED + 1000 * j_idx + a_idx)
            
            times, clean_p, noisy_p, leak_lps = simulate_scenario(
                leak_node=j_node,
                area=area,
                start_t=LEAK_START_T,
                duration=SCENARIO_DURATION,
                step=STEP,
                pattern_start=pattern_start,
                rng=scen_rng
            )
            
            scen_ids.append(scen_id)
            leak_nodes.append(j_node)
            leak_areas.append(area)
            leak_flows.append(leak_lps)
            start_times.append(LEAK_START_T)
            is_test_list.append(is_test)
            clean_pressures.append(clean_p)
            noisy_pressures.append(noisy_p)
            
            manifest.append({
                "id": scen_id,
                "leak_node": j_node,
                "leak_size_lps": round(leak_lps, 2),
                "leak_area_m2": area,
                "start_t": LEAK_START_T,
                "is_test": is_test,
                "pattern_start_hour": pat_hr,
                "description": f"Leak at node {j_node} (area {area} m2, {leak_lps:.1f} lps) starting at t={LEAK_START_T}s (hour {pat_hr}:00)"
            })
            scenario_count += 1
            if scenario_count % 10 == 0 or scenario_count == len(target_junctions) * len(all_areas):
                print(f"  Generated {scenario_count}/{len(target_junctions) * len(all_areas)} scenarios ({time.time()-t0:.1f}s)")
                
    npz_path = os.path.join(out_dir, f"scenarios{suffix}.npz")
    np.savez_compressed(
        npz_path,
        scen_ids=np.array(scen_ids),
        leak_nodes=np.array(leak_nodes),
        leak_areas=np.array(leak_areas),
        leak_flows=np.array(leak_flows),
        start_times=np.array(start_times),
        is_test=np.array(is_test_list),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=np.array(clean_pressures),
        noisy_pressures=np.array(noisy_pressures)
    )
    
    manifest_path = os.path.join(out_dir, f"manifest{suffix}.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"Saved {scenario_count} scenarios to {npz_path} and manifest to {manifest_path}")
    
    # Now generate no-leak days if full or requested
    no_leak_days_path = os.path.join(out_dir, f"no_leak_days{suffix}.npz")
    print(f"Generating no-leak days for false alarm testing...")
    n_days = 2 if subset_n is not None else 5
    day_clean = []
    day_noisy = []
    day_ids = []
    
    for d in range(n_days):
        day_id = f"no_leak_day_{d+1}"
        day_ids.append(day_id)
        day_rng = np.random.default_rng(SEED + 50000 + d)
        # Demand variation: 0.95 to 1.05
        demand_scale = 1.0 + float(day_rng.normal(0, 0.03))
        
        # Simulate full 24h at 10s step
        times_day, clean_p_day, noisy_p_day, _ = simulate_scenario(
            leak_node=None,
            area=None,
            start_t=0,
            duration=86400,
            step=STEP,
            pattern_start=0,
            demand_scale=demand_scale,
            rng=day_rng
        )
        day_clean.append(clean_p_day)
        day_noisy.append(noisy_p_day)
        print(f"  Simulated {day_id}: demand_scale={demand_scale:.3f}, timesteps={len(times_day)}")
        
    np.savez_compressed(
        no_leak_days_path,
        day_ids=np.array(day_ids),
        times=times_day,
        sensor_ids=np.array(SENSORS),
        clean_pressures=np.array(day_clean),
        noisy_pressures=np.array(day_noisy)
    )
    print(f"Saved {n_days} no-leak days to {no_leak_days_path}")
    return npz_path, manifest_path, no_leak_days_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", type=int, default=None, help="Number of junctions to subset (e.g. 5)")
    args = parser.parse_args()
    generate_dataset(subset_n=args.subset)
