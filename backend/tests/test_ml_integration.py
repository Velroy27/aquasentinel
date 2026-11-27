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
        try:
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
        finally:
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


def test_failing_real_detect_produces_no_alert_without_env_flag():
    import os
    async def _run():
        orig_env = os.environ.get("AQUASENTINEL_ALLOW_STUB_ALERTS")
        orig_detect = bridge_mod._real_detect
        try:
            os.environ["AQUASENTINEL_ALLOW_STUB_ALERTS"] = "0"
            bridge = bridge_mod.MLBridge()
            bridge.forced_stub = False
            bridge.ml_mode = "real"

            def broken_detect(w):
                raise RuntimeError("ML inference crashed")

            bridge_mod._real_detect = broken_detect

            # detect() returns anomaly=False, score=0.0, sensor_fault=False
            res = await bridge.detect([{"pressure_m": 30.0}])
            assert res == {"anomaly": False, "score": 0.0, "sensor_fault": False}
            assert bridge.stub_calls == 1
            assert bridge.stub_calls_run == 1
            assert bridge.ml_mode == "degraded"
            assert "stub" in bridge.last_stub_reason.lower() or "exception" in bridge.last_stub_reason.lower()

            # In pipeline: with failing detect and no env flag, no alerts produced even on severe pressure drop
            alerts = []
            async def on_broadcast(msg):
                if msg.get("type") == "alert":
                    alerts.append(msg)

            pipe = pipeline.__class__(db=pipeline.db, ml_bridge=bridge, broadcast_func=on_broadcast)
            pipe.reset_state(clear_buffers=True)

            for step_t in (0, 10, 20):
                await pipe.process_step({
                    "t": step_t,
                    "readings": [{"t": step_t, "node_id": "105", "pressure_m": 25.0, "flow_lps": None}]
                })

            assert len(alerts) == 0
            assert pipe.active_alert_id is None
        finally:
            bridge_mod._real_detect = orig_detect
            if orig_env is not None:
                os.environ["AQUASENTINEL_ALLOW_STUB_ALERTS"] = orig_env
            else:
                os.environ.pop("AQUASENTINEL_ALLOW_STUB_ALERTS", None)

    asyncio.run(_run())


def test_env_flag_preserves_old_stub_alert_behaviour():
    import os
    async def _run():
        orig_env = os.environ.get("AQUASENTINEL_ALLOW_STUB_ALERTS")
        orig_detect = bridge_mod._real_detect
        try:
            os.environ["AQUASENTINEL_ALLOW_STUB_ALERTS"] = "1"
            bridge = bridge_mod.MLBridge()
            bridge.forced_stub = False
            bridge.ml_mode = "real"

            def broken_detect(w):
                raise RuntimeError("ML inference crashed")

            bridge_mod._real_detect = broken_detect

            # With env flag set to 1, stub_detect runs old anomaly logic
            res = await bridge.detect([{"pressure_m": 35.0}])
            assert res["anomaly"] is True
            assert res["score"] > 0.5
            assert bridge.stub_calls == 1
        finally:
            bridge_mod._real_detect = orig_detect
            if orig_env is not None:
                os.environ["AQUASENTINEL_ALLOW_STUB_ALERTS"] = orig_env
            else:
                os.environ.pop("AQUASENTINEL_ALLOW_STUB_ALERTS", None)

    asyncio.run(_run())


def test_mode_recovery_after_20_consecutive_successful_calls():
    async def _run():
        bridge = bridge_mod.MLBridge()
        bridge.forced_stub = False
        bridge.ml_mode = "real"

        orig_detect = bridge_mod._real_detect

        def successful_detect(w):
            return {"anomaly": False, "score": 0.1, "sensor_fault": False}

        bridge_mod._real_detect = successful_detect

        # Cause a stub to degrade mode
        bridge._record_stub("simulated failure")
        assert bridge.ml_mode == "degraded"
        assert bridge.consecutive_real_calls == 0

        # Perform 19 successful calls: should remain degraded
        for _ in range(19):
            res = await bridge.detect([{"pressure_m": 50.0}])
            assert res["anomaly"] is False
            assert bridge.ml_mode == "degraded"

        assert bridge.consecutive_real_calls == 19

        # 20th successful call: must recover to "real"
        res = await bridge.detect([{"pressure_m": 50.0}])
        assert bridge.consecutive_real_calls == 20
        assert bridge.ml_mode == "real"

        bridge_mod._real_detect = orig_detect

    asyncio.run(_run())


def test_stub_calls_run_health_and_reset(client):
    ml_bridge.stub_calls = 5
    ml_bridge.stub_calls_run = 3

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stub_calls"] == 5
    assert data["stub_calls_run"] == 3

    # Reset state resets stub_calls_run but keeps cumulative stub_calls
    pipeline.reset_state(clear_buffers=True)
    assert ml_bridge.stub_calls_run == 0
    assert ml_bridge.stub_calls == 5

    resp2 = client.get("/health")
    data2 = resp2.json()
    assert data2["stub_calls"] == 5
    assert data2["stub_calls_run"] == 0

