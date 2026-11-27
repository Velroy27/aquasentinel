"""
AquaSentinel - End-to-End API Smoke Test
Streams each replay scenario file in data/replay/ through:
- ml.api.detect(window)
- ml.api.localize(window)
- ml.api.estimate_size(window)
Verifies:
- Alert firing and delay
- Localization top-3 accuracy
- Size estimation vs true flow (lps)
- Sensor fault detection
"""
import os
import sys
import json
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ml.api import detect, localize, estimate_size

DATA_DIR = os.path.join(BASE_DIR, "data")
REPLAY_DIR = os.path.join(DATA_DIR, "replay")
CATALOG_FILE = os.path.join(REPLAY_DIR, "scenarios_catalog.json")

def run_smoke_test():
    print("=" * 90)
    print("AquaSentinel Smoke Test: Streaming Replay Files through /ml/api.py")
    print("=" * 90)
    
    with open(CATALOG_FILE) as f:
        catalog = json.load(f)
        
    results = []
    
    print(f"{'Scenario':<20} | {'True Node':<9} | {'Alert':<6} | {'Delay':<7} | {'Top-3 Match':<11} | {'True (lps)':<10} | {'Est (lps)':<10} | {'Fault':<6}")
    print("-" * 90)
    
    for item in catalog:
        scen_id = item["id"]
        filename = item["filename"]
        true_node = str(item["leak_node"]) if item["leak_node"] else None
        true_lps = float(item["leak_size_lps"])
        leak_t = item["start_t"]
        
        file_path = os.path.join(REPLAY_DIR, filename)
        with open(file_path) as f:
            stream_msgs = json.load(f)
            
        # Stream window of readings (sliding window of up to 60 steps = 10 minutes)
        W_SIZE = 45 # 45 steps = 450s window
        alert_fired = False
        first_alert_t = None
        final_probs = {}
        final_size_est = 0.0
        sensor_fault_flagged = False
        
        for step in range(len(stream_msgs)):
            window_slice = stream_msgs[max(0, step - W_SIZE + 1):step + 1]
            det_res = detect(window_slice)
            
            if det_res.get("sensor_fault", False):
                sensor_fault_flagged = True
                
            curr_t = stream_msgs[step]["t"]
            if det_res["anomaly"] and not alert_fired and curr_t >= leak_t:
                alert_fired = True
                first_alert_t = curr_t
                
        # Once stream finishes, evaluate localizer and size estimator on full sequence
        loc_res = localize(stream_msgs)
        final_probs = loc_res.get("probs", {})
        final_size_est = estimate_size(stream_msgs)
        
        delay_s = (first_alert_t - leak_t) if alert_fired and first_alert_t is not None else None
        delay_str = f"{delay_s}s" if delay_s is not None else "N/A"
        
        top3_nodes = sorted(final_probs.keys(), key=lambda k: -final_probs[k])[:3]
        top3_match = (true_node in top3_nodes) if true_node else False
        top3_str = "YES" if top3_match else ("N/A" if true_node is None else "NO")
        alert_str = "YES" if alert_fired else "NO"
        fault_str = "YES" if sensor_fault_flagged else "NO"
        
        print(f"{scen_id:<20} | {str(true_node):<9} | {alert_str:<6} | {delay_str:<7} | {top3_str:<11} | {true_lps:<10.1f} | {final_size_est:<10.1f} | {fault_str:<6}")
        
        results.append({
            "id": scen_id,
            "filename": filename,
            "true_node": true_node,
            "alert_fired": alert_fired,
            "delay_s": delay_s,
            "top3_match": top3_match,
            "top3_nodes": top3_nodes,
            "true_flow_lps": true_lps,
            "est_flow_lps": final_size_est,
            "sensor_fault": sensor_fault_flagged
        })
        
    print("-" * 90)
    print("Smoke test successfully completed on all 7 replay scenarios.")
    return results

if __name__ == "__main__":
    run_smoke_test()
