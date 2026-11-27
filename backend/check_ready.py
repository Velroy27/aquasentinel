import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# AquaSentinel Pre-Demo Readiness Verification Script
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient

from backend.config import DATA_DIR, MOCK_DIR, MOCK_REPLAY_DIR, REPO_ROOT
from backend.main import app, pipeline, replay_engine, db

PASS_COUNT = 0
FAIL_COUNT = 0


def report(name: str, passed: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    tag = "[PASS]" if passed else "[FAIL]"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    msg = f"{tag} {name}"
    if detail:
        msg += f" - {detail}"
    print(msg)


def check_files_exist():
    required_files = [
        DATA_DIR / "Net3.inp",
        MOCK_DIR / "network_net3.json",
        MOCK_DIR / "results.json",
        MOCK_DIR / "placement.json",
        MOCK_DIR / "scenarios.json",
        MOCK_REPLAY_DIR / "scenario_105_leak.json",
        MOCK_REPLAY_DIR / "scenario_normal.json",
    ]
    all_exist = True
    missing = []
    for f in required_files:
        if not f.exists():
            all_exist = False
            missing.append(f.name)
    report("Required Files Check", all_exist, f"missing: {missing}" if missing else "all present")


def check_db_writable():
    try:
        passed = db.is_healthy()
        report("Database Writable Check", passed, "database responding to health queries")
    except Exception as e:
        report("Database Writable Check", False, str(e))


def check_api_health_and_ml_mode():
    try:
        with TestClient(app) as client:
            resp = client.get("/health")
            if resp.status_code == 200:
                data = resp.json()
                ml_mode = data.get("ml_mode")
                db_ok = data.get("db_ok")
                report("API Health Check", db_ok and data.get("status") == "ok", f"status={data.get('status')}")
                report("ML Mode Check", True, f"mode={ml_mode}")
            else:
                report("API Health Check", False, f"status_code={resp.status_code}")
                report("ML Mode Check", False, "health request failed")
    except Exception as e:
        report("API Health Check", False, str(e))
        report("ML Mode Check", False, str(e))


async def check_replay_and_single_alert():
    try:
        pipeline.reset_state(clear_buffers=True)
        received_alerts = []
        received_losses = []

        async def intercept_broadcast(msg):
            if msg.get("type") == "alert":
                received_alerts.append(msg)
            elif msg.get("type") == "loss":
                received_losses.append(msg)

        old_broadcast = pipeline.broadcast_func
        pipeline.broadcast_func = intercept_broadcast

        # Start 20x replay
        await replay_engine.start("scenario_105_leak", speed=20)

        # Wait for replay to complete or max 15 seconds
        start_wait = time.time()
        while replay_engine.is_running and time.time() - start_wait < 15.0:
            await asyncio.sleep(0.2)

        await replay_engine.stop()
        pipeline.broadcast_func = old_broadcast

        one_alert = len(received_alerts) == 1
        loss_increased = len(received_losses) > 0 and received_losses[-1]["litres_lost_cum"] > 0

        report("Single Deduplicated Alert Check", one_alert, f"alerts_count={len(received_alerts)}")
        report("Cumulative Loss Growth Check", loss_increased,
               f"loss_count={len(received_losses)}, final_litres={received_losses[-1]['litres_lost_cum'] if received_losses else 0}")
    except Exception as e:
        report("Single Deduplicated Alert Check", False, str(e))
        report("Cumulative Loss Growth Check", False, str(e))


def main():
    print("=" * 60)
    print(" AquaSentinel Pre-Demo Readiness Checker")
    print("=" * 60)

    check_files_exist()
    check_db_writable()
    check_api_health_and_ml_mode()
    asyncio.run(check_replay_and_single_alert())

    print("-" * 60)
    print(f"Summary: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    if FAIL_COUNT > 0:
        sys.exit(1)
    else:
        print("ALL PRE-DEMO READINESS CHECKS PASSED.")


if __name__ == "__main__":
    main()
