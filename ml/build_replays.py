"""
AquaSentinel - Replay File Builder
Generates 5 replay scenario files into /data/replay in the contract's reading format at 10s steps,
with the leak starting partway through.
"""
import os
import json
import numpy as np
import wntr

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(BASE_DIR, "data", "Net3.inp")
REPLAY_DIR = os.path.join(BASE_DIR, "data", "replay")
os.makedirs(REPLAY_DIR, exist_ok=True)

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
NOISE_SD = 0.3
STEP = 10

REPLAY_SPECS = [
    {
        "filename": "scenario_01_pipeburst_node164.json",
        "scenario_id": "scenario_01",
        "leak_node": "164",
        "leak_area": 0.010,
        "start_t": 120,
        "duration": 600,
        "pattern_start": 8 * 3600,
        "description": "High-severity pipe burst at sensor junction 164 during morning peak demand"
    },
    {
        "filename": "scenario_02_leak_node15.json",
        "scenario_id": "scenario_02",
        "leak_node": "15",
        "leak_area": 0.0075,
        "start_t": 180,
        "duration": 600,
        "pattern_start": 13 * 3600,
        "description": "Medium test leak at sensor junction 15 during midday demand"
    },
    {
        "filename": "scenario_03_small_node20.json",
        "scenario_id": "scenario_03",
        "leak_node": "20",
        "leak_area": 0.005,
        "start_t": 240,
        "duration": 600,
        "pattern_start": 19 * 3600,
        "description": "Small leak at non-sensor junction 20 near nodes 10 and 15 during evening peak"
    },
    {
        "filename": "scenario_04_burst_node60.json",
        "scenario_id": "scenario_04",
        "leak_node": "60",
        "leak_area": 0.010,
        "start_t": 150,
        "duration": 600,
        "pattern_start": 2 * 3600,
        "description": "Sudden nocturnal pipe burst at junction 60 during low-demand night hours"
    },
    {
        "filename": "scenario_05_unseen_node184.json",
        "scenario_id": "scenario_05",
        "leak_node": "184",
        "leak_area": 0.0075,
        "start_t": 200,
        "duration": 600,
        "pattern_start": 9 * 3600,
        "description": "Held-out test leak size (0.0075 m2) at sensor junction 184"
    }
]

def build_replays():
    generated_files = []
    scenarios_catalog = []
    
    for idx, spec in enumerate(REPLAY_SPECS):
        rng = np.random.default_rng(1000 + idx)
        wn = wntr.network.WaterNetworkModel(NET_FILE)
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.time.duration = spec["duration"]
        wn.options.time.hydraulic_timestep = STEP
        wn.options.time.report_timestep = STEP
        wn.options.time.pattern_start = spec["pattern_start"]
        
        wn.get_node(spec["leak_node"]).add_leak(wn, area=spec["leak_area"], start_time=spec["start_t"])
        sim = wntr.sim.WNTRSimulator(wn)
        res = sim.run_sim()
        
        p_df = res.node["pressure"][SENSORS]
        times = p_df.index.values.astype(int)
        
        # Calculate leak flow
        leak_lps = 0.0
        if "leak_demand" in res.node:
            leak_lps = float(res.node["leak_demand"][spec["leak_node"]].iloc[-1] * 1000.0)
        if leak_lps <= 0.0:
            p_leak = max(0.0, float(res.node["pressure"][spec["leak_node"]].iloc[-1]))
            leak_lps = float(0.75 * spec["leak_area"] * np.sqrt(2 * 9.81 * p_leak) * 1000.0)
            
        stream_messages = []
        for t_step in times:
            t_int = int(t_step)
            readings = []
            for s_id in SENSORS:
                clean_p = float(p_df.loc[t_step, s_id])
                noisy_p = clean_p + float(rng.normal(0, NOISE_SD))
                readings.append({
                    "t": t_int,
                    "node_id": str(s_id),
                    "pressure_m": round(noisy_p, 2),
                    "flow_lps": None
                })
            stream_messages.append({
                "type": "readings",
                "t": t_int,
                "readings": readings
            })
            
        filepath = os.path.join(REPLAY_DIR, spec["filename"])
        with open(filepath, "w") as f:
            json.dump(stream_messages, f, indent=2)
            
        generated_files.append(spec["filename"])
        scenarios_catalog.append({
            "id": spec["scenario_id"],
            "filename": spec["filename"],
            "leak_node": spec["leak_node"],
            "leak_size_lps": round(leak_lps, 2),
            "leak_area_m2": spec["leak_area"],
            "start_t": spec["start_t"],
            "duration": spec["duration"],
            "description": spec["description"]
        })
        print(f"Generated replay file: {spec['filename']} ({len(stream_messages)} timesteps, leak flow {leak_lps:.1f} lps)")
        
    catalog_path = os.path.join(REPLAY_DIR, "scenarios_catalog.json")
    with open(catalog_path, "w") as f:
        json.dump(scenarios_catalog, f, indent=2)
        
    return generated_files

if __name__ == "__main__":
    build_replays()
