"""
AquaSentinel - Frame Exporter for Replay Visualization (/ml/viz/export_frames.py)

Streams each replay scenario (01 to 07) through the real ML pipeline (ml/api.py),
matching the exact sliding window (W_SIZE=45) and start_time_of_day protocol from ml/smoke_test.py.

Extracts:
1. Net3 network topology (junctions, tanks, reservoirs, pipes, pumps) from data/Net3.inp.
2. Per-step sensor pressures, residuals, fault flag, alert status.
3. While alerting: top-3 suspect nodes and estimated leak size (lps).
4. Exports scenario JSON files and bundles them into ml/viz/frames.js (window.FRAMES = {...}).
"""

import os
import sys
import json
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import wntr
import ml.api as api
from ml.detector_hardened import SensorHealthChecker

DATA_DIR = os.path.join(BASE_DIR, "data")
REPLAY_DIR = os.path.join(DATA_DIR, "replay")
CATALOG_FILE = os.path.join(REPLAY_DIR, "scenarios_catalog.json")
VIZ_DIR = os.path.join(BASE_DIR, "ml", "viz")
INP_FILE = os.path.join(DATA_DIR, "Net3.inp")


def extract_network_topology():
    print("Extracting Net3 network topology from WNTR...", flush=True)
    wn = wntr.network.WaterNetworkModel(INP_FILE)

    nodes = {}
    for name, node in wn.nodes.items():
        coords = node.coordinates
        node_type = "junction"
        if name in wn.tank_name_list:
            node_type = "tank"
        elif name in wn.reservoir_name_list:
            node_type = "reservoir"

        nodes[name] = {
            "x": float(coords[0]),
            "y": float(coords[1]),
            "type": node_type,
            "is_sensor": name in api.SENSORS
        }

    links = []
    for name, pipe in wn.pipes():
        links.append({
            "id": name,
            "start": pipe.start_node_name,
            "end": pipe.end_node_name,
            "type": "pipe"
        })

    for name, pump in wn.pumps():
        links.append({
            "id": name,
            "start": pump.start_node_name,
            "end": pump.end_node_name,
            "type": "pump"
        })

    network_data = {
        "nodes": nodes,
        "links": links,
        "sensors": api.SENSORS,
        "bounds": {
            "min_x": min(n["x"] for n in nodes.values()),
            "max_x": max(n["x"] for n in nodes.values()),
            "min_y": min(n["y"] for n in nodes.values()),
            "max_y": max(n["y"] for n in nodes.values()),
        }
    }
    print(f"Extracted {len(nodes)} nodes ({len(api.SENSORS)} sensors) and {len(links)} links.", flush=True)
    return network_data


def process_scenario(item, network_data):
    scen_id = item["id"]
    filename = item["filename"]
    true_node = str(item["leak_node"]) if item["leak_node"] else None
    true_lps = float(item["leak_size_lps"])
    leak_t = item["start_t"]

    print(f"\nProcessing {scen_id} ({filename})...", flush=True)
    file_path = os.path.join(REPLAY_DIR, filename)
    with open(file_path, "r") as f:
        stream_msgs = json.load(f)

    api._load_models()

    W_SIZE = 45  # 450s window
    alert_fired = False
    first_alert_t = None
    sensor_fault_flagged = False

    frames = []

    for step in range(len(stream_msgs)):
        window_slice = stream_msgs[max(0, step - W_SIZE + 1):step + 1]
        det_res = api.detect(window_slice)

        if det_res.get("sensor_fault", False):
            sensor_fault_flagged = True

        curr_t = stream_msgs[step]["t"]
        if det_res["anomaly"] and not alert_fired and curr_t >= leak_t:
            alert_fired = True
            first_alert_t = curr_t

        # Compute sensor pressures and differential residuals
        P, times = api._parse_window(window_slice)
        start_sec = int(times[0]) if len(times) > 0 else 0
        healthy_mask, fault_flag, _ = SensorHealthChecker.inspect(P)
        r_diff, _ = api._detector.compute_residuals(P, start_time_of_day=start_sec, healthy_mask=healthy_mask)

        latest_pressures = [round(float(v), 2) for v in P[-1]]
        latest_residuals = [round(float(v), 2) for v in r_diff[-1]]

        # Localization and size estimation only while alert is active
        top3_nodes = []
        loss_est = None

        if alert_fired:
            loc_res = api.localize(window_slice)
            probs = loc_res.get("probs", {})
            top3_nodes = sorted(probs.keys(), key=lambda k: -probs[k])[:3]
            loss_est = round(float(api.estimate_size(window_slice)), 1)

        frames.append({
            "step": step,
            "t": curr_t,
            "pressures": latest_pressures,
            "residuals": latest_residuals,
            "sensor_fault": bool(det_res.get("sensor_fault", False)),
            "alert": bool(alert_fired),
            "suspects": top3_nodes,
            "loss_estimate_lps": loss_est
        })

    delay_s = (first_alert_t - leak_t) if alert_fired and first_alert_t is not None else None

    scenario_output = {
        "id": scen_id,
        "name": item.get("description", scen_id),
        "filename": filename,
        "category": item.get("category", ""),
        "true_leak_node": true_node,
        "true_flow_lps": true_lps,
        "leak_area_m2": item.get("leak_area_m2", 0.0),
        "start_t": leak_t,
        "duration": item.get("duration", 1200),
        "alert_fired": alert_fired,
        "first_alert_t": first_alert_t,
        "delay_s": delay_s,
        "sensor_fault_flagged": sensor_fault_flagged,
        "frame_count": len(frames),
        "frames": frames
    }

    print(f"  Alert: {alert_fired} (Delay: {delay_s}s), Fault: {sensor_fault_flagged}, Total frames: {len(frames)}")
    return scenario_output


def main():
    os.makedirs(VIZ_DIR, exist_ok=True)
    network_data = extract_network_topology()

    with open(CATALOG_FILE, "r") as f:
        catalog = json.load(f)

    all_scenarios = {}

    for item in catalog:
        scen_data = process_scenario(item, network_data)
        scen_id = item["id"]
        all_scenarios[scen_id] = scen_data

        # Write individual scenario JSON
        json_path = os.path.join(VIZ_DIR, f"{scen_id}.json")
        with open(json_path, "w") as f:
            json.dump(scen_data, f, indent=2)
        print(f"  Wrote {json_path} ({os.path.getsize(json_path):,} bytes)")

    # Bundle into frames.js
    bundle = {
        "network": network_data,
        "scenarios": all_scenarios
    }
    js_path = os.path.join(VIZ_DIR, "frames.js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by export_frames.py for AquaSentinel interactive replay\n")
        f.write("window.FRAMES = ")
        json.dump(bundle, f)
        f.write(";\n")

    print(f"\nSuccessfully bundled all scenarios into {js_path} ({os.path.getsize(js_path):,} bytes)!")


if __name__ == "__main__":
    main()
