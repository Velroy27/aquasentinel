# Pipeline Anomaly, Deduplication and Loss Tests
import asyncio
import pytest

from backend.db import Database
from backend.ml_bridge import MLBridge
from backend.pipeline import Pipeline


def test_leak_produces_single_alert_and_loss_grows(tmp_path):
    async def _run():
        test_db_path = tmp_path / "test_pipe.db"
        db = Database(db_path=test_db_path, enable=True)
        ml_bridge = MLBridge()
        ml_bridge.force_stub_mode()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115", "123"})

        broadcasted_alerts = []
        broadcasted_losses = []

        async def mock_broadcast(msg):
            if msg.get("type") == "alert":
                broadcasted_alerts.append(msg)
            elif msg.get("type") == "loss":
                broadcasted_losses.append(msg)

        pipe.broadcast_func = mock_broadcast

        # Nominal steps
        for t in [0, 10, 20]:
            await pipe.process_step({
                "t": t,
                "readings": [
                    {"t": t, "node_id": "105", "pressure_m": 52.0, "flow_lps": None},
                    {"t": t, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
                ]
            })
        assert len(broadcasted_alerts) == 0

        # Leak steps (pressure drop at node 105)
        for t in [30, 40, 50, 60, 70]:
            await pipe.process_step({
                "t": t,
                "readings": [
                    {"t": t, "node_id": "105", "pressure_m": 39.0, "flow_lps": 5.5},
                    {"t": t, "node_id": "115", "pressure_m": 49.5, "flow_lps": None},
                ]
            })

        # Assert EXACTLY ONE alert despite multiple anomaly windows (deduplication)
        assert len(broadcasted_alerts) == 1
        alert = broadcasted_alerts[0]
        assert alert["type"] == "alert"
        assert alert["severity"] in ("critical", "high", "medium")

        # Assert cumulative loss increases monotonically
        assert len(broadcasted_losses) >= 2
        losses = [l["litres_lost_cum"] for l in broadcasted_losses]
        assert losses[-1] > losses[0]
        for i in range(len(losses) - 1):
            assert losses[i+1] >= losses[i]

        pipe.reset_state(clear_buffers=True)
        db.close()

    asyncio.run(_run())


def test_normal_scenario_produces_zero_alerts(tmp_path):
    async def _run():
        test_db_path = tmp_path / "test_pipe_norm.db"
        db = Database(db_path=test_db_path, enable=True)
        ml_bridge = MLBridge()
        ml_bridge.force_stub_mode()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115", "123"})

        broadcasted_alerts = []

        async def mock_broadcast(msg):
            if msg.get("type") == "alert":
                broadcasted_alerts.append(msg)

        pipe.broadcast_func = mock_broadcast

        # 10 nominal steps
        for step_i in range(10):
            t = step_i * 10
            await pipe.process_step({
                "t": t,
                "readings": [
                    {"t": t, "node_id": "105", "pressure_m": 51.5, "flow_lps": None},
                    {"t": t, "node_id": "115", "pressure_m": 50.2, "flow_lps": None},
                ]
            })

        assert len(broadcasted_alerts) == 0
        assert pipe.active_alert_id is None

        pipe.reset_state(clear_buffers=True)
        db.close()

    asyncio.run(_run())


def test_ingest_single_reading_groups_by_t(tmp_path):
    """Verifies that ingest_single_reading groups readings by t into one step with multiple nodes."""
    async def _run():
        test_db_path = tmp_path / "test_pipe_ingest_group.db"
        db = Database(db_path=test_db_path, enable=True)
        ml_bridge = MLBridge()
        ml_bridge.force_stub_mode()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115", "123"})

        broadcasted = []
        async def mock_broadcast(msg):
            broadcasted.append(msg)
        pipe.broadcast_func = mock_broadcast

        # Post single readings for t=0 from 3 distinct nodes
        await pipe.ingest_single_reading({"t": 0, "node_id": "105", "pressure_m": 50.0, "flow_lps": None})
        await pipe.ingest_single_reading({"t": 0, "node_id": "115", "pressure_m": 51.0, "flow_lps": None})
        await pipe.ingest_single_reading({"t": 0, "node_id": "123", "pressure_m": 49.0, "flow_lps": None})

        # All 3 valid nodes arrived for t=0, so it should be flushed as ONE step
        assert len(pipe.recent_steps) == 1
        step0 = pipe.recent_steps[0]
        assert step0["t"] == 0
        assert len(step0["readings"]) == 3
        node_ids = {r["node_id"] for r in step0["readings"]}
        assert node_ids == {"105", "115", "123"}

        # Now post reading with advancing timestamp t=10
        await pipe.ingest_single_reading({"t": 10, "node_id": "105", "pressure_m": 49.5, "flow_lps": None})
        # Advancing t=20 adds the third step
        await pipe.ingest_single_reading({"t": 20, "node_id": "105", "pressure_m": 49.0, "flow_lps": None})
        assert len(pipe.recent_steps) == 3
        assert pipe.recent_steps[1]["t"] == 10
        assert pipe.recent_steps[2]["t"] == 20

        pipe.reset_state(clear_buffers=True)
        db.close()

    asyncio.run(_run())


def test_sensor_fault_broadcasts_fault_and_no_leak_alert(tmp_path):
    """Verifies that sensor_fault produces a deduplicated fault message and suppresses leak alert/localization/loss."""
    async def _run():
        test_db_path = tmp_path / "test_pipe_fault.db"
        db = Database(db_path=test_db_path, enable=True)
        ml_bridge = MLBridge()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115"})

        broadcasted = []
        async def mock_broadcast(msg):
            broadcasted.append(msg)
        pipe.broadcast_func = mock_broadcast

        # Mock detector returning sensor_fault
        async def mock_detect(window):
            return {"anomaly": False, "score": 0.15, "sensor_fault": True, "detail": {"sensor": "105", "type": "stuck"}}
        ml_bridge.detect = mock_detect

        # Step 1
        await pipe.process_step({
            "t": 0,
            "readings": [
                {"t": 0, "node_id": "105", "pressure_m": 50.0, "flow_lps": None},
                {"t": 0, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
            ]
        })
        # Step 2
        await pipe.process_step({
            "t": 10,
            "readings": [
                {"t": 10, "node_id": "105", "pressure_m": 50.0, "flow_lps": None},
                {"t": 10, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
            ]
        })
        # Step 3 (deduplication check)
        await pipe.process_step({
            "t": 20,
            "readings": [
                {"t": 20, "node_id": "105", "pressure_m": 50.0, "flow_lps": None},
                {"t": 20, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
            ]
        })

        fault_msgs = [m for m in broadcasted if m.get("type") == "fault"]
        alert_msgs = [m for m in broadcasted if m.get("type") == "alert"]
        loc_msgs = [m for m in broadcasted if m.get("type") == "localization"]
        loss_msgs = [m for m in broadcasted if m.get("type") == "loss"]

        # Exactly ONE deduplicated fault message
        assert len(fault_msgs) == 1
        assert fault_msgs[0]["type"] == "fault"
        assert fault_msgs[0]["t"] == 10
        assert fault_msgs[0]["detail"]["sensor"] == "105"

        # Leak alert, localization, and loss must NOT be created
        assert len(alert_msgs) == 0
        assert len(loc_msgs) == 0
        assert len(loss_msgs) == 0
        assert pipe.active_alert_id is None

        pipe.reset_state(clear_buffers=True)
        db.close()

    asyncio.run(_run())


def test_resolve_clear_windows_stops_loss_ticks(tmp_path):
    """Verifies that an alert resolves after RESOLVE_CLEAR_WINDOWS (12) non-anomaly steps and stops loss ticks."""
    async def _run():
        test_db_path = tmp_path / "test_pipe_resolve.db"
        db = Database(db_path=test_db_path, enable=True)
        ml_bridge = MLBridge()
        pipe = Pipeline(db=db, ml_bridge=ml_bridge, valid_nodes={"105", "115"})

        broadcasted = []
        async def mock_broadcast(msg):
            broadcasted.append(msg)
        pipe.broadcast_func = mock_broadcast

        # Force anomaly for 2 steps
        async def anomaly_detect(window):
            return {"anomaly": True, "score": 0.95, "sensor_fault": False}
        async def normal_detect(window):
            return {"anomaly": False, "score": 0.05, "sensor_fault": False}

        ml_bridge.detect = anomaly_detect

        for t in [0, 10]:
            await pipe.process_step({
                "t": t,
                "readings": [
                    {"t": t, "node_id": "105", "pressure_m": 35.0, "flow_lps": None},
                    {"t": t, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
                ]
            })

        assert pipe.active_alert_id is not None
        active_id = pipe.active_alert_id

        # Switch detector to normal
        ml_bridge.detect = normal_detect

        # Send 12 consecutive clear windows (RESOLVE_CLEAR_WINDOWS = 12)
        for i in range(12):
            t = 20 + i * 10
            await pipe.process_step({
                "t": t,
                "readings": [
                    {"t": t, "node_id": "105", "pressure_m": 50.0, "flow_lps": None},
                    {"t": t, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
                ]
            })

        # Alert should now be resolved!
        assert pipe.active_alert_id is None
        assert pipe.est_leak_lps == 0.0

        # Further steps should emit NO loss ticks
        loss_count_before = len([m for m in broadcasted if m.get("type") == "loss"])
        await pipe.process_step({
            "t": 150,
            "readings": [
                {"t": 150, "node_id": "105", "pressure_m": 50.0, "flow_lps": None},
                {"t": 150, "node_id": "115", "pressure_m": 50.0, "flow_lps": None},
            ]
        })
        loss_count_after = len([m for m in broadcasted if m.get("type") == "loss"])
        assert loss_count_after == loss_count_before

        pipe.reset_state(clear_buffers=True)
        db.close()

    asyncio.run(_run())
