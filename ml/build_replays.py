"""
AquaSentinel - Build Demo Replay Scenarios
Constructs 7 diverse replay scenarios in data/replay/:
1. Two sensor-junction leaks (Node 123, Node 10)
2. Three non-sensor leaks where detection and top-3 both succeed (Node 101 nocturnal, Node 103 morning, Node 101 midday)
3. One small leak honestly missed (Node 103, area 0.002 m2)
4. One sensor-fault case (Sensor 208 stuck-at reading, flagged as sensor fault without leak alert)
Updates data/replay/scenarios_catalog.json.
"""
import os
import sys
import json
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPLAY_DIR = os.path.join(DATA_DIR, "replay")
os.makedirs(REPLAY_DIR, exist_ok=True)

SENSORS = ["208", "10", "15", "184", "601", "164", "60", "123"]
SCENARIOS_FILE = os.path.join(DATA_DIR, "scenarios_hardened.npz")
DISTURB_FILE = os.path.join(DATA_DIR, "disturbances.npz")
CATALOG_FILE = os.path.join(REPLAY_DIR, "scenarios_catalog.json")

def format_replay_json(P_noisy, leak_start_sec=300):
    """
    Format (T, 8) pressure matrix into list of WebSocket reading stream messages.
    """
    T, n_sensors = P_noisy.shape
    messages = []
    for t_step in range(T):
        t_sec = t_step * 10
        readings = []
        for s_idx, s_id in enumerate(SENSORS):
            readings.append({
                "t": t_sec,
                "node_id": s_id,
                "pressure_m": round(float(P_noisy[t_step, s_idx]), 2),
                "flow_lps": None
            })
        messages.append({
            "type": "readings",
            "t": t_sec,
            "readings": readings
        })
    return messages

