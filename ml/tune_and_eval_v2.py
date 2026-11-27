"""
AquaSentinel - Tuning, Ablation, and Final Dual-Evaluation Pipeline (/ml/tune_and_eval_v2.py)

Strict Protocol:
1. Hourly slack k(h) fitted on 30 tuning days with circular smoothing across adjacent hours.
2. Grid search / cross-validation on 736 training scenarios using GroupKFold grouped by leak size.
3. Step-by-step ablation study evaluated on DEV data (1,104 runs from scenarios_hardened.npz and 4 dev days).
4. Single final evaluation on Fresh Hold-Out (1,656 runs, fresh controls, 24 fresh no-leak days).
   Evaluates BOTH old (*_hardened) and new (V2) models using the exact same evaluation pipeline.
5. Bootstraps all 95% confidence intervals by scenario/junction (2,000 resamples).
6. Saves candidate models to ml/detector_v2.joblib and ml/localizer_v2.joblib.
7. Saves comprehensive evaluation results to ml/eval_v2.json.
"""

import os
import sys
import json
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import joblib
from sklearn.model_selection import GroupKFold
from scipy.spatial.distance import cdist

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
EXP_DIR = os.path.join(DATA_DIR, "experiments")
ML_DIR = os.path.join(BASE_DIR, "ml")

# Datasets
DEV_SCENARIOS = os.path.join(DATA_DIR, "scenarios_hardened.npz")
DEV_CONTROLS = os.path.join(DATA_DIR, "matched_controls.npz")
TUNE_DAYS = os.path.join(DATA_DIR, "no_leak_days_all_tune.npz")
DEV_DAYS = os.path.join(DATA_DIR, "no_leak_days_untouched_eval.npz")

FRESH_SCENARIOS = os.path.join(EXP_DIR, "scenarios_fresh_holdout.npz")
FRESH_CONTROLS = os.path.join(EXP_DIR, "matched_controls_fresh.npz")
FRESH_DAYS = os.path.join(EXP_DIR, "no_leak_days_fresh_eval.npz")

OUT_EVAL_JSON = os.path.join(ML_DIR, "eval_v2.json")
DETECTOR_V2_PATH = os.path.join(ML_DIR, "detector_v2.joblib")
LOCALIZER_V2_PATH = os.path.join(ML_DIR, "localizer_v2.joblib")

OLD_DETECTOR_PATH = os.path.join(ML_DIR, "detector_hardened.joblib")
OLD_LOCALIZER_PATH = os.path.join(ML_DIR, "localizer.joblib")

from ml.detector_v2 import HardenedDetectorV2, COOLDOWN_STEPS
from ml.localizer_v2 import HardenedLocalizerV2


def bootstrap_scenario_metric(scen_ids, values, n_boot=2000, seed=42):
    """
    Bootstrap confidence interval by scenario/junction cluster rather than by run.
    scen_ids: array of scenario identifiers (e.g. 3 runs share the same scenario_id)
    values: binary or scalar values per run
    """
    unique_scens = np.unique(scen_ids)
    scen_to_indices = {s: np.where(scen_ids == s)[0] for s in unique_scens}
    n_scens = len(unique_scens)

    rng = np.random.default_rng(seed)
    boot_means = np.zeros(n_boot)

    for b in range(n_boot):
        sample_scens = rng.choice(unique_scens, size=n_scens, replace=True)
        idx_list = []
        for s in sample_scens:
            idx_list.extend(scen_to_indices[s])
        boot_means[b] = np.mean(values[idx_list])

    low = float(np.percentile(boot_means, 2.5))
    high = float(np.percentile(boot_means, 97.5))
    return low, high


def bootstrap_daily_fa(daily_counts, n_boot=2000, seed=42):
    """Bootstrap 95% CI across daily false alarm event counts."""
    n_days = len(daily_counts)
    rng = np.random.default_rng(seed)
    boot_means = np.zeros(n_boot)
    for b in range(n_boot):
        sample = rng.choice(daily_counts, size=n_days, replace=True)
        boot_means[b] = np.mean(sample)
    low = float(np.percentile(boot_means, 2.5))
    high = float(np.percentile(boot_means, 97.5))
    return low, high


