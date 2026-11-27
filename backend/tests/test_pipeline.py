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
