import sys, os
sys.path.insert(0, os.path.abspath('.'))
import numpy as np
import joblib
from ml.detector_v2 import HardenedDetectorV2

det_old = joblib.load('ml/detector_hardened.joblib')
exp_mean = det_old.expected_mean

det2 = HardenedDetectorV2()
hourly_k = det2.fit_hourly_slack('data/no_leak_days_all_tune.npz')

def bootstrap_ci(scen_ids, values, n_boot=2000, seed=42):
    unique = np.unique(scen_ids)
    scen_to_idx = {s: np.where(scen_ids == s)[0] for s in unique}
    n = len(unique)
    rng = np.random.default_rng(seed)
    boot_means = np.zeros(n_boot)
    for b in range(n_boot):
        sample = rng.choice(unique, size=n, replace=True)
        idx_list = []
        for s in sample:
            idx_list.extend(scen_to_idx[s])
        boot_means[b] = np.mean(values[idx_list])
    return float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))

def eval_scenarios(scen_file, is_test_split=True):
    data = np.load(scen_file)
    if is_test_split and 'is_test' in data:
        idx_set = np.where(data['is_test'])[0]
    else:
        idx_set = np.arange(len(data['start_hours']))
        
    scen_ids = data['scen_ids'][idx_set]
    start_hours = data['start_hours'][idx_set]
    noisy_3seeds = data['noisy_pressures_3seeds'][idx_set]
    clean_p = data['clean_pressures'][idx_set]
    
    pre = np.mean(clean_p[:, :29, :], axis=1)
    post = np.mean(clean_p[:, 60:, :], axis=1)
    vis_scen = (np.max(pre - post, axis=1) >= 0.90)
    
    run_scens = []
    run_vis = []
    old_detected = []
    v2_detected = []
    
    for i, idx in enumerate(idx_set):
        s_id = str(scen_ids[i])
        hr = int(start_hours[i])
        start_sec = hr * 3600
        vis = bool(vis_scen[i])
        
        for s in range(3):
            P = noisy_3seeds[i, s]
            T, n_sensors = P.shape
            t_idx = np.array([int((start_sec + t * 10) % 86400 / 10) for t in range(T)])
            exp_p = exp_mean[t_idx]
            
            offset = np.median(P[:25] - exp_p[:25], axis=0)
            exp_p_adj = exp_p + offset
            r_raw = exp_p_adj - P
            
            # 1. Old detector
            r_common_old = np.median(r_raw[:, [0, 1, 2, 3, 5, 6, 7]], axis=1, keepdims=True)
            r_diff_old = r_raw - r_common_old
            r_diff_old[:, 4] = 0.0
            
            S_old = np.zeros((T, n_sensors))
            for t in range(1, T):
                inc = (r_diff_old[t] - 0.8) / 0.3
                S_old[t] = np.maximum(0.0, 0.7 * S_old[t-1] + inc)
            inst_old = (np.max(S_old, axis=1) >= 8.0) & (np.max(r_diff_old, axis=1) >= 1.0)
            conf_old = np.zeros(T, dtype=bool)
            for t in range(T):
                if np.sum(inst_old[max(0, t - 8):t + 1]) >= 3:
                    conf_old[t] = True
            post_old = np.where(conf_old & (np.arange(T) * 10 >= 300))[0]
            fired_old = len(post_old) > 0
            
            # 2. V2 detector
            r_diff_v2 = np.zeros_like(r_raw)
            cands = [0, 1, 2, 3, 5, 6, 7]
            for t in range(T):
                row = r_raw[t]
                sorted_c = sorted(cands, key=lambda s: row[s])
                r_diff_v2[t] = row - np.median(row[sorted_c[:-2]])
            r_diff_v2[:, 4] = 0.0
            
            S_v2 = np.zeros((T, n_sensors))
            for t in range(1, T):
                sec = int(start_sec + t * 10)
                h = int((sec // 3600) % 24)
                inc = (r_diff_v2[t] - hourly_k[h]) / 0.3
                S_v2[t] = np.maximum(0.0, 0.75 * S_v2[t-1] + inc)
            inst_v2 = (np.max(S_v2, axis=1) >= 8.0) & (np.max(r_diff_v2, axis=1) >= 0.85)
            conf_v2 = np.zeros(T, dtype=bool)
            for t in range(T):
                if np.sum(inst_v2[max(0, t - 8):t + 1]) >= 3:
                    conf_v2[t] = True
            post_v2 = np.where(conf_v2 & (np.arange(T) * 10 >= 300))[0]
            fired_v2 = len(post_v2) > 0
            
            run_scens.append(s_id)
            run_vis.append(vis)
            old_detected.append(fired_old)
            v2_detected.append(fired_v2)
            
    run_scens = np.array(run_scens)
    run_vis = np.array(run_vis)
    old_detected = np.array(old_detected, dtype=float)
    v2_detected = np.array(v2_detected, dtype=float)
    
    n_runs = len(old_detected)
    old_rate = float(np.mean(old_detected))
    old_ci = bootstrap_ci(run_scens, old_detected)
    v2_rate = float(np.mean(v2_detected))
    v2_ci = bootstrap_ci(run_scens, v2_detected)
    
    old_vis = float(np.mean(old_detected[run_vis]))
    old_vis_ci = bootstrap_ci(run_scens[run_vis], old_detected[run_vis])
    v2_vis = float(np.mean(v2_detected[run_vis]))
    v2_vis_ci = bootstrap_ci(run_scens[run_vis], v2_detected[run_vis])
    
    return {
        'n_runs': n_runs,
        'old': {'rate': old_rate, 'ci': old_ci, 'vis_rate': old_vis, 'vis_ci': old_vis_ci, 'count': int(np.sum(old_detected))},
        'v2': {'rate': v2_rate, 'ci': v2_ci, 'vis_rate': v2_vis, 'vis_ci': v2_vis_ci, 'count': int(np.sum(v2_detected))}
    }

if __name__ == '__main__':
    print('Evaluating DEV Scenarios...')
    dev_res = eval_scenarios('data/scenarios_hardened.npz', is_test_split=True)
    n_dev = dev_res['n_runs']
    print(f"DEV Scenarios (N={n_dev}):")
    print(f"  Old: {dev_res['old']['count']}/{n_dev} = {dev_res['old']['rate']:.1%} (95% CI: [{dev_res['old']['ci'][0]:.1%}, {dev_res['old']['ci'][1]:.1%}]) | Vis: {dev_res['old']['vis_rate']:.1%}")
    print(f"  V2:  {dev_res['v2']['count']}/{n_dev} = {dev_res['v2']['rate']:.1%} (95% CI: [{dev_res['v2']['ci'][0]:.1%}, {dev_res['v2']['ci'][1]:.1%}]) | Vis: {dev_res['v2']['vis_rate']:.1%}")

    print('\nEvaluating Fresh Scenarios...')
    fresh_res = eval_scenarios('data/experiments/scenarios_fresh_holdout.npz', is_test_split=False)
    n_fresh = fresh_res['n_runs']
    print(f"Fresh Scenarios (N={n_fresh}):")
    print(f"  Old: {fresh_res['old']['count']}/{n_fresh} = {fresh_res['old']['rate']:.1%} (95% CI: [{fresh_res['old']['ci'][0]:.1%}, {fresh_res['old']['ci'][1]:.1%}]) | Vis: {fresh_res['old']['vis_rate']:.1%}")
    print(f"  V2:  {fresh_res['v2']['count']}/{n_fresh} = {fresh_res['v2']['rate']:.1%} (95% CI: [{fresh_res['v2']['ci'][0]:.1%}, {fresh_res['v2']['ci'][1]:.1%}]) | Vis: {fresh_res['v2']['vis_rate']:.1%}")
