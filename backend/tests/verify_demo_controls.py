import asyncio
import json
import time
import httpx
import websockets

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/stream"


async def run_verification():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        # Check health
        health = await client.get("/health")
        print(f"Health response: {health.status_code} -> {health.json()['status']}")
        assert health.status_code == 200

        # Check scenarios
        sc_resp = await client.get("/api/scenarios")
        assert sc_resp.status_code == 200
        scenarios = sc_resp.json()
        sc_ids = [s["id"] for s in scenarios]
        print(f"Loaded {len(scenarios)} scenarios. First 5: {sc_ids[:5]}")
        assert "scenario_03" in sc_ids
        assert "scenario_04" in sc_ids
        assert "scenario_105_leak" in sc_ids

        # Ensure reset before test
        reset_resp = await client.post("/api/replay/reset")
        assert reset_resp.status_code == 200
        print("Reset demo endpoint OK.")

        # Connect WebSocket
        async with websockets.connect(WS_URL) as ws:
            print("Connected to WebSocket.")

            # Test 1: Start scenario_04 at speed=20
            start_resp = await client.post("/api/replay/start", json={"scenario_id": "scenario_04", "speed": 20})
            assert start_resp.status_code == 200
            print(f"Replay started: {start_resp.json()}")

            loss_messages = []
            alert_seen = False
            loc_seen = False

            # Collect messages until alert and loss are received (up to 25s for t=330 to be reached)
            t_end = time.time() + 25.0
            while time.time() < t_end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "alert":
                        alert_seen = True
                        print(f"Alert received: id={msg.get('id')} severity={msg.get('severity')} score={msg.get('score')}")
                    elif mtype == "localization":
                        loc_seen = True
                        print(f"Localization received: alert_id={msg.get('alert_id')} top3={msg.get('top3')}")
                    elif mtype == "loss":
                        loss_messages.append(msg)
                        if len(loss_messages) <= 5 or len(loss_messages) % 10 == 0:
                            print(f"Loss tick: rate={msg.get('est_leak_lps')} L/s, cum={msg.get('litres_lost_cum')} L")
                        if len(loss_messages) >= 10:
                            break
                    elif mtype == "replay_end":
                        print("Replay end event received!")
                        break
                except asyncio.TimeoutError:
                    break

            assert alert_seen, "Expected alert to be generated for scenario_04"
            assert loc_seen, "Expected localization to be generated for scenario_04"
            assert len(loss_messages) >= 2, f"Expected multiple loss messages, got {len(loss_messages)}"

            # Verify loss rate:
            diffs = [
                round(loss_messages[i]["litres_lost_cum"] - loss_messages[i - 1]["litres_lost_cum"], 1)
                for i in range(1, len(loss_messages))
            ]
            est_lps = loss_messages[-1]["est_leak_lps"]
            print(f"Est leak rate: {est_lps} L/s. Sample consecutive diffs: {diffs[:8]}")
            for d in diffs[:8]:
                assert abs(d - est_lps) < 2.0, f"Diff {d} does not match est_lps {est_lps} (was it 10x 1110?)"
            print("VERIFIED: Consecutive loss increments match leak rate (e.g. ~111 L/s), NOT 1110 L!")

            # Test 2: Stop demo
            stop_resp = await client.post("/api/replay/stop")
            assert stop_resp.status_code == 200
            print("POST /api/replay/stop OK.")

            # Test 3: Reset demo
            reset_resp = await client.post("/api/replay/reset")
            assert reset_resp.status_code == 200
            print("POST /api/replay/reset OK.")

            # Verify reset event received on WS
            reset_event_seen = False
            for _ in range(5):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    msg = json.loads(raw)
                    if msg.get("type") == "reset":
                        reset_event_seen = True
                        print("WebSocket received type='reset' event!")
                        break
                except asyncio.TimeoutError:
                    break

            assert reset_event_seen, "Expected WebSocket reset event"

        # Verify scenario_105_leak works from start to finish
        print("\nTesting upgraded scenario_105_leak...")
        start_105 = await client.post("/api/replay/start", json={"scenario_id": "scenario_105_leak", "speed": 20})
        assert start_105.status_code == 200
        print(f"scenario_105_leak started: {start_105.json()}")

        async with websockets.connect(WS_URL) as ws:
            alert_105 = False
            loss_105 = False
            t_end = time.time() + 25.0
            while time.time() < t_end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg = json.loads(raw)
                    if msg.get("type") == "alert":
                        alert_105 = True
                        print(f"scenario_105 alert: {msg.get('id')}")
                    elif msg.get("type") == "loss":
                        loss_105 = True
                        print(f"scenario_105 loss: rate={msg.get('est_leak_lps')} L/s, cum={msg.get('litres_lost_cum')} L")
                        break
                except asyncio.TimeoutError:
                    break

            assert alert_105, "Expected alert for scenario_105_leak"
            assert loss_105, "Expected loss for scenario_105_leak"
            print("VERIFIED: scenario_105_leak successfully detects alert and calculates loss!")

            # Clean stop & reset
            await client.post("/api/replay/reset")

        print("\nALL DEMO START, STOP, RESET, AND LOSS INCREMENT CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_verification())
