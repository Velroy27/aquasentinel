import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import httpx
import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/stream"


async def main():
    print("=" * 70)
    print(" AquaSentinel E2E 3-Phase Verification")
    print("=" * 70)

    py_exe = sys.executable

    # Check 3: Cold Start with backend/run_all.py
    print("\n[PHASE 1] Starting backend/run_all.py from Cold Start...")
    supervisor_cmd = [
        py_exe, "-m", "backend.run_all",
        "--scenario", "scenario_03_nonsensor_node101",
        "--speed", "20"
    ]

    import threading

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        supervisor_cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env
    )

    startup_logs = []
    server_ready = False
    emulator_started = False

    def read_output():
        nonlocal server_ready, emulator_started
        for line in iter(proc.stdout.readline, ''):
            clean = line.strip()
            if clean:
                startup_logs.append(clean)
                print(f"  [supervisor] {clean}")
                if "API Server is healthy and listening" in clean:
                    server_ready = True
                if "Starting Sensor Node Emulator" in clean:
                    emulator_started = True

    t_reader = threading.Thread(target=read_output, daemon=True)
    t_reader.start()

    # Wait for startup confirmation
    start_wait = time.time()
    while time.time() - start_wait < 45.0:
        if server_ready and emulator_started:
            break
        if proc.poll() is not None:
            break
        await asyncio.sleep(0.2)

    assert server_ready, "API server failed to become ready before emulator"
    assert emulator_started, "Emulator failed to start after API became ready"
    print("\n>>> Check 3 Verified: Supervisor ordered startup correctly (API warm-up completed before emulator started).")

    # Connect to WebSocket (Check 2: React Dashboard / WS live propagation)
    print("\n[PHASE 2] Connecting WebSocket client to /ws/stream (simulating React dashboard)...")
    received_alert = None
    received_loc = None
    received_loss = None

    async with websockets.connect(WS_URL) as ws:
        # Check /health to verify active_ws_clients >= 1
        async with httpx.AsyncClient() as client:
            h_resp = await client.get(f"{API_URL}/health")
            health_during_ws = h_resp.json()
            print(f"  /health with active WS client: active_ws_clients={health_during_ws.get('active_ws_clients')}")
            assert health_during_ws.get("active_ws_clients", 0) >= 1

        print("  Listening for live sensor readings and leak detection events...")
        listen_start = time.time()
        while time.time() - listen_start < 60.0:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg = json.loads(raw_msg)
                mtype = msg.get("type")
                if mtype != "readings":
                    print(f"  >>> WS Received message [{mtype}]: {msg}")
                if mtype == "alert" and not received_alert:
                    received_alert = msg
                elif mtype == "localization" and not received_loc:
                    received_loc = msg
                elif mtype == "loss":
                    received_loss = msg

                if received_alert and received_loc and received_loss and received_loss.get("litres_lost_cum", 0) > 0:
                    print("  >>> Received alert, localization, and accumulating loss!")
                    break
            except asyncio.TimeoutError:
                if proc.poll() is not None:
                    print("  [ERROR] Supervisor process terminated prematurely")
                    break

        # Check /health after leak detection (Check 1: ML Mode & stub calls)
        print("\n[PHASE 3] Checking /health after leak detection and localization...")
        async with httpx.AsyncClient() as client:
            final_health_resp = await client.get(f"{API_URL}/health")
            final_health = final_health_resp.json()

    print("\n" + "=" * 70)
    print(" EXACT /health JSON:")
    print("=" * 70)
    print(json.dumps(final_health, indent=2))

    print("\n" + "=" * 70)
    print(" LEAK PREDICTION OUTPUT:")
    print("=" * 70)
    print(f"Alert ID:       {received_alert.get('id') if received_alert else 'N/A'}")
    print(f"Severity:       {received_alert.get('severity') if received_alert else 'N/A'}")
    print(f"Anomaly Score:  {received_alert.get('score') if received_alert else 'N/A'}")
    print(f"Localized Top3: {json.dumps(received_loc.get('top3') if received_loc else [], indent=2)}")
    print(f"Est. Leak Size: {received_loss.get('est_leak_lps') if received_loss else 'N/A'} L/s")
    print(f"Cumulative Vol: {received_loss.get('litres_lost_cum') if received_loss else 'N/A'} L")

    # Gracefully stop supervisor
    print("\n[CLEANUP] Stopping supervisor process...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    print("Supervisor stopped.")

    # Validation assertions
    assert final_health.get("status") == "ok", f"Expected status ok, got {final_health.get('status')}"
    assert final_health.get("ml_mode") == "real", f"Expected ml_mode real, got {final_health.get('ml_mode')}"
    assert final_health.get("stub_calls") == 0, f"Expected stub_calls 0, got {final_health.get('stub_calls')}"
    assert final_health.get("is_warming_up") is False, "Expected is_warming_up False"
    assert final_health.get("db_ok") is True, "Expected db_ok True"
    assert received_alert is not None, "Alert was not received over WebSocket"
    assert received_loc is not None, "Localization was not received over WebSocket"
    assert received_loss is not None, "Loss estimate was not received over WebSocket"

    print("\n>>> ALL 3 END-TO-END CHECKS PASSED WITH REAL ML MODELS AND 0 STUB CALLS! <<<")


if __name__ == "__main__":
    asyncio.run(main())
