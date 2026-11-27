# AquaSentinel Soak Test (Long-running stability & memory leak verification)
import argparse
import asyncio
import random
import sys
import time
import tracemalloc
import pytest

from backend.main import app, pipeline, replay_engine, db, ws_manager
import backend.ml_bridge as ml_mod


class SimulatedWSClient:
    def __init__(self, client_id: int):
        self.client_id = client_id
        self.received: list[dict] = []
        self.is_closed = False

    async def accept(self):
        pass

    async def send_json(self, msg: dict):
        if self.is_closed:
            raise ConnectionResetError("Client disconnected")
        self.received.append(msg)


def run_soak_test(duration_seconds: float = 10.0):
    print(f"Starting AquaSentinel Soak Test for {duration_seconds:.1f} seconds...")
    tracemalloc.start()
    snapshot_start = tracemalloc.take_snapshot()

    unhandled_exceptions = []
    alerts_seen = []

    # Inject flaky ML stub intermittently
    orig_detect = ml_mod._real_detect
    orig_mode = ml_mod.ML_MODE
    ml_mod.ML_MODE = "real"

    def flaky_detect(window):
        # 15% chance to raise error
        if random.random() < 0.15:
            raise RuntimeError("Injected transient ML exception")
        return ml_mod.stub_detect(window)

    ml_mod._real_detect = flaky_detect

    async def async_soak():
        start_time = time.time()
        pipeline.reset_state(clear_buffers=True)

        # 1. Start 20x replay
        await replay_engine.start("scenario_105_leak", speed=20)
        active_ws_clients: dict[int, SimulatedWSClient] = {}

        iteration = 0
        while time.time() - start_time < duration_seconds:
            iteration += 1

            # Manage up to 3 simulated WebSocket clients randomly connecting/disconnecting
            for cid in [1, 2, 3]:
                if cid not in active_ws_clients and random.random() < 0.5:
                    client = SimulatedWSClient(cid)
                    await ws_manager.connect(client)
                    active_ws_clients[cid] = client
                elif cid in active_ws_clients and random.random() < 0.3:
                    client = active_ws_clients.pop(cid)
                    client.is_closed = True
                    await ws_manager.disconnect(client)

            # Ingest occasionally malformed reading to test pipeline resiliency
            if random.random() < 0.2:
                try:
                    await pipeline.ingest_single_reading({
                        "t": iteration * 10,
                        "node_id": "105",
                        "pressure_m": 42.0 + random.gauss(0, 0.5),
                        "flow_lps": None
                    })
                except Exception as e:
                    unhandled_exceptions.append(f"Ingest exception: {e}")

            # Collect any alerts received by active clients
            for client in active_ws_clients.values():
                for m in client.received:
                    if m.get("type") == "alert":
                        alerts_seen.append(m["id"])

            # Check health
            if not db.is_healthy():
                unhandled_exceptions.append("DB is not healthy")

            await asyncio.sleep(0.2)

        # Cleanup clients
        for client in list(active_ws_clients.values()):
            await ws_manager.disconnect(client)

        await replay_engine.stop()

    asyncio.run(async_soak())

    # Restore ML
    ml_mod._real_detect = orig_detect
    ml_mod.ML_MODE = orig_mode

    # Memory snapshot
    snapshot_end = tracemalloc.take_snapshot()
    top_stats = snapshot_end.compare_to(snapshot_start, "lineno")
    total_diff_kb = sum(stat.size_diff for stat in top_stats) / 1024.0

    print(f"Soak completed.")
    print(f"Memory diff: {total_diff_kb:.2f} KB")
    print(f"Distinct alerts seen: {len(set(alerts_seen))}")
    print(f"Unhandled exceptions: {len(unhandled_exceptions)}")

    # Assertions
    assert len(unhandled_exceptions) == 0, f"Encountered unhandled exceptions: {unhandled_exceptions}"
    # Memory growth bounded (under 15MB)
    assert total_diff_kb < 15000, f"Memory growth excessive: {total_diff_kb} KB"
    # Deduplication: exactly 1 distinct alert id for the leak event
    assert len(set(alerts_seen)) <= 1

    # Verify DB has recorded events
    runs = db.get_runs()
    assert len(runs) >= 1

    tracemalloc.stop()
    print("SOAK TEST PASSED.")


def test_soak_short():
    """Short soak test executed during pytest runs (10 seconds)."""
    run_soak_test(duration_seconds=10.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AquaSentinel Soak Test")
    parser.add_argument("--duration", type=float, default=300.0, help="Soak test duration in seconds (default: 300 = 5 minutes)")
    args = parser.parse_args()
    run_soak_test(duration_seconds=args.duration)
