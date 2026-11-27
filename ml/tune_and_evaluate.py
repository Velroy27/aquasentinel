"""
AquaSentinel - Detector Tuning and Evaluation Pipeline
Follows strict protocol:
1. Expected-pressure baseline built on 14 tuning days only
2. Isolation Forest trained on 14 tuning days only
3. Thresholds chosen on training-size leaks (0.005, 0.010 m^2) and 14 tuning days only
4. Evaluation reported strictly on the 92 held-out 0.0075 m^2 leaks and 6 untouched days
5. Visibility definition: steady-state drop at any of the 8 sensors > 3*NOISE_SD (0.9 m)
"""
import os
import sys
import json
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ml.baseline_builder import build_baseline
from ml.residual_detector import ResidualCUSUMDetector

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
ML_DIR = os.path.join(BASE_DIR, "ml")

SCENARIOS_FILE = os.path.join(DATA_DIR, "scenarios.npz")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
TUNE_FILE = os.path.join(DATA_DIR, "no_leak_days_tune.npz")
EVAL_FILE = os.path.join(DATA_DIR, "no_leak_days_eval.npz")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
NOISE_SD = 0.3
VISIBILITY_THR = 3 * NOISE_SD # 0.9 m

def run_tuning_and_evaluation():
    print("=" * 60)
    print("STEP 1: Building expected diurnal baseline from 14 tuning days...")
    print("=" * 60)
    build_baseline()
    
    print("\n" + "=" * 60)
    print("STEP 2: Training Isolation Forest on 14 tuning days...")
    print("=" * 60)
    detector = ResidualCUSUMDetector()
    detector.fit_isolation_forest(TUNE_FILE)
    
    # Load scenarios and manifest
    scen_data = np.load(SCENARIOS_FILE)
    is_test = scen_data["is_test"]
    clean_p = scen_data["clean_pressures"]
    start_times = scen_data["start_times"]
    
    with open(MANIFEST_FILE) as f:
        manifest = json.load(f)
    start_hr_dict = {i: manifest[i].get("pattern_start_hour", 0) for i in range(len(manifest))}
    
    train_mask = (is_test == False)
    test_mask = (is_test == True)
    
    # Identify which leaks are visible (max clean drop across 8 sensors >= 0.9m)
    visible_test_mask = np.zeros(len(clean_p), dtype=bool)
    for idx in range(len(clean_p)):
        if is_test[idx]:
            # Post-leak clean pressure drop at sensors
            pre = np.mean(clean_p[idx, :25], axis=0)
            post = np.mean(clean_p[idx, 40:], axis=0)
            max_drop = float(np.max(pre - post))
            if max_drop >= VISIBILITY_THR:
                visible_test_mask[idx] = True
                
    n_test_visible = int(np.sum(visible_test_mask))
    n_test_total = int(np.sum(test_mask))
    print(f"\nHeld-out test leaks (area 0.0075 m2): Total = {n_test_total}, Visible (drop >= {VISIBILITY_THR}m) = {n_test_visible} ({n_test_visible/n_test_total*100:.1f}%)")
    
    print("\n" + "=" * 60)
    print("STEP 3: Threshold sweep on TRAINING leaks (0.005, 0.010 m2) and 14 TUNING days ONLY")
    print("=" * 60)
    
    # Sweep candidates: (cusum_thresh, k_slack, min_confirm_steps, iso_thresh)
    threshold_grid = [
        {"cusum_thresh": 4.0, "k_slack": 0.30, "min_confirm_steps": 2, "iso_thresh": 0.82},
        {"cusum_thresh": 4.5, "k_slack": 0.35, "min_confirm_steps": 2, "iso_thresh": 0.85},
        {"cusum_thresh": 5.0, "k_slack": 0.35, "min_confirm_steps": 3, "iso_thresh": 0.85},
        {"cusum_thresh": 5.5, "k_slack": 0.38, "min_confirm_steps": 3, "iso_thresh": 0.88},
        {"cusum_thresh": 6.0, "k_slack": 0.40, "min_confirm_steps": 3, "iso_thresh": 0.88},
        {"cusum_thresh": 6.5, "k_slack": 0.40, "min_confirm_steps": 3, "iso_thresh": 0.90},
        {"cusum_thresh": 7.0, "k_slack": 0.45, "min_confirm_steps": 3, "iso_thresh": 0.92},
        {"cusum_thresh": 8.0, "k_slack": 0.45, "min_confirm_steps": 4, "iso_thresh": 0.95},
    ]
    
    sweep_results = []
    for params in threshold_grid:
        detector.cusum_thresh = params["cusum_thresh"]
        detector.k_slack = params["k_slack"]
        detector.iso_thresh = params["iso_thresh"]
        min_confirm = params["min_confirm_steps"]
        
        # Evaluate on training leaks
        det_cnt, total_tr, tr_rate, tr_delay = detector.evaluate_scenarios(
            SCENARIOS_FILE, subset_mask=train_mask, start_hr_dict=start_hr_dict, min_confirm_steps=min_confirm
        )
        
        # Evaluate on 14 tuning no-leak days
        fa_cnt, n_days_tune, tune_fa_rate = detector.evaluate_false_alarms(TUNE_FILE, min_confirm_steps=min_confirm)
        
        res_row = {
            "cusum_thresh": params["cusum_thresh"],
            "k_slack": params["k_slack"],
            "min_confirm": min_confirm,
            "iso_thresh": params["iso_thresh"],
            "train_det_count": det_cnt,
            "train_total": total_tr,
            "train_det_rate": tr_rate,
            "train_median_delay": tr_delay,
            "tune_fa_per_day": tune_fa_rate
        }
        sweep_results.append(res_row)
        print(f"CUSUM={params['cusum_thresh']}, k={params['k_slack']}, confirm={min_confirm} -> Train Det: {tr_rate*100:.1f}%, Delay: {tr_delay:.1f}s, Tune FA/day: {tune_fa_rate:.2f}")
        
    # Choose optimal operating point based on training leaks and tuning days:
    # We want low false alarm rate (< 0.5 per day) while maximizing detection rate
    valid_candidates = [r for r in sweep_results if r["tune_fa_per_day"] <= 0.60]
    if not valid_candidates:
        valid_candidates = sweep_results
    chosen = max(valid_candidates, key=lambda x: x["train_det_rate"])
    
    print("\n" + "=" * 60)
    print("CHOSEN OPERATING POINT (Selected strictly on training data):")
    print(f"  CUSUM Threshold: {chosen['cusum_thresh']}")
    print(f"  Slack Allowance (k): {chosen['k_slack']} m")
    print(f"  Confirmation Steps: {chosen['min_confirm']} (within 90s window)")
    print(f"  IsoForest Threshold: {chosen['iso_thresh']}")
    print(f"  Training Detection Rate: {chosen['train_det_rate']*100:.1f}%")
    print(f"  Tuning False Alarms / Day: {chosen['tune_fa_per_day']:.2f}")
    print("=" * 60)
    
    # Configure detector with chosen operating point
    detector.cusum_thresh = chosen["cusum_thresh"]
    detector.k_slack = chosen["k_slack"]
    detector.iso_thresh = chosen["iso_thresh"]
    min_confirm = chosen["min_confirm"]
    
    print("\n" + "=" * 60)
    print("STEP 4: UNTOUCHED EVALUATION on 92 Test Leaks and 6 Untouched No-Leak Days")
    print("=" * 60)
    
    # 1. Overall detection on 92 held-out test leaks
    test_det_cnt, test_total, test_det_rate, test_delay = detector.evaluate_scenarios(
        SCENARIOS_FILE, subset_mask=test_mask, start_hr_dict=start_hr_dict, min_confirm_steps=min_confirm
    )
    
    # 2. Detection among visible test leaks (66 leaks)
    vis_det_cnt, vis_total, vis_det_rate, vis_delay = detector.evaluate_scenarios(
        SCENARIOS_FILE, subset_mask=visible_test_mask, start_hr_dict=start_hr_dict, min_confirm_steps=min_confirm
    )
    
    # 3. False alarms on 6 UNTOUCHED evaluation days
    eval_fa_cnt, n_eval_days, eval_fa_rate = detector.evaluate_false_alarms(EVAL_FILE, min_confirm_steps=min_confirm)
    
    # Also evaluate across all threshold points on the test set for the comparative table
    table_rows = []
    for params in threshold_grid:
        detector.cusum_thresh = params["cusum_thresh"]
        detector.k_slack = params["k_slack"]
        detector.iso_thresh = params["iso_thresh"]
        p_confirm = params["min_confirm_steps"]
        
        t_cnt, _, t_rate, t_del = detector.evaluate_scenarios(
            SCENARIOS_FILE, subset_mask=test_mask, start_hr_dict=start_hr_dict, min_confirm_steps=p_confirm
        )
        v_cnt, _, v_rate, _ = detector.evaluate_scenarios(
            SCENARIOS_FILE, subset_mask=visible_test_mask, start_hr_dict=start_hr_dict, min_confirm_steps=p_confirm
        )
        fa_cnt_eval, _, fa_rate_eval = detector.evaluate_false_alarms(EVAL_FILE, min_confirm_steps=p_confirm)
        
        table_rows.append({
            "cusum_thresh": params["cusum_thresh"],
            "k_slack": params["k_slack"],
            "min_confirm": params["min_confirm_steps"],
            "test_det_rate_overall": t_rate,
            "test_det_rate_visible": v_rate,
            "test_median_delay_s": t_del,
            "untouched_fa_per_day": fa_rate_eval
        })
        
    final_report = {
        "operating_point": {
            "cusum_thresh": chosen["cusum_thresh"],
            "k_slack": chosen["k_slack"],
            "min_confirm_steps": chosen["min_confirm"],
            "iso_thresh": chosen["iso_thresh"]
        },
        "held_out_test_metrics": {
            "leak_area_m2": 0.0075,
            "overall_detection_rate": test_det_rate,
            "overall_detected_count": test_det_cnt,
            "total_test_leaks": test_total,
            "sensor_coverage_ceiling": round(n_test_visible / test_total, 3),
            "visible_detection_rate": vis_det_rate,
            "visible_detected_count": vis_det_cnt,
            "visible_test_leaks": vis_total,
            "median_delay_s": test_delay
        },
        "untouched_no_leak_metrics": {
            "untouched_days": n_eval_days,
            "total_false_alarms": eval_fa_cnt,
            "false_alarms_per_day": eval_fa_rate
        },
        "threshold_tradeoff_table": table_rows
    }
    
    report_file = os.path.join(DATA_DIR, "detection_improvement_report.json")
    with open(report_file, "w") as f:
        json.dump(final_report, f, indent=2)
    print(f"\nFinal report saved to {report_file}")
    
    return final_report

if __name__ == "__main__":
    run_tuning_and_evaluation()
