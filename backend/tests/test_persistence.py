# Persistence and DB Resiliency Tests
import asyncio
import time
import pytest
from backend.db import Database
from backend.ml_bridge import MLBridge
from backend.pipeline import Pipeline


def test_persistence_across_restarts(tmp_path):
    db_file = tmp_path / "persistent_test.db"

    # 1. Instance A writes run, alert, loc, loss
    db_a = Database(db_path=db_file, enable=True)
    run_id = "RUN-TEST-001"
    alert_id = "ALT-TEST-999"

    db_a.enqueue("INSERT_RUN", {
        "id": run_id,
        "scenario_id": "scenario_105_leak",
        "speed": 5.0,
        "source": "replay"
    })
    db_a.enqueue("INSERT_ALERT", {
        "id": alert_id,
        "run_id": run_id,
        "t": 60,
        "severity": "high",
        "score": 0.78,
        "detector": "statistical",
        "status": "active"
    })
    db_a.enqueue("INSERT_LOCALIZATION", {
        "alert_id": alert_id,
        "top3": [{"node_id": "105", "prob": 0.82}],
        "probs": {"105": 0.82, "115": 0.18}
    })
    db_a.enqueue("INSERT_LOSS", {
        "alert_id": alert_id,
        "t": 70,
        "est_leak_lps": 5.5,
        "litres_lost_cum": 55.0
    })

    # Close instance A (flushes queue)
    db_a.close()

    # 2. Instance B connects to the same file
    db_b = Database(db_path=db_file, enable=True)
    runs = db_b.get_runs()
    assert len(runs) >= 1
    assert any(r["id"] == run_id for r in runs)

    alerts = db_b.get_alerts(run_id=run_id)
    assert len(alerts) >= 1
    # On startup, previously uncompleted active alerts are marked interrupted
    assert alerts[0]["status"] == "interrupted"

    detail = db_b.get_alert_detail(alert_id)
    assert detail is not None
    assert detail["id"] == alert_id
    assert detail["localization"] is not None
    assert detail["localization"]["top3"][0]["node_id"] == "105"
    assert len(detail["loss_timeline"]) >= 1
    assert detail["loss_timeline"][0]["litres_lost_cum"] == 55.0

    db_b.close()


def test_failing_db_write_does_not_break_pipeline(tmp_path):
    async def _run():
        db_file = tmp_path / "broken.db"
        # Create db instance and then simulate failure or close it
        db = Database(db_path=db_file, enable=True)
        # Point to closed / disabled state
        db.close()

        ml_bridge = MLBridge()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115"})

        # Feed step into pipeline: it should catch any DB error gracefully and continue
        await pipe.process_step({
            "t": 10,
            "readings": [
                {"t": 10, "node_id": "105", "pressure_m": 50.0, "flow_lps": None}
            ]
        })

        assert pipe.latest_t == 10

    asyncio.run(_run())
