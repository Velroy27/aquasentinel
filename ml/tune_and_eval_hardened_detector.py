"""
AquaSentinel - Detector Tuning, Evaluation, and Hardening
Strict Data Split:
- Fit & Tune on:
  - 8 training leak sizes (736 scenarios * 3 noise seeds)
  - 30 tuning & validation no-leak days (data/no_leak_days_all_tune.npz)
- Final single evaluation on:
  - 2 held-out test leak sizes (0.0065, 0.0075 m^2 -> 368 scenarios * 3 seeds = 1104 test runs)
  - 4 untouched no-leak days (data/no_leak_days_untouched_eval.npz)
  - 40 non-leak disturbances (data/disturbances.npz)
Outputs:
- data/detection_curve.png
- ml/detector_hardened.joblib
"""
import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import joblib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ml.detector_hardened import HardenedDetector, SensorHealthChecker, SENSORS, NOISE_SD, CONFIRM_WINDOW_STEPS, COOLDOWN_STEPS

DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")
SCENARIOS_FILE = os.path.join(DATA_DIR, "scenarios_hardened.npz")
TUNE_DAYS_FILE = os.path.join(DATA_DIR, "no_leak_days_all_tune.npz")
EVAL_DAYS_FILE = os.path.join(DATA_DIR, "no_leak_days_untouched_eval.npz")
DISTURB_FILE = os.path.join(DATA_DIR, "disturbances.npz")
CURVE_PNG = os.path.join(DATA_DIR, "detection_curve.png")
MODEL_PATH = os.path.join(ML_DIR, "detector_hardened.joblib")

def evaluate_day_false_alarms(detector, day_p, start_sec=0):
    """
    Evaluate false alarms on a 24-hr day (8641 steps).
    Returns:
        fa_raw_events: debounced at 90s
        fa_cooldown_events: with 30-min (180 steps) cooldown
    """
    dets, _, _ = detector.predict_series(day_p, start_time_of_day=start_sec)
    det_steps = np.where(dets)[0]
    
    # 1. Raw debounced (90s = 9 steps)
    raw_events = 0
    last_step = -9999
    for s in det_steps:
        if s - last_step >= 9:
            raw_events += 1
            last_step = s
            
    # 2. 30-min cooldown (180 steps)
    cooldown_events = 0
    last_cd_step = -9999
    for s in det_steps:
        if s - last_cd_step >= COOLDOWN_STEPS:
            cooldown_events += 1
            last_cd_step = s
            
    return raw_events, cooldown_events

