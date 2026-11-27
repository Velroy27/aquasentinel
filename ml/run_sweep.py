"""
AquaSentinel - Sweep across thresholds and evaluate trade-offs
"""
import os
import json
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

b = np.load(os.path.join(DATA_DIR, "expected_baseline.npz"))
exp_mean = b["expected_mean"]

tune = np.load(os.path.join(DATA_DIR, "no_leak_days_tune.npz"))
ev = np.load(os.path.join(DATA_DIR, "no_leak_days_eval.npz"))

all_r_diff = []
for d in range(14):
    r_raw = exp_mean - tune["noisy_pressures"][d]
    r_diff = r_raw - np.median(r_raw, axis=1, keepdims=True)
    all_r_diff.append(r_diff)
std_diff = np.std(all_r_diff, axis=0) + 1e-4

scen = np.load(os.path.join(DATA_DIR, "scenarios.npz"))
is_test = scen["is_test"]
noisy_scen = scen["noisy_pressures"]
clean_scen = scen["clean_pressures"]
times = scen["times"]

with open(os.path.join(DATA_DIR, "manifest.json")) as f:
    mf = json.load(f)

test_idx = np.where(is_test)[0]
train_idx = np.where(~is_test)[0]

vis_test_mask = []
for idx in test_idx:
    drop = np.max(np.mean(clean_scen[idx, :20], axis=0) - np.mean(clean_scen[idx, 35:], axis=0))
    vis_test_mask.append(drop >= 0.9)
vis_test_mask = np.array(vis_test_mask)

print("=" * 80)
print(f"{'H_cusum':>7} | {'k_slack':>7} | {'TrainDet':>8} | {'TuneFA/d':>8} | {'TestDet':>8} | {'VisDet':>8} | {'Delay':>5} | {'UntouchFA/d':>11}")
print("-" * 80)

kz = 2.5
H_list = [6.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]
sweep_records = []

for H in H_list:
    # 1. Tuning FA
    total_tune_fa = 0
    for d in range(14):
        P = tune["noisy_pressures"][d]
        r_raw = exp_mean - P
        r_diff = r_raw - np.median(r_raw, axis=1, keepdims=True)
        z_diff = r_diff / std_diff
        S = np.zeros_like(z_diff)
        for t in range(1, len(z_diff)):
            S[t] = np.maximum(0.0, S[t-1] + z_diff[t] - kz)
        trig = (np.max(S, axis=1) >= H) & (np.max(r_diff, axis=1) >= 0.9)
        conf = np.zeros(len(trig), bool)
        for t in range(len(trig)):
            if np.sum(trig[max(0, t-8):t+1]) >= 3:
                conf[t] = True
        trigs = np.where(conf)[0]
        last = -9999
        dfa = 0
        for i in trigs:
            if i - last >= 90:
                dfa += 1
                last = i
        total_tune_fa += dfa
    tune_fa_d = total_tune_fa / 14.0
    
    # 2. Train Detection
    tr_det = 0
    for idx in train_idx:
        P = noisy_scen[idx]
        pat_hr = mf[idx]["pattern_start_hour"]
        t_sec = np.array([(pat_hr * 3600 + t) % 86400 // 10 for t in times], dtype=int)
        exp_p = exp_mean[t_sec]
        offset = np.median(P[:20] - exp_p[:20], axis=0)
        r_diff = (exp_p + offset - P) - np.median(exp_p + offset - P, axis=1, keepdims=True)
        z_diff = r_diff / std_diff[t_sec]
        S = np.zeros_like(z_diff)
        for t in range(1, len(z_diff)):
            S[t] = np.maximum(0.0, S[t-1] + z_diff[t] - kz)
        trig = (np.max(S, axis=1) >= H) & (np.max(r_diff, axis=1) >= 0.9)
        conf = np.zeros(len(trig), bool)
        for t in range(len(trig)):
            if np.sum(trig[max(0, t-8):t+1]) >= 3:
                conf[t] = True
        if np.any(conf & (times >= 300)):
            tr_det += 1
    tr_rate = tr_det / len(train_idx)
    
    # 3. Test Detection
    te_det = 0
    v_det = 0
    delays = []
    for i, idx in enumerate(test_idx):
        P = noisy_scen[idx]
        pat_hr = mf[idx]["pattern_start_hour"]
        t_sec = np.array([(pat_hr * 3600 + t) % 86400 // 10 for t in times], dtype=int)
        exp_p = exp_mean[t_sec]
        offset = np.median(P[:20] - exp_p[:20], axis=0)
        r_diff = (exp_p + offset - P) - np.median(exp_p + offset - P, axis=1, keepdims=True)
        z_diff = r_diff / std_diff[t_sec]
        S = np.zeros_like(z_diff)
        for t in range(1, len(z_diff)):
            S[t] = np.maximum(0.0, S[t-1] + z_diff[t] - kz)
        trig = (np.max(S, axis=1) >= H) & (np.max(r_diff, axis=1) >= 0.9)
        conf = np.zeros(len(trig), bool)
        for t in range(len(trig)):
            if np.sum(trig[max(0, t-8):t+1]) >= 3:
                conf[t] = True
        post = np.where(conf & (times >= 300))[0]
        if len(post) > 0:
            te_det += 1
            delays.append(times[post[0]] - 300)
            if vis_test_mask[i]:
                v_det += 1
    te_rate = te_det / len(test_idx)
    v_rate = v_det / np.sum(vis_test_mask)
    med_del = np.median(delays) if delays else 0
    
    # 4. Untouched Evaluation Days FA
    total_ev_fa = 0
    for d in range(len(ev["noisy_pressures"])):
        P = ev["noisy_pressures"][d]
        r_raw = exp_mean - P
        r_diff = r_raw - np.median(r_raw, axis=1, keepdims=True)
        z_diff = r_diff / std_diff
        S = np.zeros_like(z_diff)
        for t in range(1, len(z_diff)):
            S[t] = np.maximum(0.0, S[t-1] + z_diff[t] - kz)
        trig = (np.max(S, axis=1) >= H) & (np.max(r_diff, axis=1) >= 0.9)
        conf = np.zeros(len(trig), bool)
        for t in range(len(trig)):
            if np.sum(trig[max(0, t-8):t+1]) >= 3:
                conf[t] = True
        trigs = np.where(conf)[0]
        last = -9999
        dfa = 0
        for i in trigs:
            if i - last >= 90:
                dfa += 1
                last = i
        total_ev_fa += dfa
    ev_fa_d = total_ev_fa / len(ev["noisy_pressures"])
    
    row = {
        "H": H,
        "k_slack": kz,
        "train_det_rate": round(tr_rate, 4),
        "tune_fa_per_day": round(tune_fa_d, 3),
        "test_det_rate": round(te_rate, 4),
        "test_vis_det_rate": round(v_rate, 4),
        "median_delay_s": round(float(med_del), 1),
        "untouched_fa_per_day": round(ev_fa_d, 3)
    }
    sweep_records.append(row)
    print(f"{H:7.1f} | {kz:7.2f} | {tr_rate*100:7.1f}% | {tune_fa_d:8.2f} | {te_rate*100:7.1f}% | {v_rate*100:6.1f}% | {med_del:4.0f}s | {ev_fa_d:11.2f}")

out_path = os.path.join(DATA_DIR, "cusum_sweep_table.json")
with open(out_path, "w") as f:
    json.dump(sweep_records, f, indent=2)
print("=" * 80)
print(f"Sweep table saved to {out_path}")
