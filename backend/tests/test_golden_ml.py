"""
AquaSentinel Golden Test: Verifies Pipeline end-to-end integration against real ML module (ml.api)
for all 7 audited replay scenarios in data/replay/scenarios_catalog.json.
"""
import asyncio
import json
from pathlib import Path
import pytest

from backend.config import REPO_ROOT
from backend.db import Database
from backend.ml_bridge import MLBridge
from backend.pipeline import Pipeline


def test_golden_scenarios_match_direct_ml_detect():
    try:
        from ml.api import detect as real_detect
    except (ImportError, Exception) as exc:
        pytest.skip(f"ml.api could not be imported: {exc}")

    catalog_path = REPO_ROOT / "data" / "replay" / "scenarios_catalog.json"
    if not catalog_path.exists():
        pytest.skip(f"Scenarios catalog not found at {catalog_path}")

    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    assert len(catalog) == 7, f"Expected 7 audited scenarios in catalog, found {len(catalog)}"

    async def _run_golden():
        results = []
        db = Database(enable=False)
        bridge = MLBridge()
        bridge.set_real_mode()

        for item in catalog:
            scen_id = item["id"]
            fn = item["filename"]
            leak_node = item.get("leak_node")
            category = item.get("category", "")
            is_sensor_fault = (category == "sensor_fault" or leak_node is None)

            scenario_file = REPO_ROOT / "data" / "replay" / fn
            assert scenario_file.exists(), f"Scenario file not found: {scenario_file}"

            with open(scenario_file, "r", encoding="utf-8") as sf:
                stream_msgs = json.load(sf)

            # 1. Pipeline execution with real ML
            pipeline_alerts = []
            pipeline_faults = []

            async def broadcast(msg):
                msg_type = msg.get("type")
                if msg_type == "alert":
                    pipeline_alerts.append(msg)
                elif msg_type == "fault":
                    pipeline_faults.append(msg)

            pipeline = Pipeline(db=db, ml_bridge=bridge, broadcast_func=broadcast)
            pipeline.reset_state(clear_buffers=True)

            for step in stream_msgs:
                await pipeline.process_step(step, run_id=f"golden_{scen_id}")

            pipe_first_alert_t = pipeline_alerts[0]["t"] if pipeline_alerts else None

            # 2. Direct sliding window detect(window) on last 45 raw messages
            W_SIZE = 45
            direct_first_anomaly_t = None
            direct_first_fault_t = None

            for step_idx in range(len(stream_msgs)):
                window_slice = stream_msgs[max(0, step_idx - W_SIZE + 1):step_idx + 1]
                det_res = real_detect(window_slice)
                curr_t = stream_msgs[step_idx]["t"]

                if det_res.get("sensor_fault", False) and direct_first_fault_t is None:
                    direct_first_fault_t = curr_t

                if det_res.get("anomaly", False) and not det_res.get("sensor_fault", False) and direct_first_anomaly_t is None:
                    direct_first_anomaly_t = curr_t

            # 3. Sensor fault scenarios must produce ZERO leak alerts
            if is_sensor_fault:
                assert len(pipeline_alerts) == 0, (
                    f"Scenario {scen_id} is a sensor-fault scenario but produced {len(pipeline_alerts)} leak alerts: {pipeline_alerts}"
                )
                assert pipe_first_alert_t is None, f"Scenario {scen_id} leak alert t must be None"
                assert len(pipeline_faults) > 0, f"Scenario {scen_id} expected sensor fault broadcast, got none"

            # 4. First alert t must match direct detect anomaly t exactly
            assert pipe_first_alert_t == direct_first_anomaly_t, (
                f"Mismatch in scenario {scen_id} ({fn}): "
                f"Pipeline first alert t={pipe_first_alert_t} vs Direct detect t={direct_first_anomaly_t}"
            )

            results.append({
                "id": scen_id,
                "filename": fn,
                "category": category,
                "pipeline_alert_t": pipe_first_alert_t,
                "direct_detect_t": direct_first_anomaly_t,
                "alerts_count": len(pipeline_alerts),
                "faults_count": len(pipeline_faults),
                "matched": (pipe_first_alert_t == direct_first_anomaly_t)
            })

        return results

    results = asyncio.run(_run_golden())
    for r in results:
        print(f"Golden match: {r['id']} ({r['filename']}) -> alert_t={r['pipeline_alert_t']}, direct_t={r['direct_detect_t']}, matched={r['matched']}")