def build_all_replays():
    scen_data = np.load(SCENARIOS_FILE)
    noisy_3seeds = scen_data["noisy_pressures_3seeds"]
    leak_flows = scen_data["leak_flows"]
    
    disturb_data = np.load(DISTURB_FILE)
    disturb_noisy = disturb_data["noisy_pressures"]
    
    catalog = []
    
    # 1. Sensor-junction leak: Node 123 (area 0.0065, flow 146.3 lps, idx 52)
    s1_P = noisy_3seeds[52, 0]
    s1_flow = float(leak_flows[52])
    with open(os.path.join(REPLAY_DIR, "scenario_01_sensor_node123.json"), "w") as f:
        json.dump(format_replay_json(s1_P), f, indent=2)
    catalog.append({
        "id": "scenario_01",
        "filename": "scenario_01_sensor_node123.json",
        "leak_node": "123",
        "leak_size_lps": round(s1_flow, 1),
        "leak_area_m2": 0.0065,
        "start_t": 300,
        "duration": 1200,
        "category": "sensor_junction",
        "description": "High-severity leak at sensor junction 123 during night low-demand (2:00)"
    })
    
    # 2. Sensor-junction leak: Node 10 (area 0.0075, flow 17.1 lps, idx 22)
    s2_P = noisy_3seeds[22, 0]
    s2_flow = float(leak_flows[22])
    with open(os.path.join(REPLAY_DIR, "scenario_02_sensor_node10.json"), "w") as f:
        json.dump(format_replay_json(s2_P), f, indent=2)
    catalog.append({
        "id": "scenario_02",
        "filename": "scenario_02_sensor_node10.json",
        "leak_node": "10",
        "leak_size_lps": round(s2_flow, 1),
        "leak_area_m2": 0.0075,
        "start_t": 300,
        "duration": 1200,
        "category": "sensor_junction",
        "description": "Medium test leak at sensor junction 10 during morning peak demand (6:00)"
    })
    
    # 3. Non-sensor leak (success): Node 101 (area 0.0065, flow 107.7 lps, idx 1)
    s3_P = noisy_3seeds[1, 0]
    s3_flow = float(leak_flows[1])
    with open(os.path.join(REPLAY_DIR, "scenario_03_nonsensor_node101.json"), "w") as f:
        json.dump(format_replay_json(s3_P), f, indent=2)
    catalog.append({
        "id": "scenario_03",
        "filename": "scenario_03_nonsensor_node101.json",
        "leak_node": "101",
        "leak_size_lps": round(s3_flow, 1),
        "leak_area_m2": 0.0065,
        "start_t": 300,
        "duration": 1200,
        "category": "non_sensor_success",
        "description": "Pipe burst at non-sensor junction 101 (correctly detected and localized in top-1)"
    })
    
    # 4. Non-sensor leak (success): Node 103 (area 0.0065, flow 112.5 lps, idx 4)
    s4_P = noisy_3seeds[4, 0]
    s4_flow = float(leak_flows[4])
    with open(os.path.join(REPLAY_DIR, "scenario_04_nonsensor_node103.json"), "w") as f:
        json.dump(format_replay_json(s4_P), f, indent=2)
    catalog.append({
        "id": "scenario_04",
        "filename": "scenario_04_nonsensor_node103.json",
        "leak_node": "103",
        "leak_size_lps": round(s4_flow, 1),
        "leak_area_m2": 0.0065,
        "start_t": 300,
        "duration": 1200,
        "category": "non_sensor_success",
        "description": "Burst at non-sensor junction 103 during morning peak (detected and localized in top-3)"
    })
    
    # 5. Non-sensor leak (success): Node 101 held-out (area 0.0075, flow 127.9 lps, idx 3)
    s5_P = noisy_3seeds[3, 0]
    s5_flow = float(leak_flows[3])
    with open(os.path.join(REPLAY_DIR, "scenario_05_nonsensor_node101_midday.json"), "w") as f:
        json.dump(format_replay_json(s5_P), f, indent=2)
    catalog.append({
        "id": "scenario_05",
        "filename": "scenario_05_nonsensor_node101_midday.json",
        "leak_node": "101",
        "leak_size_lps": round(s5_flow, 1),
        "leak_area_m2": 0.0075,
        "start_t": 300,
        "duration": 1200,
        "category": "non_sensor_success",
        "description": "Held-out test leak (0.0075 m2) at non-sensor junction 101 during afternoon peak"
    })
    
    # 6. Small leak honestly missed: Node 119 (area 0.002, idx 448)
    s6_P = noisy_3seeds[448, 0]
    s6_flow = float(leak_flows[448])
    with open(os.path.join(REPLAY_DIR, "scenario_06_small_missed_node119.json"), "w") as f:
        json.dump(format_replay_json(s6_P), f, indent=2)
    catalog.append({
        "id": "scenario_06",
        "filename": "scenario_06_small_missed_node119.json",
        "leak_node": "119",
        "leak_size_lps": round(s6_flow, 1),
        "leak_area_m2": 0.002,
        "start_t": 300,
        "duration": 1200,
        "category": "small_leak_missed",
        "description": "Small leak (0.002 m2) at Node 119: honestly undetected (max clean drop 0.19m under sensor noise floor)"
    })
    
    # 7. Sensor fault: Sensor 208 stuck-at fixed value (disturbances idx 30)
    s7_P = disturb_noisy[30]
    with open(os.path.join(REPLAY_DIR, "scenario_07_sensor_fault_stuck.json"), "w") as f:
        json.dump(format_replay_json(s7_P), f, indent=2)
    catalog.append({
        "id": "scenario_07",
        "filename": "scenario_07_sensor_fault_stuck.json",
        "leak_node": None,
        "leak_size_lps": 0.0,
        "leak_area_m2": 0.0,
        "start_t": 300,
        "duration": 1200,
        "category": "sensor_fault",
        "description": "Sensor fault disturbance: Sensor 208 stuck-at reading from t=300s (correctly flagged as sensor fault, no leak alert)"
    })
    
    with open(CATALOG_FILE, "w") as f:
        json.dump(catalog, f, indent=2)
        
    print(f"Successfully generated {len(catalog)} demo replay scenarios in {REPLAY_DIR}:")
    for item in catalog:
        print(f"  [{item['id']}] {item['filename']} -> {item['category']} (Node: {item['leak_node']})")

if __name__ == "__main__":
    build_all_replays()