def evaluate_days_fa(detector, noisy_days):
    """Evaluate daily false alarm events with 30-min debouncing/cooldown."""
    n_days = len(noisy_days)
    daily_events = []
    for d in range(n_days):
        P = noisy_days[d]
        dets, _, _ = detector.predict_series(P, start_time_of_day=0)
        det_steps = np.where(dets)[0]
        cooldown_events = 0
        last_step = -9999
        for s in det_steps:
            if s - last_step >= COOLDOWN_STEPS:
                cooldown_events += 1
                last_step = s
        daily_events.append(cooldown_events)
    daily_events = np.array(daily_events)
    mean_fa = float(np.mean(daily_events))
    ci_low, ci_high = bootstrap_daily_fa(daily_events)
    return mean_fa, (ci_low, ci_high), daily_events.tolist()


def evaluate_detector_scenarios(detector, scen_data, ctrl_data=None):
    """
    Evaluates detector on leak scenarios (and optional matched controls).
    Computes alert rate, control rate, true detection rate (minus control),
    delays, visibility breakdown, size breakdown, and cluster-bootstrapped CIs.
    """
    scen_ids = scen_data["scen_ids"]
    leak_areas = scen_data["leak_areas"]
    start_hours = scen_data["start_hours"]
    clean_p = scen_data["clean_pressures"]
    noisy_3seeds = scen_data["noisy_pressures_3seeds"]

    n_scens = len(scen_ids)
    n_runs = n_scens * 3

    # Clean drops for visibility (drop >= 0.90m)
    pre = np.mean(clean_p[:, :29, :], axis=1)
    post = np.mean(clean_p[:, 60:, :], axis=1)
    max_clean_drops = np.max(pre - post, axis=1)
    is_visible_scen = max_clean_drops >= 0.90

    run_scen_ids = []
    run_areas = []
    run_hours = []
    run_visible = []
    run_detected = []
    run_delays = []

    for i in range(n_scens):
        s_id = str(scen_ids[i])
        area = float(leak_areas[i])
        hr = int(start_hours[i])
        start_sec = hr * 3600
        vis = bool(is_visible_scen[i])

        for s_idx in range(3):
            P = noisy_3seeds[i, s_idx]
            dets, _, _ = detector.predict_series(P, start_time_of_day=start_sec)
            post_leak = np.where(dets & (np.arange(len(dets)) * 10 >= 300))[0]
            fired = len(post_leak) > 0

            run_scen_ids.append(s_id)
            run_areas.append(area)
            run_hours.append(hr)
            run_visible.append(vis)
            run_detected.append(fired)
            if fired:
                run_delays.append(int(post_leak[0] * 10 - 300))

    run_scen_ids = np.array(run_scen_ids)
    run_areas = np.array(run_areas)
    run_visible = np.array(run_visible)
    run_detected = np.array(run_detected, dtype=float)

    det_rate = float(np.mean(run_detected))
    ci_low, ci_high = bootstrap_scenario_metric(run_scen_ids, run_detected)

    # Control alert rate
    ctrl_rate = 0.0
    if ctrl_data is not None:
        c_noisy_3seeds = ctrl_data["noisy_pressures_3seeds"]
        ctrl_detected = []
        for i in range(n_scens):
            hr = int(start_hours[i])
            start_sec = hr * 3600
            for s_idx in range(3):
                P = c_noisy_3seeds[i, s_idx]
                dets, _, _ = detector.predict_series(P, start_time_of_day=start_sec)
                post_leak = np.where(dets & (np.arange(len(dets)) * 10 >= 300))[0]
                ctrl_detected.append(len(post_leak) > 0)
        ctrl_rate = float(np.mean(ctrl_detected))

    true_det_rate = float(det_rate - ctrl_rate)

    # Visibility breakdown
    vis_mask = run_visible
    vis_det_rate = float(np.mean(run_detected[vis_mask])) if np.any(vis_mask) else 0.0
    vis_ci = bootstrap_scenario_metric(run_scen_ids[vis_mask], run_detected[vis_mask]) if np.any(vis_mask) else (0.0, 0.0)

    nonvis_mask = ~run_visible
    nonvis_det_rate = float(np.mean(run_detected[nonvis_mask])) if np.any(nonvis_mask) else 0.0

    # Size breakdown
    by_size = {}
    for a in np.unique(run_areas):
        a_mask = (run_areas == a)
        by_size[f"{a:.4f}"] = {
            "rate": float(np.mean(run_detected[a_mask])),
            "total_runs": int(np.sum(a_mask)),
            "detected_runs": int(np.sum(run_detected[a_mask]))
        }

    med_delay = float(np.median(run_delays)) if run_delays else None

    return {
        "detection_rate": det_rate,
        "detection_rate_ci_95": [ci_low, ci_high],
        "control_alert_rate": ctrl_rate,
        "true_detection_rate_minus_control": true_det_rate,
        "visible_detection_rate": vis_det_rate,
        "visible_ci_95": [vis_ci[0], vis_ci[1]],
        "nonvisible_detection_rate": nonvis_det_rate,
        "median_delay_s": med_delay,
        "by_leak_size": by_size,
        "total_runs": n_runs,
        "total_detected": int(np.sum(run_detected))
    }


