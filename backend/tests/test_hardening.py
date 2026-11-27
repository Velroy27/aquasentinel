# Hardening, Fault-Tolerance & Edge-Case Tests
import asyncio
import json
import pytest
from fastapi.testclient import TestClient

from backend.db import Database
from backend.main import app, pipeline, replay_engine, ws_manager
from backend.ml_bridge import MLBridge


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_malformed_payloads_return_4xx_never_500(client):
    # 1. Non-numeric flow_lps -> 422
    resp1 = client.post("/api/ingest", json={"t": 10, "node_id": "105", "pressure_m": 50.0, "flow_lps": "invalid_flow"})
    assert resp1.status_code == 422
    assert "error" in resp1.json()

    # 2. NaN / Inf pressure_m -> 422
    resp2 = client.post("/api/ingest", json={"t": 10, "node_id": "105", "pressure_m": "NaN", "flow_lps": None})
    assert resp2.status_code == 422

    # 3. Unknown scenario_id -> 404
    resp3 = client.post("/api/replay/start", json={"scenario_id": "non_existent_scenario_12345", "speed": 5})
    assert resp3.status_code == 404

    # 4. Invalid replay speed (e.g. 13 instead of 1, 5, 20) -> 422
    resp4 = client.post("/api/replay/start", json={"scenario_id": "scenario_normal", "speed": 13})
    assert resp4.status_code == 422

    # 5. Oversized payload -> 413
    huge_str = "x" * 70000
    resp5 = client.post("/api/ingest", content=huge_str, headers={"Content-Type": "application/json"})
    assert resp5.status_code in (413, 422)


def test_faulty_ml_returns_stub_and_does_not_crash():
    async def _run():
        bridge = MLBridge()

        # Simulate ML raising an unhandled exception
        import backend.ml_bridge as ml_mod
        orig_detect = ml_mod._real_detect
        orig_mode = ml_mod.ML_MODE
        ml_mod.ML_MODE = "real"

        def broken_detect(w):
            raise RuntimeError("CUDA device out of memory!")

        ml_mod._real_detect = broken_detect

        # Detect must catch exception, log warning and return normalized stub output
        res = await bridge.detect([{"pressure_m": 35.0}])
        assert isinstance(res, dict)
        assert "anomaly" in res
        assert "score" in res
        assert 0.0 <= res["score"] <= 1.0

        # Simulate ML returning garbage schema
        def garbage_detect(w):
            return "not a dict"

        ml_mod._real_detect = garbage_detect
        res2 = await bridge.detect([{"pressure_m": 35.0}])
        assert isinstance(res2, dict)
        assert "anomaly" in res2

        # Restore
        ml_mod._real_detect = orig_detect
        ml_mod.ML_MODE = orig_mode

    asyncio.run(_run())


def test_killed_websocket_client_does_not_affect_others(client):
    pipeline.reset_state(clear_buffers=True)

    with client.websocket_connect("/ws/stream") as ws1:
        with client.websocket_connect("/ws/stream") as ws2:
            # Broadcast reading
            client.post("/api/ingest", json={"t": 10, "node_id": "105", "pressure_m": 52.0, "flow_lps": None})
            m1 = ws1.receive_json()
            m2 = ws2.receive_json()
            assert m1["type"] == "readings"
            assert m2["type"] == "readings"

        # ws2 is now closed/killed
        # ws1 should still receive subsequent messages without issue
        client.post("/api/ingest", json={"t": 20, "node_id": "105", "pressure_m": 51.5, "flow_lps": None})
        m3 = ws1.receive_json()
        assert m3["type"] == "readings"
        assert m3["t"] == 20


def test_rapid_replay_start_stop_50_times(client):
    for i in range(50):
        resp_start = client.post("/api/replay/start", json={"scenario_id": "scenario_normal", "speed": 20})
        assert resp_start.status_code == 200
        resp_stop = client.post("/api/replay/stop")
        assert resp_stop.status_code == 200

    # Ensure engine is stopped cleanly
    assert not replay_engine.is_running
