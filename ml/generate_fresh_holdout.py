"""
AquaSentinel - Fresh Hold-Out Dataset & No-Leak Days Generator (/ml/generate_fresh_holdout.py)

Generates:
1. Fresh Hold-Out Scenarios (data/experiments/scenarios_fresh_holdout.npz):
   - Leak areas: [0.0035, 0.0045, 0.0105] m2 (strictly outside training and dev)
   - Start hours: [4, 8, 11, 15, 18, 22] (distinct from dev hours [2, 6, 9, 13, 16, 20])
   - 2 start hours per junction per size:
     - 0.0035 m2: hours [START_HOURS[j_i % 6], START_HOURS[(j_i + 3) % 6]]
     - 0.0045 m2: hours [START_HOURS[(j_i + 1) % 6], START_HOURS[(j_i + 4) % 6]]
     - 0.0105 m2: hours [START_HOURS[(j_i + 2) % 6], START_HOURS[(j_i + 5) % 6]]
   - 92 junctions * 3 sizes * 2 hours = 552 scenarios * 3 noise seeds = 1,656 runs
2. Fresh Matched Zero-Leak Controls (data/experiments/matched_controls_fresh.npz):
   - Exact same 552 scenarios, exact same demand seeds and noise seeds, but NO leak added.
3. Fresh 24 No-Leak Days (data/experiments/no_leak_days_fresh_eval.npz):
   - 24 independent 24-hour days with demand variation N(1.0, 0.08) and sensor noise.
"""

import os
import sys
import time
import concurrent.futures
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXP_DIR = os.path.join(DATA_DIR, "experiments")
NET_FILE = os.path.join(DATA_DIR, "Net3.inp")

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
STEP = 10
SCENARIO_DURATION = 1200 # 20 mins
LEAK_START_T = 300       # t=300s
DAY_DURATION = 86400     # 24 hours

FRESH_SIZES = [0.0035, 0.0045, 0.0105]
FRESH_START_HOURS = [4, 8, 11, 15, 18, 22]
N_FRESH_DAYS = 24


def simulate_single_leak(task):
    scen_id, j_node, area, start_hr, seed = task
    rng = np.random.default_rng(seed)

    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = SCENARIO_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = start_hr * 3600

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

    noisy_p_3seeds = []
    for s_idx in range(3):
        noise_rng = np.random.default_rng(seed + 20000 * (s_idx + 1))
        noisy_p = clean_p + noise_rng.normal(0, NOISE_SD, clean_p.shape)
        noisy_p_3seeds.append(noisy_p)
    noisy_p_3seeds = np.array(noisy_p_3seeds)

    leak_lps = 0.0
    if "leak_demand" in res.node:
        leak_lps = float(res.node["leak_demand"][j_node].iloc[-1] * 1000.0)
    if leak_lps <= 0.0:
        p_leak = max(0.0, float(res.node["pressure"][j_node].iloc[-1]))
        leak_lps = float(0.75 * area * np.sqrt(2 * 9.81 * p_leak) * 1000.0)

    return scen_id, j_node, area, start_hr, times, clean_p, noisy_p_3seeds, leak_lps


def simulate_single_control(task):
    scen_id, j_node, area, start_hr, seed = task
    rng = np.random.default_rng(seed)

    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = SCENARIO_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = start_hr * 3600

    scen_scale = 1.0 + float(rng.normal(0, 0.04))
    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            p_m = m * scen_scale * (1.0 + float(rng.normal(0, 0.02)))
            perturbed.append(max(0.0, p_m))
        pat.multipliers = perturbed

    # NO LEAK ADDED (Control)
    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()

    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()

    noisy_p_3seeds = []
    for s_idx in range(3):
        noise_rng = np.random.default_rng(seed + 20000 * (s_idx + 1))
        noisy_p = clean_p + noise_rng.normal(0, NOISE_SD, clean_p.shape)
        noisy_p_3seeds.append(noisy_p)
    noisy_p_3seeds = np.array(noisy_p_3seeds)

    return scen_id, j_node, area, start_hr, times, clean_p, noisy_p_3seeds


def simulate_single_day(day_spec):
    day_idx, seed, base_scale, hourly_noise = day_spec
    rng = np.random.default_rng(seed)

    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = DAY_DURATION
    wn.options.time.hydraulic_timestep = STEP
    wn.options.time.report_timestep = STEP
    wn.options.time.pattern_start = 0

    for pat in wn.patterns.values():
        perturbed = []
        for m in pat.multipliers:
            m_noisy = m * base_scale * (1.0 + float(rng.normal(0, hourly_noise)))
            perturbed.append(max(0.0, m_noisy))
        pat.multipliers = perturbed

    sim = wntr.sim.WNTRSimulator(wn)
    res = sim.run_sim()

    p_df = res.node["pressure"][SENSORS]
    times = p_df.index.values.astype(int)
    clean_p = p_df.values.copy()
    noisy_p = clean_p + rng.normal(0, NOISE_SD, clean_p.shape)

    return day_idx, times, clean_p, noisy_p, base_scale