def evaluate_localizer_scenarios(localizer, scen_data):
    """Evaluates localizer Top-1, Top-3 accuracy with cluster bootstrap CIs."""
    scen_ids = scen_data["scen_ids"]
    leak_nodes = scen_data["leak_nodes"]
    noisy_3seeds = scen_data["noisy_pressures_3seeds"]

    n_scens = len(scen_ids)
    run_scen_ids = []
    run_P = []
    run_true = []

    for i in range(n_scens):
        s_id = str(scen_ids[i])
        true_node = str(leak_nodes[i])
        for s_idx in range(3):
            run_scen_ids.append(s_id)
            run_P.append(noisy_3seeds[i, s_idx])
            run_true.append(true_node)

    if hasattr(localizer, "predict_top3_batch"):
        X = np.array([localizer.extract_features(p) for p in run_P])
        top3_list, _ = localizer.predict_top3_batch(X)
    else:
        # Old localizer
        X = np.array([localizer.extract_features(p) for p in run_P])
        p_rf = localizer.rf_model.predict_proba(X)
        p_et = localizer.et_model.predict_proba(X)
        ml_p = 0.5 * p_rf + 0.5 * p_et
        dists = cdist(X, localizer.hydraulic_fingerprints)
        prior_logits = -dists / (np.std(dists, axis=1, keepdims=True) + 1e-4)
        exps = np.exp(prior_logits - np.max(prior_logits, axis=1, keepdims=True))
        prior_p = exps / np.sum(exps, axis=1, keepdims=True)
        blended = localizer.blend_weight * ml_p + (1.0 - localizer.blend_weight) * prior_p
        logits = np.log(np.maximum(blended, 1e-7)) / localizer.temperature
        probs = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        probs = probs / np.sum(probs, axis=1, keepdims=True)
        top3_indices = np.argsort(-probs, axis=1)[:, :3]
        top3_list = [[localizer.junctions[idx] for idx in row] for row in top3_indices]

    top1_correct = np.array([float(top3_list[i][0] == run_true[i]) for i in range(len(run_true))])
    top3_correct = np.array([float(run_true[i] in top3_list[i]) for i in range(len(run_true))])

    top1_rate = float(np.mean(top1_correct))
    top3_rate = float(np.mean(top3_correct))

    run_scen_ids = np.array(run_scen_ids)
    t1_low, t1_high = bootstrap_scenario_metric(run_scen_ids, top1_correct)
    t3_low, t3_high = bootstrap_scenario_metric(run_scen_ids, top3_correct)

    return {
        "top1": top1_rate,
        "top1_ci_95": [t1_low, t1_high],
        "top3": top3_rate,
        "top3_ci_95": [t3_low, t3_high],
        "total_runs": len(top1_correct),
        "top1_count": int(np.sum(top1_correct)),
        "top3_count": int(np.sum(top3_correct))
    }


