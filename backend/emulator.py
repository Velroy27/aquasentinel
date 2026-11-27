# AquaSentinel Node Emulator
import argparse
import json
import logging
import sys
import time
from typing import Any, Optional
import httpx

from backend.config import DATA_REPLAY_DIR, MOCK_REPLAY_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (emulator) %(message)s"
)
logger = logging.getLogger("aquasentinel.emulator")


class NodeEmulator:
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8000",
        use_mqtt: bool = False,
        mqtt_host: str = "127.0.0.1",
        mqtt_port: int = 1883,
        speed: float = 1.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.use_mqtt = use_mqtt
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.speed = max(0.1, speed)
        self.mqtt_client = None

        if self.use_mqtt:
            self._init_mqtt()

    def _init_mqtt(self) -> None:
        try:
            import paho.mqtt.client as mqtt
            callback_api_version = getattr(mqtt, 'CallbackAPIVersion', None)
            if callback_api_version:
                self.mqtt_client = mqtt.Client(callback_api_version.VERSION2, client_id="aquasentinel_emulator")
            else:
                self.mqtt_client = mqtt.Client(client_id="aquasentinel_emulator")
            self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
            self.mqtt_client.loop_start()
            logger.info("Connected to MQTT broker at %s:%d", self.mqtt_host, self.mqtt_port)
            logger.warning("Backend has no native MQTT subscriber; MQTT mode requires an external bridge to HTTP /api/ingest")
        except Exception as e:
            logger.warning("Failed to connect to MQTT broker (%s); falling back to HTTP POST", e)
            self.use_mqtt = False
            self.mqtt_client = None

    def publish_reading(self, reading: dict[str, Any]) -> bool:
        if self.use_mqtt and self.mqtt_client:
            try:
                topic = f"aquasentinel/nodes/{reading.get('node_id')}"
                payload = json.dumps(reading)
                res = self.mqtt_client.publish(topic, payload)
                if res.rc == 0:
                    return True
                logger.warning("MQTT publish failed with rc=%d; falling back to HTTP", res.rc)
            except Exception as e:
                logger.warning("MQTT publish error: %s; falling back to HTTP", e)

        # HTTP POST fallback / default
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.post(f"{self.api_url}/api/ingest", json=reading)
                if resp.status_code in (200, 201):
                    return True
                logger.warning("HTTP POST ingest returned status %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.warning("HTTP POST ingest failed: %s", e)
            return False

    def run_scenario(self, scenario_file: Path, repeat: bool = False) -> None:
        if not scenario_file.exists():
            logger.error("Scenario file not found: %s", scenario_file)
            return

        with open(scenario_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            steps = data
            scenario_name = scenario_file.stem
        elif isinstance(data, dict):
            steps = data.get("steps", [])
            scenario_name = data.get("scenario_id", scenario_file.name)
        else:
            steps = []
            scenario_name = scenario_file.name

        if not steps:
            logger.error("No steps found in scenario: %s", scenario_file)
            return

        logger.info("Starting emulator for scenario '%s' with %d steps at %.1fx speed",
                    scenario_name, len(steps), self.speed)

        while True:
            for step in steps:
                step_t = step.get("t", 0)
                readings = step.get("readings", [])
                logger.info("Emulating step t=%d with %d node readings", step_t, len(readings))

                for r in readings:
                    self.publish_reading(r)

                time.sleep(10.0 / self.speed)

            if not repeat:
                break
            logger.info("Repeating scenario playback...")

        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="AquaSentinel Sensor Node Emulator")
    parser.add_argument("--scenario", type=str, default="scenario_03_nonsensor_node101", help="Scenario ID or filename")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (1, 5, 20)")
    parser.add_argument("--api", type=str, default="http://127.0.0.1:8000", help="Backend API base URL")
    parser.add_argument("--mqtt", action="store_true", help="Publish over MQTT instead of HTTP")
    parser.add_argument("--mqtt-host", type=str, default="127.0.0.1", help="MQTT Broker Host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT Broker Port")
    parser.add_argument("--repeat", action="store_true", help="Loop playback continuously")

    args = parser.parse_args()

    sc_input = args.scenario
    sc_path = Path(sc_input)
    if not sc_path.exists():
        candidates = [
            DATA_REPLAY_DIR / sc_input,
            DATA_REPLAY_DIR / f"{sc_input}.json",
            MOCK_REPLAY_DIR / sc_input,
            MOCK_REPLAY_DIR / f"{sc_input}.json",
        ]
        cat_file = DATA_REPLAY_DIR / "scenarios_catalog.json"
        if cat_file.exists():
            try:
                with open(cat_file, "r", encoding="utf-8") as f:
                    cat = json.load(f)
                for item in cat:
                    if item.get("id") == sc_input or item.get("filename") == sc_input:
                        candidates.insert(0, DATA_REPLAY_DIR / item["filename"])
            except Exception:
                pass

        for cand in candidates:
            if cand.exists():
                sc_path = cand
                break

    emulator = NodeEmulator(
        api_url=args.api,
        use_mqtt=args.mqtt,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        speed=args.speed,
    )
    try:
        emulator.run_scenario(sc_path, repeat=args.repeat)
    except KeyboardInterrupt:
        logger.info("Emulator stopped by user")


if __name__ == "__main__":
    main()
