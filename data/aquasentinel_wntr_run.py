"""
AquaSentinel: real WNTR validation run (v2, larger leaks + progress prints).
Needs Net3.inp in the same folder.
Run:  py -3.12 aquasentinel_wntr_run.py
Produces: detection_chart.png and results.json
"""
import json, warnings
import numpy as np
import wntr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier

warnings.filterwarnings("ignore")
NET_FILE = "Net3.inp"
N_SENSORS = 8
NOISE_SD = 0.3                    # metres of pressure noise per reading
TRAIN_AREAS = [0.005, 0.01]       # leak sizes (m^2) for training
TEST_AREA = 0.0075                # unseen leak size for testing
rng = np.random.default_rng(42)


def load(duration=0, step=3600):
    wn = wntr.network.WaterNetworkModel(NET_FILE)
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.time.duration = duration
    wn.options.time.hydraulic_timestep = step
    wn.options.time.report_timestep = step
    return wn


def snapshot(leak_node=None, area=None):
    wn = load()
    if leak_node:
        wn.get_node(leak_node).add_leak(wn, area=area, start_time=0)
    res = wntr.sim.WNTRSimulator(wn).run_sim()
    return res.node["pressure"].iloc[0][wn.junction_name_list]


print("Baseline simulation...")
base = snapshot()
junctions = list(base.index)

# 1. Leak at every junction, several sizes
def drops_for(area):
    rows = []
    for n, j in enumerate(junctions):
        rows.append((base - snapshot(j, area)).values)
        if n % 20 == 0:
            print(f"  leak area {area}: junction {n}/{len(junctions)}")
    return np.array(rows)          # leaks x nodes

D = {a: drops_for(a) for a in TRAIN_AREAS + [TEST_AREA]}
med_drop = float(np.median(D[TEST_AREA].max(axis=1)))
print("median of max drop per leak (m):", round(med_drop, 2))

# 2. Greedy sensor placement: maximise leaks seen (drop > 3x noise)
thr = 3 * NOISE_SD
cover = np.mean([D[a] > thr for a in TRAIN_AREAS], axis=0) > 0.5   # leaks x nodes
chosen, covered = [], np.zeros(len(junctions), bool)
for _ in range(N_SENSORS):
    gain = [(cover[:, k] & ~covered).sum() if k not in chosen else -1
            for k in range(len(junctions))]
    k = int(np.argmax(gain)); chosen.append(k); covered |= cover[:, k]
sensors = [junctions[k] for k in chosen]
coverage = covered.mean()
print("Sensors:", sensors, "coverage:", round(100 * coverage, 1), "%")

# 3. Train localizer on noisy sensor fingerprints
def feats(drop_matrix, copies):
    X, y = [], []
    for i, row in enumerate(drop_matrix):
        for _ in range(copies):
            v = row[chosen] + rng.normal(0, NOISE_SD, len(chosen))
            n = np.linalg.norm(v) + 1e-9
            X.append(np.append(v / n, n)); y.append(i)
    return np.array(X), np.array(y)

print("Training localizer...")
parts = [feats(D[a], 30) for a in TRAIN_AREAS]
Xtr = np.vstack([p[0] for p in parts]); ytr = np.concatenate([p[1] for p in parts])
Xte, yte = feats(D[TEST_AREA], 20)
clf = RandomForestClassifier(n_estimators=300, random_state=0, n_jobs=-1).fit(Xtr, ytr)
proba = clf.predict_proba(Xte)
top = clf.classes_[np.argsort(proba, axis=1)[:, ::-1]]
top1 = np.mean(top[:, 0] == yte)
top3 = np.mean([yte[i] in top[i, :3] for i in range(len(yte))])

# 4. Time-series detection demo (10 s steps, leak at t=60 s)
print("Detection demo...")
STEP, T_LEAK = 10, 60
wn = load(duration=120, step=STEP)
order = np.argsort(-D[TEST_AREA][:, chosen].sum(axis=1))
leak_j = junctions[int(order[len(junctions) // 3])]
wn.get_node(leak_j).add_leak(wn, area=TEST_AREA, start_time=T_LEAK)
res = wntr.sim.WNTRSimulator(wn).run_sim()
P = res.node["pressure"][sensors]
P = P + rng.normal(0, NOISE_SD, P.shape)
t = P.index.values
pre = P[t < T_LEAK].mean()
post = P[t >= T_LEAK + 20].mean()
nearest = (post - pre).idxmin()
s = P[nearest]
detect_t = None
for i in range(6, len(s)):
    w = s.iloc[i - 6:i]
    z = (s.iloc[i] - w.mean()) / (w.std() + 1e-6)
    if t[i] >= T_LEAK and z < -3:
        detect_t = int(t[i]); break

# plot the 3 sensors with the biggest drop
show = list((post - pre).sort_values().index[:3])
fig, ax = plt.subplots(figsize=(6, 3.2))
for c in show:
    ax.plot(t, P[c], label=f"Node {c}")
ax.axvline(T_LEAK, ls=":", c="red")
ax.text(T_LEAK - 2, ax.get_ylim()[1], "leak starts", ha="right", va="top", color="red")
if detect_t:
    ax.axvline(detect_t, c="teal")
    ax.text(detect_t + 2, ax.get_ylim()[0], f"alert t+{detect_t - T_LEAK} s", color="teal", va="bottom")
ax.set_xlabel("time (s)"); ax.set_ylabel("pressure (m)"); ax.legend(frameon=False)
ax.set_title("WNTR Net3: pressure at 3 sensors")
fig.tight_layout(); fig.savefig("detection_chart.png", dpi=200)

out = dict(network="Net3", junctions=len(junctions), sensors=N_SENSORS, sensor_nodes=sensors,
           coverage_pct=round(100 * coverage, 1), noise_sd_m=NOISE_SD,
           test_leak_area_m2=TEST_AREA, median_max_drop_m=round(med_drop, 2),
           top1_pct=round(100 * top1, 1), top3_pct=round(100 * top3, 1),
           detection_delay_s=None if detect_t is None else detect_t - T_LEAK)
json.dump(out, open("results.json", "w"), indent=2)
print(json.dumps(out, indent=2))
