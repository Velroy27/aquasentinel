# ML Integration, Type Coercion, Timeout, and Warm-Up Tests
import asyncio
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, ml_bridge, pipeline, replay_engine
import backend.main as main_mod
import backend.ml_bridge as bridge_mod


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_numpy_return_types_accepted():
    async def _run():
        bridge = bridge_mod.MLBridge()

        # Mock ML returning numpy types
        orig_detect = bridge_mod._real_detect
        orig_localize = bridge_mod._real_localize
        orig_estimate_size = bridge_mod._real_estimate_size
        bridge.forced_stub = False

        bridge_mod._real_detect = lambda w: {
            "anomaly": np.bool_(True),
            "score": np.float32(0.88),
            "sensor_fault": np.bool_(False)
        }
        bridge_mod._real_localize = lambda w: {
            "probs": {
                "105": np.float64(0.75),
                "115": np.float32(0.25)
            }
        }
        bridge_mod._real_estimate_size = lambda w: np.float64(14.8)

        det = await bridge.detect([{"pressure_m": 40.0}])
        assert isinstance(det["anomaly"], bool)
        assert not isinstance(det["anomaly"], np.bool_)
        assert isinstance(det["score"], float)
        assert not isinstance(det["score"], np.float32)

        loc = await bridge.localize([{"pressure_m": 40.0}], valid_nodes={"105", "115"})
        for item in loc["top3"]:
            assert isinstance(item["prob"], float)
            assert not isinstance(item["prob"], np.floating)
        for k, v in loc["probs"].items():
            assert isinstance(v, float)
            assert not isinstance(v, np.floating)

        size = await bridge.estimate_size([{"pressure_m": 40.0}])
        assert isinstance(size, float)
        assert not isinstance(size, np.floating)
        assert size == 14.8

        bridge_mod._real_detect = orig_detect
        bridge_mod._real_localize = orig_localize
        bridge_mod._real_estimate_size = orig_estimate_size

    asyncio.run(_run())


def test_timeout_fallback_sets_stub_flag(client):
    async def _run():
        # Setup real mode with a hanging detect function
        orig_detect = bridge_mod._real_detect
        orig_timeout = bridge_mod.ML_TIMEOUT_S
        bridge_mod.ML_TIMEOUT_S = 0.1 # Fast timeout for test

        def hanging_detect(w):
            import time
            time.sleep(0.5)
            return {"anomaly": True, "score": 0.9}

        bridge_mod._real_detect = hanging_detect
        ml_bridge.forced_stub = False
        ml_bridge.ml_mode = "real"
        ml_bridge.stub_calls = 0
        ml_bridge.last_stub_reason = None
        pipeline.reset_state(clear_buffers=True)

        res = await ml_bridge.detect([{"pressure_m": 35.0}])
        assert ml_bridge.stub_calls > 0
        assert "timed out" in ml_bridge.last_stub_reason.lower()
        assert ml_bridge.ml_mode == "degraded"

        # Verify pipeline records run_had_stub and sets "stub": True on messages
        with client.websocket_connect("/ws/stream") as ws:
            # Trigger anomaly via ingest with low pressure
            await pipeline.ingest_single_reading({"t": 0, "node_id": "105", "pressure_m": 35.0, "flow_lps": None})
            await pipeline.ingest_single_reading({"t": 10, "node_id": "105", "pressure_m": 35.0, "flow_lps": None})
            await pipeline.ingest_single_reading({"t": 20, "node_id": "105", "pressure_m": 35.0, "flow_lps": None})

            # Read WS messages
            msg_types = {}
            for _ in range(6):
                m = ws.receive_json()
                t = m.get("type")
                if t in ("alert", "localization", "loss"):
                    msg_types[t] = m

            assert "alert" in msg_types
            assert msg_types["alert"].get("stub") is True
            assert "localization" in msg_types
            assert msg_types["localization"].get("stub") is True
            assert "loss" in msg_types
            assert msg_types["loss"].get("stub") is True

        bridge_mod._real_detect = orig_detect
        bridge_mod.ML_TIMEOUT_S = orig_timeout

    asyncio.run(_run())


def test_warmup_failure_does_not_crash():
    async def _run():
        bridge = bridge_mod.MLBridge()
        orig_detect = bridge_mod._real_detect
        bridge.forced_stub = False
        bridge.ml_mode = "real"

        def broken_detect(w):
            raise RuntimeError("Model file corrupt!")

        bridge_mod._real_detect = broken_detect

        # Warmup should return False, log ERROR, and transition to stub mode without raising
        ok = await bridge.warmup([{"pressure_m": 50.0}])
        assert ok is False
        assert bridge.ml_mode == "stub"
        assert bridge.forced_stub is True
        assert "exception" in bridge.last_stub_reason.lower()

        bridge_mod._real_detect = orig_detect

    asyncio.run(_run())


def test_503_during_warmup(client):
    main_mod.is_warming_up = True
    try:
        resp_start = client.post("/api/replay/start", json={"scenario_id": "scenario_normal", "speed": 1})
        assert resp_start.status_code == 503
        assert resp_start.json() == {"error": "ML warming up"}

        resp_ingest = client.post("/api/ingest", json={"t": 0, "node_id": "105", "pressure_m": 50.0})
        assert resp_ingest.status_code == 503
        assert resp_ingest.json() == {"error": "ML warming up"}
    finally:
        main_mod.is_warming_up = False
