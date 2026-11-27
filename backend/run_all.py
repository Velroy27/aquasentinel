# AquaSentinel Supervisor & All-in-One Runner
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
API_HOST = "127.0.0.1"
API_PORT = 8000
MAX_EMULATOR_RESTARTS = 5


def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def wait_for_api_ready(timeout_s: float = 15.0) -> bool:
    start = time.time()
    url = f"http://{API_HOST}:{API_PORT}/health"
    while time.time() - start < timeout_s:
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get(url)
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    print("=" * 60)
    print(" AquaSentinel Unified Runner")
    print("=" * 60)

    # 1. Port Check
    if is_port_in_use(API_HOST, API_PORT):
        print(f"
[ERROR] Port {API_PORT} is already in use!")
        print(f"Fix: Run 'Get-Process -Id (Get-NetTCPConnection -LocalPort {API_PORT}).OwningProcess | Stop-Process -Force' in PowerShell
")
        sys.exit(1)

    py_exe = sys.executable

    # 2. Launch FastAPI Server via Uvicorn
    server_cmd = [
        py_exe, "-m", "uvicorn", "backend.main:app",
        "--host", API_HOST,
        "--port", str(API_PORT),
        "--log-level", "info"
    ]
    print(f"Starting API Server on http://{API_HOST}:{API_PORT}...")
    server_proc = subprocess.Popen(server_cmd, cwd=str(REPO_ROOT))

    # Wait for API to become ready
    if not wait_for_api_ready(15.0):
        print("
[ERROR] API Server failed to respond to /health in 15 seconds.")
        server_proc.terminate()
        sys.exit(1)

    print("API Server is healthy and listening.")

    # 3. Launch and Supervise Node Emulator
    emulator_cmd = [
        py_exe, "-m", "backend.emulator",
        "--scenario", "scenario_105_leak",
        "--speed", "5",
        "--api", f"http://{API_HOST}:{API_PORT}",
        "--repeat"
    ]

    restarts = 0
    emulator_proc = None

    try:
        print("Starting Sensor Node Emulator (scenario_105_leak @ 5x)...")
        emulator_proc = subprocess.Popen(emulator_cmd, cwd=str(REPO_ROOT))

        while True:
            # Check server
            if server_proc.poll() is not None:
                print("[ERROR] API server exited unexpectedly.")
                break

            # Check emulator
            if emulator_proc.poll() is not None:
                ret = emulator_proc.returncode
                if restarts < MAX_EMULATOR_RESTARTS:
                    restarts += 1
                    print(f"[WARNING] Emulator crashed with code {ret}. Restarting ({restarts}/{MAX_EMULATOR_RESTARTS}) in 3s...")
                    time.sleep(3.0)
                    emulator_proc = subprocess.Popen(emulator_cmd, cwd=str(REPO_ROOT))
                else:
                    print(f"[ERROR] Emulator reached maximum restart limit ({MAX_EMULATOR_RESTARTS}). Exiting.")
                    break

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("
Received Ctrl+C, shutting down processes gracefully...")
    finally:
        if emulator_proc and emulator_proc.poll() is None:
            emulator_proc.terminate()
            try:
                emulator_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                emulator_proc.kill()

        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

        print("AquaSentinel shutdown complete.")


if __name__ == "__main__":
    main()
