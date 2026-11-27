# REST API Contract Validation Tests
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_get_network_contract(client):
    response = client.get("/api/network")
    assert response.status_code == 200
    data = response.json()

    # Contract: {'nodes':[{'id','x','y','elevation','is_sensor'}],'pipes':[{'id','from','to','length','diameter'}],'sensors':['id',...]}
    assert "nodes" in data and isinstance(data["nodes"], list)
    assert "pipes" in data and isinstance(data["pipes"], list)
    assert "sensors" in data and isinstance(data["sensors"], list)

    assert len(data["nodes"]) == 92, "Should have 92 junctions for Net3"

    for node in data["nodes"]:
        assert isinstance(node["id"], str)
        assert isinstance(node["x"], (int, float))
        assert isinstance(node["y"], (int, float))
        assert isinstance(node["elevation"], (int, float))
        assert isinstance(node["is_sensor"], bool)

    for pipe in data["pipes"]:
        assert isinstance(pipe["id"], str)
        assert isinstance(pipe["from"], str)
        assert isinstance(pipe["to"], str)
        assert isinstance(pipe["length"], (int, float))
        assert isinstance(pipe["diameter"], (int, float))

    for s in data["sensors"]:
        assert isinstance(s, str)


def test_get_scenarios_contract(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()

    # Contract: [{'id','leak_node','leak_size_lps','start_t','description'}]
    assert isinstance(data, list)
    # Assert audited catalog scenarios appear FIRST in exact catalog order
    sc_ids = [sc["id"] for sc in data]
    assert "scenario_01" in sc_ids
    assert "scenario_03" in sc_ids
    assert sc_ids.index("scenario_01") == 0
    assert sc_ids.index("scenario_02") == 1
    assert sc_ids.index("scenario_03") == 2

    # Assert mock scenario appears after catalog scenarios
    if "scenario_105_leak" in sc_ids:
        assert sc_ids.index("scenario_105_leak") >= 7


def test_get_results_contract(client):
    response = client.get("/api/results")
    assert response.status_code == 200
    data = response.json()

    # Contract: {'detection_rate','false_alarm_per_day','top1','top3','neighbour_acc','chance_baseline','placement':[{'n_sensors','optimized','random_mean','random_std'}],'measured_at'}
    required_keys = [
        "detection_rate", "false_alarm_per_day", "top1", "top3",
        "neighbour_acc", "chance_baseline", "placement", "measured_at"
    ]
    for key in required_keys:
        assert key in data, f"Missing key {key}"

    assert isinstance(data["detection_rate"], (int, float))
    assert isinstance(data["false_alarm_per_day"], (int, float))
    assert isinstance(data["top1"], (int, float))
    assert isinstance(data["top3"], (int, float))
    assert isinstance(data["neighbour_acc"], (int, float))
    assert isinstance(data["chance_baseline"], (int, float))
    assert isinstance(data["measured_at"], str)

    assert isinstance(data["placement"], list)
    for p in data["placement"]:
        assert isinstance(p["n_sensors"], int)
        assert isinstance(p["optimized"], (int, float))
        assert isinstance(p["random_mean"], (int, float))
        assert isinstance(p["random_std"], (int, float))


def test_get_placement_contract(client):
    response = client.get("/api/placement?n=8")
    assert response.status_code == 200
    data = response.json()

    # Contract: {'sensors':[ids],'coverage':float}
    assert "sensors" in data and isinstance(data["sensors"], list)
    assert "coverage" in data and isinstance(data["coverage"], float)
    assert len(data["sensors"]) == 8
    for s in data["sensors"]:
        assert isinstance(s, str)


def test_get_cost_contract(client):
    response = client.get("/api/cost?nodes=8")
    assert response.status_code == 200
    data = response.json()

    # Contract: EXACTLY {'hardware_rs','per_node_rs':1500,'est_loss_avoided_rs'}
    assert set(data.keys()) == {"hardware_rs", "per_node_rs", "est_loss_avoided_rs"}
    assert data["per_node_rs"] == 1500
    assert data["hardware_rs"] == 8 * 1500
    assert isinstance(data["est_loss_avoided_rs"], int)
    assert data["est_loss_avoided_rs"] > 0


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "uptime_s" in data
    assert "ml_mode" in data
    assert "db_ok" in data
    assert "replay_state" in data
    assert "active_ws_clients" in data


def test_post_ingest_contract_and_validation(client):
    # Valid reading
    reading = {"t": 10, "node_id": "105", "pressure_m": 51.2, "flow_lps": None}
    response = client.post("/api/ingest", json=reading)
    assert response.status_code == 201

    # Valid with float flow
    reading2 = {"t": 20, "node_id": "105", "pressure_m": 50.8, "flow_lps": 12.5}
    response2 = client.post("/api/ingest", json=reading2)
    assert response2.status_code == 201

    # Invalid: missing node_id
    bad_reading = {"t": 10, "pressure_m": 51.2}
    resp_bad = client.post("/api/ingest", json=bad_reading)
    assert resp_bad.status_code == 422


def test_replay_start_stop(client):
    # Valid start
    resp_start = client.post("/api/replay/start", json={"scenario_id": "scenario_normal", "speed": 20})
    assert resp_start.status_code == 200
    data = resp_start.json()
    assert data["status"] == "started"
    assert data["speed"] == 20.0

    # Stop
    resp_stop = client.post("/api/replay/stop")
    assert resp_stop.status_code == 200
    assert resp_stop.json()["status"] == "stopped"

    # Stop again (idempotent)
    resp_stop2 = client.post("/api/replay/stop")
    assert resp_stop2.status_code == 200
    assert resp_stop2.json()["status"] == "stopped"


def test_history_endpoints(client):
    resp_runs = client.get("/api/history/runs")
    assert resp_runs.status_code == 200
    assert isinstance(resp_runs.json(), list)

    resp_alerts = client.get("/api/history/alerts")
    assert resp_alerts.status_code == 200
    assert isinstance(resp_alerts.json(), list)

    resp_readings = client.get("/api/history/readings?limit=10")
    assert resp_readings.status_code == 200
    assert isinstance(resp_readings.json(), list)