def generate_all():
    os.makedirs(EXP_DIR, exist_ok=True)
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    junctions = list(wn.junction_name_list)

    # 1. Build leak tasks
    print("=== 1. Building Fresh Hold-Out Leak Tasks ===", flush=True)
    leak_tasks = []
    task_idx = 0
    for j_i, j_node in enumerate(junctions):
        # 3 sizes, 2 hours each
        hour_pairs = [
            [FRESH_START_HOURS[j_i % 6], FRESH_START_HOURS[(j_i + 3) % 6]],
            [FRESH_START_HOURS[(j_i + 1) % 6], FRESH_START_HOURS[(j_i + 4) % 6]],
            [FRESH_START_HOURS[(j_i + 2) % 6], FRESH_START_HOURS[(j_i + 5) % 6]],
        ]
        for a_idx, area in enumerate(FRESH_SIZES):
            for h in hour_pairs[a_idx]:
                scen_id = f"fresh_leak_{j_node}_a{int(area*10000):04d}_h{h:02d}"
                seed = 700000 + task_idx
                leak_tasks.append((scen_id, j_node, area, h, seed))
                task_idx += 1

    print(f"Total fresh leak scenarios to simulate: {len(leak_tasks)} (92 junctions * 6 runs)", flush=True)

    t0 = time.time()
    leak_results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_single_leak, t): t[0] for t in leak_tasks}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            leak_results.append(res)
            completed += 1
            if completed % 100 == 0 or completed == len(leak_tasks):
                print(f"  Leaks: {completed}/{len(leak_tasks)} done [{time.time()-t0:.1f}s]", flush=True)

    leak_results.sort(key=lambda x: x[0])
    scen_ids = [r[0] for r in leak_results]
    leak_nodes = [r[1] for r in leak_results]
    leak_areas = [r[2] for r in leak_results]
    start_hours = [r[3] for r in leak_results]
    times = leak_results[0][4]
    clean_pressures = np.array([r[5] for r in leak_results])
    noisy_3seeds = np.array([r[6] for r in leak_results])
    leak_flows = np.array([r[7] for r in leak_results])

    leak_npz = os.path.join(EXP_DIR, "scenarios_fresh_holdout.npz")
    np.savez_compressed(
        leak_npz,
        scen_ids=np.array(scen_ids),
        leak_nodes=np.array(leak_nodes),
        leak_areas=np.array(leak_areas),
        start_hours=np.array(start_hours),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=clean_pressures,
        noisy_pressures_3seeds=noisy_3seeds,
        leak_flows=leak_flows
    )
    print(f"Saved {len(scen_ids)} fresh leak scenarios to {leak_npz} ({os.path.getsize(leak_npz):,} bytes) in {time.time()-t0:.1f}s.\n", flush=True)

    # 2. Build matched controls
    print("=== 2. Building Fresh Matched Zero-Leak Controls ===", flush=True)
    control_tasks = list(leak_tasks) # Exact same tasks without leak
    t0 = time.time()
    ctrl_results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_single_control, t): t[0] for t in control_tasks}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            ctrl_results.append(res)
            completed += 1
            if completed % 100 == 0 or completed == len(control_tasks):
                print(f"  Controls: {completed}/{len(control_tasks)} done [{time.time()-t0:.1f}s]", flush=True)

    ctrl_results.sort(key=lambda x: x[0])
    ctrl_scen_ids = [r[0] for r in ctrl_results]
    ctrl_clean_p = np.array([r[5] for r in ctrl_results])
    ctrl_noisy_3seeds = np.array([r[6] for r in ctrl_results])

    ctrl_npz = os.path.join(EXP_DIR, "matched_controls_fresh.npz")
    np.savez_compressed(
        ctrl_npz,
        scen_ids=np.array(ctrl_scen_ids),
        start_hours=np.array(start_hours),
        times=times,
        sensor_ids=np.array(SENSORS),
        clean_pressures=ctrl_clean_p,
        noisy_pressures_3seeds=ctrl_noisy_3seeds
    )
    print(f"Saved {len(ctrl_scen_ids)} matched controls to {ctrl_npz} ({os.path.getsize(ctrl_npz):,} bytes) in {time.time()-t0:.1f}s.\n", flush=True)

    # 3. Build 24 Fresh No-Leak Days
    print(f"=== 3. Simulating {N_FRESH_DAYS} Fresh 24-Hour No-Leak Days ===", flush=True)
    day_specs = []
    rng_master = np.random.default_rng(888888)
    for d in range(N_FRESH_DAYS):
        # Sample base demand scale: mean 1.0, std 0.08, with some stress days
        if d in [4, 11]:
            scale = 1.15 # High-demand stress
        elif d in [8, 17]:
            scale = 0.85 # Low-demand stress
        else:
            scale = float(np.clip(rng_master.normal(1.0, 0.06), 0.88, 1.12))
        seed = 800000 + d * 50
        day_specs.append((d + 1, seed, scale, 0.02))

    t0 = time.time()
    day_results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(simulate_single_day, s): s[0] for s in day_specs}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            day_results.append(res)
            completed += 1
            print(f"  Day {res[0]}/{N_FRESH_DAYS} done (scale: {res[4]:.2f}) [{time.time()-t0:.1f}s]", flush=True)

    day_results.sort(key=lambda x: x[0])
    day_ids = [f"fresh_day_{r[0]:02d}" for r in day_results]
    day_scales = [r[4] for r in day_results]
    day_clean_p = np.array([r[2] for r in day_results])
    day_noisy_p = np.array([r[3] for r in day_results])

    days_npz = os.path.join(EXP_DIR, "no_leak_days_fresh_eval.npz")
    np.savez_compressed(
        days_npz,
        day_ids=np.array(day_ids),
        day_scales=np.array(day_scales),
        sensor_ids=np.array(SENSORS),
        clean_pressures=day_clean_p,
        noisy_pressures=day_noisy_p
    )
    print(f"Saved {N_FRESH_DAYS} fresh no-leak days to {days_npz} ({os.path.getsize(days_npz):,} bytes) in {time.time()-t0:.1f}s.", flush=True)
    print("\nAll fresh hold-out data generation successfully completed!")


if __name__ == "__main__":
    generate_all()