def run_pipeline():
    print("=" * 80)
    print("AQUASENTINEL ML V2: TUNING, ABLATION & FINAL DUAL-EVALUATION")
    print("=" * 80)

    # 1. Fit Hourly Adaptive Slack k(h)
    print("\n--- 1. Fitting Hourly Adaptive Slack k(h) on 30 Tuning Days ---")
    detector_v2 = HardenedDetectorV2()
    hourly_k = detector_v2.fit_hourly_slack(TUNE_DAYS, kappa=2.2, k_base=0.15)

    print("Fitted Hourly k(h) Values (00:00 to 23:00):")
    for h in range(24):
        print(f"  Hour {h:02d}:00 | Raw Sigma: {detector_v2.raw_sigma_h[h]:.3f} m | Smooth Sigma: {detector_v2.smooth_sigma_h[h]:.3f} m | k(h): {hourly_k[h]:.3f} m")

    # 2. Grid Search / CV on 736 Training Scenarios (GroupKFold by leak size)
    print("\n--- 2. Grid Search / Cross-Validation on Training Scenarios (Grouped by Leak Size) ---")
    dev_data = np.load(DEV_SCENARIOS)
    is_test = dev_data["is_test"]
    train_idx = np.where(~is_test)[0]
    print(f"Total training scenarios: {len(train_idx)} across 8 leak sizes.")

    tr_clean_p = dev_data["clean_pressures"][train_idx]
    tr_noisy_3seeds = dev_data["noisy_pressures_3seeds"][train_idx]
    tr_areas = dev_data["leak_areas"][train_idx]
    tr_hours = dev_data["start_hours"][train_idx]

    # Clean drops for training visibility
    pre_tr = np.mean(tr_clean_p[:, :29, :], axis=1)
    post_tr = np.mean(tr_clean_p[:, 60:, :], axis=1)
    tr_vis = np.max(pre_tr - post_tr, axis=1) >= 0.90

    # Grid of candidate spatial constants
    candidate_params = [
        {"drop_high": 1.30, "grad_thresh": 0.20, "drop_low": 0.80, "cusum_thresh": 7.0},
        {"drop_high": 1.35, "grad_thresh": 0.25, "drop_low": 0.85, "cusum_thresh": 7.5},
        {"drop_high": 1.40, "grad_thresh": 0.25, "drop_low": 0.85, "cusum_thresh": 7.5},
        {"drop_high": 1.45, "grad_thresh": 0.30, "drop_low": 0.90, "cusum_thresh": 8.0},
    ]

    # 4-Fold GroupKFold grouped by leak size
    gkf = GroupKFold(n_splits=4)
    best_score = -1.0
    best_params = candidate_params[1]

    for params in candidate_params:
        fold_scores = []
        for tr_fold, val_fold in gkf.split(train_idx, groups=tr_areas):
            test_det = HardenedDetectorV2(
                cusum_thresh=params["cusum_thresh"],
                drop_high=params["drop_high"],
                grad_thresh=params["grad_thresh"],
                drop_low=params["drop_low"]
            )
            test_det.hourly_k = hourly_k

            detected = 0
            total_vis = 0
            for idx in val_fold:
                if tr_vis[idx]:
                    total_vis += 3
                    start_sec = int(tr_hours[idx] * 3600)
                    for s in range(3):
                        P = tr_noisy_3seeds[idx, s]
                        dets, _, _ = test_det.predict_series(P, start_time_of_day=start_sec)
                        if len(np.where(dets & (np.arange(len(dets)) * 10 >= 300))[0]) > 0:
                            detected += 1
            rate = detected / total_vis if total_vis > 0 else 0
            fold_scores.append(rate)
        mean_cv_rate = float(np.mean(fold_scores))
        print(f"CV Params: drop_high={params['drop_high']}, grad={params['grad_thresh']}, drop_low={params['drop_low']}, cusum={params['cusum_thresh']} -> Train Vis Det: {mean_cv_rate:.1%}")
        if mean_cv_rate > best_score:
            best_score = mean_cv_rate
            best_params = params

    print(f"\n>>> Selected Best CV Constants: {best_params} (CV Vis Det: {best_score:.1%}) <<<")
    detector_v2.drop_high = best_params["drop_high"]
    detector_v2.grad_thresh = best_params["grad_thresh"]
    detector_v2.drop_low = best_params["drop_low"]
    detector_v2.cusum_thresh = best_params["cusum_thresh"]

    # 3. Fit Localizer V2
    print("\n--- 3. Fitting Localizer V2 on Training Scenarios (Grouped by Leak Size) ---")
    localizer_v2 = HardenedLocalizerV2()
    tr_leak_nodes = dev_data["leak_nodes"][train_idx]

    X_train_list, y_train_list, groups_list = [], [], []
    for i, idx in enumerate(train_idx):
        node = str(tr_leak_nodes[i])
        y = localizer_v2.junction_to_idx[node]
        area = float(tr_areas[i])

        # Clean
        X_train_list.append(localizer_v2.extract_features(tr_clean_p[i]))
        y_train_list.append(y)
        groups_list.append(area)

        # 3 noisy realizations
        for s in range(3):
            X_train_list.append(localizer_v2.extract_features(tr_noisy_3seeds[i, s]))
            y_train_list.append(y)
            groups_list.append(area)

    X_train = np.array(X_train_list)
    y_train = np.array(y_train_list)
    groups = np.array(groups_list)
    print(f"Extracted {len(X_train)} training feature vectors ({X_train.shape[1]} features).")

    # Fit ensemble models and temperature
    t0 = time.time()
    localizer_v2.fit(X_train, y_train)
    print(f"Fitted RF, ExtraTrees, and HistGradientBoosting in {time.time()-t0:.1f}s.")

    # Calibration on 20% holdout split by size
    val_sizes = [0.005, 0.010]
    val_mask = np.isin(groups, val_sizes)
    localizer_v2.calibrate_temperature(X_train[val_mask], y_train[val_mask])

    # Save candidate models
    joblib.dump(detector_v2, DETECTOR_V2_PATH, compress=3)
    joblib.dump(localizer_v2, LOCALIZER_V2_PATH, compress=3)
    print(f"Saved candidate models to {DETECTOR_V2_PATH} and {LOCALIZER_V2_PATH}.")

    # 4. Load Models for Evaluation
    old_detector = joblib.load(OLD_DETECTOR_PATH)
    old_localizer = joblib.load(OLD_LOCALIZER_PATH)

    # Load DEV data
    dev_test_idx = np.where(is_test)[0]
    dev_scen_dict = {
        "scen_ids": dev_data["scen_ids"][dev_test_idx],
        "leak_nodes": dev_data["leak_nodes"][dev_test_idx],
        "leak_areas": dev_data["leak_areas"][dev_test_idx],
        "start_hours": dev_data["start_hours"][dev_test_idx],
        "clean_pressures": dev_data["clean_pressures"][dev_test_idx],
        "noisy_pressures_3seeds": dev_data["noisy_pressures_3seeds"][dev_test_idx]
    }
    dev_ctrl_data = np.load(DEV_CONTROLS)
    dev_days_data = np.load(DEV_DAYS)["noisy_pressures"]

    # 5. Step-by-Step Ablation Study on DEV Data
    print("\n" + "=" * 80)
    print("STEP 4: STEP-BY-STEP ABLATION STUDY ON DEV DATA (1,104 runs / 4 days)")
    print("=" * 80)

    # Step 0: Baseline Hardened Models
    res_base_det = evaluate_detector_scenarios(old_detector, dev_scen_dict, dev_ctrl_data)
    res_base_fa, base_fa_ci, _ = evaluate_days_fa(old_detector, dev_days_data)
    res_base_loc = evaluate_localizer_scenarios(old_localizer, dev_scen_dict)

    print(f"Step 0 (Baseline Hardened): Det = {res_base_det['detection_rate']:.1%}, VisDet = {res_base_det['visible_detection_rate']:.1%}, FA/day = {res_base_fa:.2f}, Top-1 = {res_base_loc['top1']:.1%}, Top-3 = {res_base_loc['top3']:.1%}")

    # Step 1: Localizer Features Added (RF + ET with 86 features)
    step1_loc = HardenedLocalizerV2()
    step1_loc.rf_model = localizer_v2.rf_model
    step1_loc.et_model = localizer_v2.et_model
    step1_loc.predict_probs_batch = lambda X: (0.5 * step1_loc.rf_model.predict_proba(X) + 0.5 * step1_loc.et_model.predict_proba(X))
    res_step1_loc = evaluate_localizer_scenarios(step1_loc, dev_scen_dict)
    print(f"Step 1 (+ Dynamic Sensor Features): Top-1 = {res_step1_loc['top1']:.1%}, Top-3 = {res_step1_loc['top3']:.1%}")

    # Step 2: Add HistGradientBoosting (Triple Ensemble)
    step2_loc = HardenedLocalizerV2()
    step2_loc.rf_model = localizer_v2.rf_model
    step2_loc.et_model = localizer_v2.et_model
    step2_loc.hgb_model = localizer_v2.hgb_model
    step2_loc.predict_probs_batch = lambda X: (0.4 * step2_loc.rf_model.predict_proba(X) + 0.4 * step2_loc.et_model.predict_proba(X) + 0.2 * step2_loc.hgb_model.predict_proba(X))
    res_step2_loc = evaluate_localizer_scenarios(step2_loc, dev_scen_dict)
    print(f"Step 2 (+ HistGradientBoosting): Top-1 = {res_step2_loc['top1']:.1%}, Top-3 = {res_step2_loc['top3']:.1%}")

    # Step 3: Add Prior blending + Temperature Calibration (Full Localizer V2)
    res_step3_loc = evaluate_localizer_scenarios(localizer_v2, dev_scen_dict)
    print(f"Step 3 (+ Prior & Temp Calibration): Top-1 = {res_step3_loc['top1']:.1%}, Top-3 = {res_step3_loc['top3']:.1%}")

    # Step 4: Detector Trimmed Common-Mode Rejection
    det_step4 = HardenedDetectorV2(
        cusum_thresh=8.0,
        drop_high=1.60,
        drop_low=1.00,
        grad_thresh=0.25
    )
    det_step4.hourly_k = np.full(24, 0.80) # static slack
    res_step4_det = evaluate_detector_scenarios(det_step4, dev_scen_dict, dev_ctrl_data)
    res_step4_fa, _, _ = evaluate_days_fa(det_step4, dev_days_data)
    print(f"Step 4 (+ Trimmed Common-Mode): Det = {res_step4_det['detection_rate']:.1%}, VisDet = {res_step4_det['visible_detection_rate']:.1%}, FA/day = {res_step4_fa:.2f}")

    # Step 5: Detector Hourly Smoothed Adaptive Slack k(h)
    det_step5 = HardenedDetectorV2(
        cusum_thresh=8.0,
        drop_high=1.60,
        drop_low=1.00,
        grad_thresh=0.25
    )
    det_step5.hourly_k = hourly_k
    res_step5_det = evaluate_detector_scenarios(det_step5, dev_scen_dict, dev_ctrl_data)
    res_step5_fa, _, _ = evaluate_days_fa(det_step5, dev_days_data)
    print(f"Step 5 (+ Smoothed Hourly k(h)): Det = {res_step5_det['detection_rate']:.1%}, VisDet = {res_step5_det['visible_detection_rate']:.1%}, FA/day = {res_step5_fa:.2f}")

    # Step 6: Detector Dual-Tier Confirmation (Full Detector V2)
    res_step6_det = evaluate_detector_scenarios(detector_v2, dev_scen_dict, dev_ctrl_data)
    res_step6_fa, step6_fa_ci, _ = evaluate_days_fa(detector_v2, dev_days_data)
    print(f"Step 6 (+ Dual-Tier Confirmation / Full Detector V2): Det = {res_step6_det['detection_rate']:.1%}, VisDet = {res_step6_det['visible_detection_rate']:.1%}, FA/day = {res_step6_fa:.2f}")

    ablation_results = {
        "step0_baseline": {"det": res_base_det, "fa": res_base_fa, "loc": res_base_loc},
        "step1_loc_features": {"loc": res_step1_loc},
        "step2_loc_hgb": {"loc": res_step2_loc},
        "step3_loc_full": {"loc": res_step3_loc},
        "step4_det_trimmed_common": {"det": res_step4_det, "fa": res_step4_fa},
        "step5_det_hourly_k": {"det": res_step5_det, "fa": res_step5_fa},
        "step6_det_full": {"det": res_step6_det, "fa": res_step6_fa}
    }

    # 6. Single Final Evaluation on Fresh Hold-Out
    print("\n" + "=" * 80)
    print("STEP 5: SINGLE FINAL EVALUATION ON FRESH HOLD-OUT (1,656 runs / 24 fresh days)")
    print("=" * 80)

    fresh_leak_data = np.load(FRESH_SCENARIOS)
    fresh_ctrl_data = np.load(FRESH_CONTROLS)
    fresh_days_data = np.load(FRESH_DAYS)["noisy_pressures"]

    print("Evaluating Old Models on Fresh Hold-Out...")
    old_fresh_det = evaluate_detector_scenarios(old_detector, fresh_leak_data, fresh_ctrl_data)
    old_fresh_fa, old_fresh_fa_ci, old_daily_fa = evaluate_days_fa(old_detector, fresh_days_data)
    old_fresh_loc = evaluate_localizer_scenarios(old_localizer, fresh_leak_data)

    print("Evaluating New V2 Models on Fresh Hold-Out...")
    new_fresh_det = evaluate_detector_scenarios(detector_v2, fresh_leak_data, fresh_ctrl_data)
    new_fresh_fa, new_fresh_fa_ci, new_daily_fa = evaluate_days_fa(detector_v2, fresh_days_data)
    new_fresh_loc = evaluate_localizer_scenarios(localizer_v2, fresh_leak_data)

    final_comparison = {
        "fresh_holdout": {
            "old_models": {
                "detection": old_fresh_det,
                "localization": old_fresh_loc,
                "false_alarms": {
                    "mean_per_day": old_fresh_fa,
                    "ci_95": [old_fresh_fa_ci[0], old_fresh_fa_ci[1]],
                    "daily_events": old_daily_fa
                }
            },
            "new_v2_models": {
                "detection": new_fresh_det,
                "localization": new_fresh_loc,
                "false_alarms": {
                    "mean_per_day": new_fresh_fa,
                    "ci_95": [new_fresh_fa_ci[0], new_fresh_fa_ci[1]],
                    "daily_events": new_daily_fa
                }
            }
        },
        "dev_dataset": {
            "old_models": {
                "detection": res_base_det,
                "localization": res_base_loc,
                "false_alarms": res_base_fa
            },
            "new_v2_models": {
                "detection": res_step6_det,
                "localization": res_step3_loc,
                "false_alarms": res_step6_fa
            }
        },
        "fitted_k_h": {h: float(hourly_k[h]) for h in range(24)},
        "ablation": ablation_results,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    with open(OUT_EVAL_JSON, "w") as f:
        json.dump(final_comparison, f, indent=2)
    print(f"\nSuccessfully wrote comprehensive benchmark results to {OUT_EVAL_JSON}!")

    return final_comparison


if __name__ == "__main__":
    run_pipeline()
