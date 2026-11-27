# AquaSentinel Pre-Demo Readiness Verification Script
import asyncio
import json
import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

base_sp = Path(sys.base_prefix) / "Lib" / "site-packages"
if base_sp.exists() and str(base_sp) not in sys.path:
    sys.path.append(str(base_sp))

from backend.config import DATA_DIR, DATA_REPLAY_DIR, MOCK_DIR
from backend.main import app, pipeline, replay_engine, db, ml_bridge

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
        DATA_REPLAY_DIR / "scenarios_catalog.json",
        MOCK_DIR / "network_net3.json",
        MOCK_DIR / "results.json",
        MOCK_DIR / "placement.json",
        MOCK_DIR / "scenarios.json",
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
                report("ML Real Mode Check", ml_mode == "real", f"mode={ml_mode}")
            else:
                report("API Health Check", False, f"status_code={resp.status_code}")
                report("ML Real Mode Check", False, "health request failed")
    except Exception as e:
        report("API Health Check", False, str(e))
        report("ML Real Mode Check", False, str(e))


async def check_catalog_scenarios_and_print_table():
    catalog_path = DATA_REPLAY_DIR / "scenarios_catalog.json"
    if not catalog_path.exists():
        report("Scenarios Catalog Exists", False, f"Missing {catalog_path}")
        return

    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Dynamically find scenario 03 from catalog by real id or filename
    scen_03_item = next(
        (item for item in catalog if "03" in item.get("id", "") or "03" in item.get("filename", "")),
        None
    )
    if not scen_03_item:
        report("Discover Scenario 03 Check", False, "Scenario 03 not found in catalog")
        return

    scen_03_id = scen_03_item["id"]
    report("Discover Scenario 03 Check", True, f"Found '{scen_03_id}' ({scen_03_item.get('filename')})")

    # Ensure real ML mode is active
    ml_bridge.set_real_mode()
    await ml_bridge.warmup([])

    print("\nRunning all catalog scenarios at 20x speed...")
    t_start_all = time.time()
    results = []

    for item in catalog:
        sc_id = item["id"]
        start_t = int(item.get("start_t", 0))
        category = item.get("category", "")
        leak_node = item.get("leak_node")
        leak_size_lps = float(item.get("leak_size_lps", 0.0))
        is_leak = (leak_node is not None) and (leak_size_lps > 0.0) and (category != "sensor_fault")
        is_no_leak_or_fault = (not is_leak) or (category in ("sensor_fault", "no_leak"))

        pipeline.reset_state(clear_buffers=True)
        ml_bridge.stub_calls = 0

        received_alerts = []
        fault_flagged = False

        async def intercept_broadcast(msg):
            nonlocal fault_flagged
            m_type = msg.get("type")
            if m_type == "alert":
                received_alerts.append(msg)
            elif m_type == "fault":
                fault_flagged = True

        old_broadcast = pipeline.broadcast_func
        pipeline.broadcast_func = intercept_broadcast

        t0 = time.time()
        await replay_engine.start(sc_id, speed=20)
        while replay_engine.is_running:
            await asyncio.sleep(0.2)

        elapsed = time.time() - t0
        pipeline.broadcast_func = old_broadcast

        alerts_count = len(received_alerts)
        first_alert_t = received_alerts[0].get("t") if received_alerts else None
        stub_calls = ml_bridge.stub_calls
        avg_latency = ml_bridge.get_avg_latency_ms()

        results.append({
            "id": sc_id,
            "filename": item.get("filename"),
            "alerts_count": alerts_count,
            "first_alert_t": first_alert_t,
            "start_t": start_t,
            "fault_flagged": fault_flagged,
            "stub_calls": stub_calls,
            "avg_latency": avg_latency,
            "is_leak": is_leak,
            "is_no_leak_or_fault": is_no_leak_or_fault,
            "elapsed_s": round(elapsed, 1),
        })

    total_duration = time.time() - t_start_all

    # Print Table
    print("\n" + "=" * 105)
    print(f"{'Scenario ID':<15} | {'Alerts':<6} | {'First Alert t':<13} | {'Start t':<7} | {'Fault':<6} | {'Stub Calls':<10} | {'Avg Latency (ms)':<16} | {'Runtime':<7}")
    print("-" * 105)
    for r in results:
        fat_str = f"{r['first_alert_t']}s" if r['first_alert_t'] is not None else "None"
        st_str = f"{r['start_t']}s"
        fault_str = "YES" if r["fault_flagged"] else "NO"
        rt_str = f"{r['elapsed_s']}s"
        print(f"{r['id']:<15} | {r['alerts_count']:<6} | {fat_str:<13} | {st_str:<7} | {fault_str:<6} | {r['stub_calls']:<10} | {r['avg_latency']:<16.1f} | {rt_str:<7}")
    print("=" * 105)
    print(f"Total scenarios runtime: {total_duration:.1f} seconds (~{total_duration/60.0:.1f} minutes)\n")

    # Assertions on Scenario 03
    scen03_res = next((r for r in results if r["id"] == scen_03_id), None)
    if scen03_res:
        one_alert = (scen03_res["alerts_count"] == 1)
        timing_ok = (scen03_res["first_alert_t"] is not None) and (scen03_res["first_alert_t"] >= scen03_res["start_t"])
        zero_stubs = (scen03_res["stub_calls"] == 0)
        real_mode = (ml_bridge.get_mode() == "real")
        report("Scenario 03 Single Alert Check", one_alert, f"alerts_count={scen03_res['alerts_count']}")
        report("Scenario 03 Alert Timing Check (t >= start_t)", timing_ok,
               f"alert_t={scen03_res['first_alert_t']}s, start_t={scen03_res['start_t']}s")
        report("Scenario 03 Zero Stub Calls Check", zero_stubs, f"stub_calls={scen03_res['stub_calls']}")
        report("Scenario 03 ML Real Mode Check", real_mode, f"mode={ml_bridge.get_mode()}")
    else:
        report("Scenario 03 Evaluation", False, "Scenario 03 missing from results")

    # Assertions across all scenarios
    for r in results:
        sc_id = r["id"]
        # 1. Fail on stub calls
        stubs_ok = (r["stub_calls"] == 0)
        report(f"{sc_id} Zero Stubs Check", stubs_ok, f"stub_calls={r['stub_calls']}")

        # 2. For leak scenarios, fail on alert before start_t
        if r["is_leak"]:
            if r["first_alert_t"] is not None:
                timing_ok = (r["first_alert_t"] >= r["start_t"])
                report(f"{sc_id} Alert Timing Check", timing_ok,
                       f"first_alert={r['first_alert_t']}s >= start_t={r['start_t']}s")
            else:
                # Undetected leak scenarios (e.g. scenario_01, 02, 06) do not fire an alert before start_t
                report(f"{sc_id} No Premature Alert Check", True, "No false positive prior to start_t")

        # 3. For no-leak or sensor-fault scenarios, fail on any leak alert
        if r["is_no_leak_or_fault"]:
            no_leak_alert = (r["alerts_count"] == 0)
            report(f"{sc_id} Zero False Leak Alerts Check", no_leak_alert, f"alerts={r['alerts_count']}")
            if "fault" in r["id"]:
                report(f"{sc_id} Fault Detected Check", r["fault_flagged"], "Sensor fault flagged")


def main():
    print("=" * 60)
    print(" AquaSentinel Pre-Demo Readiness Checker")
    print("=" * 60)

    check_files_exist()
    check_db_writable()
    check_api_health_and_ml_mode()
    asyncio.run(check_catalog_scenarios_and_print_table())

    print("-" * 60)
    print(f"Summary: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    if FAIL_COUNT > 0:
        sys.exit(1)
    else:
        print("ALL PRE-DEMO READINESS CHECKS PASSED.")


if __name__ == "__main__":
    main()