def run_evaluation():
    print("--- 1. Training Isolation Forest on 30 Tuning Days ---")
    detector = HardenedDetector(cusum_thresh=5.5, comb_thresh=0.55)
    detector.fit_isolation_forest(TUNE_DAYS_FILE)
    
    print("\n--- 2. Loading Datasets ---")
    scen_data = np.load(SCENARIOS_FILE)
    scen_ids = scen_data["scen_ids"]
    leak_nodes = scen_data["leak_nodes"]
    leak_areas = scen_data["leak_areas"]
    start_hours = scen_data["start_hours"]
    is_test = scen_data["is_test"]
    clean_p = scen_data["clean_pressures"]
    noisy_3seeds = scen_data["noisy_pressures_3seeds"]
    
    # Calculate visibility (steady-state drop >= 0.9m at any sensor)
    pre_drop = np.mean(clean_p[:, :29, :], axis=1)
    post_drop = np.mean(clean_p[:, 60:, :], axis=1)
    max_clean_drops = np.max(pre_drop - post_drop, axis=1)
    is_visible = max_clean_drops >= 0.90
    
    train_indices = np.where(~is_test)[0]
    test_indices = np.where(is_test)[0]
    print(f"Total scenarios: {len(scen_ids)} (Train: {len(train_indices)}, Test: {len(test_indices)})")
    print(f"Train visible: {np.sum(is_visible[train_indices])}/{len(train_indices)} ({np.mean(is_visible[train_indices]):.1%})")
    print(f"Test visible:  {np.sum(is_visible[test_indices])}/{len(test_indices)} ({np.mean(is_visible[test_indices]):.1%})")
    
    # Load 30 tuning days and sample 6 days for threshold tuning
    tune_days_data = np.load(TUNE_DAYS_FILE)
    tune_noisy_p = tune_days_data["noisy_pressures"]
    
    print("\n--- 3. Threshold Sweep on Training Data & Tuning Days ---")
    threshold_grid = [0.55, 0.60, 0.62, 0.65, 0.68, 0.70]
    sweep_results = []
    
    # Quick subsample of train scenarios for fast sweep: 184 train scenarios across 3 seeds = 552 runs
    sub_train = train_indices[::4]
    sample_tune_days = tune_noisy_p[:8] # 8 tuning days for sweep
    
    for th in threshold_grid:
        detector.comb_thresh = th
        
        # Test detection on train scenarios
        train_vis_detected = 0
        train_vis_total = 0
        delays = []
        for idx in sub_train:
            start_sec = int(start_hours[idx] * 3600)
            vis = is_visible[idx]
            for seed_idx in range(3):
                P = noisy_3seeds[idx, seed_idx]
                dets, _, _ = detector.predict_series(P, start_time_of_day=start_sec)
                post_leak = np.where(dets & (np.arange(len(dets)) * 10 >= 300))[0]
                if vis:
                    train_vis_total += 1
                    if len(post_leak) > 0:
                        train_vis_detected += 1
                        delays.append(post_leak[0] * 10 - 300)
                        
        det_rate_vis = train_vis_detected / train_vis_total if train_vis_total > 0 else 0
        med_delay = float(np.median(delays)) if delays else 0.0
        
        # Test FA on tuning days
        total_cd_fa = 0
        total_raw_fa = 0
        for d in range(len(sample_tune_days)):
            raw_fa, cd_fa = evaluate_day_false_alarms(detector, sample_tune_days[d])
            total_raw_fa += raw_fa
            total_cd_fa += cd_fa
        fa_cd_per_day = total_cd_fa / len(sample_tune_days)
        fa_raw_per_day = total_raw_fa / len(sample_tune_days)
        
        sweep_results.append({
            "threshold": th,
            "train_vis_det_rate": det_rate_vis,
            "med_delay_s": med_delay,
            "fa_cd_per_day": fa_cd_per_day,
            "fa_raw_per_day": fa_raw_per_day
        })
        print(f"Th={th:.2f} | Train Vis Det: {det_rate_vis:.1%} | Delay: {med_delay:.0f}s | FA/day (cd): {fa_cd_per_day:.2f} | FA/day (raw): {fa_raw_per_day:.2f}")

    # Choose operating threshold: highest detection with FA (cd) <= 4.0/day
    valid_cands = [s for s in sweep_results if s["fa_cd_per_day"] <= 4.0]
    if not valid_cands:
        valid_cands = sweep_results
    chosen = max(valid_cands, key=lambda s: s["train_vis_det_rate"])
    chosen_th = chosen["threshold"]
    detector.comb_thresh = chosen_th
    print(f"\n>>> Selected Operating Threshold: {chosen_th} (Train Vis Det: {chosen['train_vis_det_rate']:.1%}, FA/day: {chosen['fa_cd_per_day']:.2f}) <<<")
    
    # Save tuned detector
    joblib.dump(detector, MODEL_PATH, compress=3)
    
    print("\n--- 4. FINAL UNTOUCHED EVALUATION ---")
    # Load 4 untouched evaluation days
    eval_days_data = np.load(EVAL_DAYS_FILE)
    eval_noisy_p = eval_days_data["noisy_pressures"]
    n_eval_days = len(eval_noisy_p)
    print(f"Evaluating false alarms on {n_eval_days} UNTOUCHED evaluation days...")
    
    eval_cd_fa = 0
    eval_raw_fa = 0
    for d in range(n_eval_days):
        raw_fa, cd_fa = evaluate_day_false_alarms(detector, eval_noisy_p[d])
        eval_raw_fa += raw_fa
        eval_cd_fa += cd_fa
        print(f"  Eval Day {d+1}: {cd_fa} cooldown events ({raw_fa} raw events)")
        
    final_fa_cd = eval_cd_fa / n_eval_days
    final_fa_raw = eval_raw_fa / n_eval_days
    print(f"Untouched Evaluation Days: {final_fa_cd:.2f} FA/day (with 30m cooldown), {final_fa_raw:.2f} FA/day (raw)")
    
    print("\nEvaluating on 368 Held-Out Test Scenarios (x 3 noise seeds = 1104 runs)...")
    test_results = []
    delays_vis = []
    
    # 2x2 table:
    #                 Detected    Not Detected
    # Visible
    # Not Visible
    table_vis_det = 0
    table_vis_nondet = 0
    table_nonvis_det = 0
    table_nonvis_nondet = 0
    
    by_size = {0.0065: {"det": 0, "tot": 0}, 0.0075: {"det": 0, "tot": 0}}
    by_time = {}
    
    for idx in test_indices:
        area = float(leak_areas[idx])
        start_hr = int(start_hours[idx])
        start_sec = start_hr * 3600
        vis = bool(is_visible[idx])
        
        if start_hr not in by_time:
            by_time[start_hr] = {"det": 0, "tot": 0}
            
        for seed_idx in range(3):
            P = noisy_3seeds[idx, seed_idx]
            dets, scores, _ = detector.predict_series(P, start_time_of_day=start_sec)
            post_leak = np.where(dets & (np.arange(len(dets)) * 10 >= 300))[0]
            detected = len(post_leak) > 0
            
            by_size[area]["tot"] += 1
            by_time[start_hr]["tot"] += 1
            if detected:
                by_size[area]["det"] += 1
                by_time[start_hr]["det"] += 1
                
            if vis:
                if detected:
                    table_vis_det += 1
                    delays_vis.append(post_leak[0] * 10 - 300)
                else:
                    table_vis_nondet += 1
            else:
                if detected:
                    table_nonvis_det += 1
                else:
                    table_nonvis_nondet += 1
                    
            test_results.append({
                "detected": detected,
                "visible": vis,
                "area": area,
                "hour": start_hr,
                "delay": post_leak[0] * 10 - 300 if detected else None
            })
            
    total_test = len(test_results)
    tot_detected = table_vis_det + table_nonvis_det
    overall_det_rate = tot_detected / total_test
    
    tot_visible = table_vis_det + table_vis_nondet
    vis_det_rate = table_vis_det / tot_visible if tot_visible > 0 else 0
    
    med_delay = float(np.median(delays_vis)) if delays_vis else 0.0
    p90_delay = float(np.percentile(delays_vis, 90)) if delays_vis else 0.0
    
    print(f"\n================ FINAL HELD-OUT METRICS ================")
    print(f"Overall Detection Rate: {tot_detected}/{total_test} = {overall_det_rate:.1%}")
    print(f"Visible Leaks Detection Rate: {table_vis_det}/{tot_visible} = {vis_det_rate:.1%}")
    print(f"Median Delay (visible): {med_delay:.0f} s | 90th percentile delay: {p90_delay:.0f} s")
    print(f"False Alarms per Day (Untouched 4 Days): {final_fa_cd:.2f} (with cooldown), {final_fa_raw:.2f} (raw)")
    print("\n2x2 Contingency Table (Counts across 1104 test runs):")
    print(f"                | Detected | Not Detected | Total")
    print(f"Visible (>0.9m) |  {table_vis_det:5d}   |    {table_vis_nondet:5d}     | {tot_visible:5d}")
    print(f"Not Visible     |  {table_nonvis_det:5d}   |    {table_nonvis_nondet:5d}     | {table_nonvis_det + table_nonvis_nondet:5d}")
    print(f"Total           |  {tot_detected:5d}   |    {total_test - tot_detected:5d}     | {total_test:5d}")
    
    print("\nDetection Rate by Held-Out Leak Size:")
    for a in sorted(by_size.keys()):
        d = by_size[a]["det"]
        t = by_size[a]["tot"]
        print(f"  Area {a*1000:.1f}e-3 m^2: {d}/{t} = {d/t:.1%}")
        
    print("\nDetection Rate by Start Time of Day:")
    for h in sorted(by_time.keys()):
        d = by_time[h]["det"]
        t = by_time[h]["tot"]
        print(f"  Hour {h:02d}:00: {d}/{t} = {d/t:.1%}")

    print("\n--- 5. Non-Leak Disturbances Evaluation ---")
    disturb_data = np.load(DISTURB_FILE)
    d_types = disturb_data["disturbance_types"]
    d_press = disturb_data["noisy_pressures"]
    
    disturb_counts = {}
    fault_flags = {}
    for i in range(len(d_types)):
        dtype = str(d_types[i])
        cat = "pump_valve" if "pump" in dtype else ("tank_shift" if "tank" in dtype else ("fire_flow" if "fire" in dtype else "sensor_fault"))
        if cat not in disturb_counts:
            disturb_counts[cat] = {"leak_alert": 0, "fault_flag": 0, "tot": 0}
            
        start_sec = 8 * 3600 if cat == "pump_valve" else (13 * 3600 if cat == "tank_shift" else (9 * 3600 if cat == "fire_flow" else 0))
        dets, scores, s_fault = detector.predict_series(d_press[i], start_time_of_day=start_sec)
        
        disturb_counts[cat]["tot"] += 1
        if np.any(dets):
            disturb_counts[cat]["leak_alert"] += 1
        if s_fault:
            disturb_counts[cat]["fault_flag"] += 1
            
    print("Non-Leak Disturbances Results:")
    for cat, res in disturb_counts.items():
        print(f"  {cat:15s}: {res['leak_alert']}/{res['tot']} leak alerts ({res['leak_alert']/res['tot']:.0%}), {res['fault_flag']}/{res['tot']} sensor faults flagged")

    print("\n--- 6. Generating ROC-style Trade-Off Curve ---")
    # Generate points across threshold sweep
    t_vals = [s["threshold"] for s in sweep_results]
    det_vals = [s["train_vis_det_rate"] * 100 for s in sweep_results]
    fa_vals = [s["fa_cd_per_day"] for s in sweep_results]
    
    plt.figure(figsize=(8, 6), dpi=150)
    plt.plot(fa_vals, det_vals, "o-", color="#1f77b4", linewidth=2.5, markersize=8, label="Detector Curve")
    plt.axvline(x=final_fa_cd, color="#d62728", linestyle="--", alpha=0.7, label=f"Final Untouched Eval FA ({final_fa_cd:.2f}/day)")
    plt.axhline(y=vis_det_rate * 100, color="#2ca02c", linestyle="--", alpha=0.7, label=f"Final Visible Det Rate ({vis_det_rate:.1%})")
    
    for i, txt in enumerate(t_vals):
        plt.annotate(f"Th={txt:.2f}", (fa_vals[i], det_vals[i]), textcoords="offset points", xytext=(8, -4), fontsize=9)
        
    plt.xlabel("False Alarms per Day (30-min Cooldown)", fontsize=11)
    plt.ylabel("Detection Rate on Visible Leaks (%)", fontsize=11)
    plt.title("AquaSentinel Detector: Detection Rate vs. False Alarm Rate", fontsize=12, fontweight="bold")
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend(loc="lower right", fontsize=10)
    plt.tight_layout()
    plt.savefig(CURVE_PNG)
    plt.close()
    print(f"Saved detection curve to {CURVE_PNG}")
    
    return {
        "sweep": sweep_results,
        "chosen_threshold": chosen_th,
        "overall_det_rate": overall_det_rate,
        "vis_det_rate": vis_det_rate,
        "med_delay": med_delay,
        "p90_delay": p90_delay,
        "final_fa_cd": final_fa_cd,
        "final_fa_raw": final_fa_raw,
        "table": (table_vis_det, table_vis_nondet, table_nonvis_det, table_nonvis_nondet),
        "by_size": by_size,
        "by_time": by_time,
        "disturbances": disturb_counts
    }

if __name__ == "__main__":
    run_evaluation()
