# WebSocket Stream Contract & Behavior Tests
import pytest
from fastapi.testclient import TestClient

from backend.main import app, pipeline


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_websocket_stream_messages(client):
    pipeline.reset_state(clear_buffers=True)

    with client.websocket_connect("/ws/stream") as ws:
        reading_payload = {
            "t": 10,
            "node_id": "105",
            "pressure_m": 51.5,
            "flow_lps": None
        }
        resp = client.post("/api/ingest", json=reading_payload)
        assert resp.status_code == 201

        msg = ws.receive_json()
        if msg.get("type") == "ping":
            msg = ws.receive_json()

        assert msg["type"] == "readings"
        assert msg["t"] == 10
        assert isinstance(msg["readings"], list)
        assert len(msg["readings"]) == 1
        r = msg["readings"][0]
        assert r["t"] == 10
        assert r["node_id"] == "105"
        assert r["pressure_m"] == 51.5
        assert r["flow_lps"] is None


def test_websocket_contract_schema_types(client):
    pipeline.reset_state(clear_buffers=True)

    with client.websocket_connect("/ws/stream") as ws:
        # Step 0: normal
        client.post("/api/ingest", json={"t": 0, "node_id": "105", "pressure_m": 52.0, "flow_lps": None})
        m0 = ws.receive_json()
        assert m0["type"] == "readings"

        # Step 1: normal
        client.post("/api/ingest", json={"t": 10, "node_id": "105", "pressure_m": 52.0, "flow_lps": None})
        m1 = ws.receive_json()
        assert m1["type"] == "readings"

        # Step 2: sharp pressure drop to trigger anomaly
        client.post("/api/ingest", json={"t": 20, "node_id": "105", "pressure_m": 38.0, "flow_lps": 5.0})
        
        # We expect: readings -> alert -> localization -> loss
        m_readings = ws.receive_json()
        assert m_readings["type"] == "readings"

        m_alert = ws.receive_json()
        assert m_alert["type"] == "alert"
        assert isinstance(m_alert["id"], str)
        assert isinstance(m_alert["t"], int)
        assert m_alert["severity"] in ("critical", "high", "medium", "low")
        assert isinstance(m_alert["score"], float)
        assert isinstance(m_alert["detector"], str)

        m_loc = ws.receive_json()
        assert m_loc["type"] == "localization"
        assert isinstance(m_loc["alert_id"], str)
        assert isinstance(m_loc["top3"], list)
        assert len(m_loc["top3"]) <= 3
        for item in m_loc["top3"]:
            assert isinstance(item["node_id"], str)
            assert isinstance(item["prob"], float)
        assert isinstance(m_loc["probs"], dict)

        m_loss = ws.receive_json()
        assert m_loss["type"] == "loss"
        assert isinstance(m_loss["alert_id"], str)
        assert isinstance(m_loss["est_leak_lps"], (int, float))
        assert isinstance(m_loss["litres_lost_cum"], (int, float))
        assert m_loss["litres_lost_cum"] >= 0.0
