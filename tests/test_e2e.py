"""The extension as the agent runs it: main.py, a saved config, a signal; the trace read back."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from .conftest import DEMO_MAP, ROOT, free_port, read_trace

EVENTS = ("_broker", "environment", "power", "device", "device/status", "device/info")


def run_extension(tmp_path, config: dict, *args: str, stop=signal.SIGTERM, seconds=3.0):
    """main.py with a saved config and --file, stopped by `stop`; (output, trace)."""
    (tmp_path / "config.json").write_text(json.dumps(config))
    trace = tmp_path / "run.trz"
    env = {
        **os.environ,
        "ZELOS_CONFIG_PATH": str(tmp_path / "config.json"),
        # Nothing listens: the extension must run and record without an agent.
        "ZELOS_AGENT_URL": f"http://127.0.0.1:{free_port()}",
    }
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--file", str(trace), *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(seconds)
    started = time.monotonic()
    proc.send_signal(stop)
    output, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0, output
    assert time.monotonic() - started < 3.0, output
    return output, read_trace(trace)


@pytest.mark.parametrize(
    ("prefix", "stop", "expected"),
    [
        ("MQTT", signal.SIGTERM, {"MQTT/log", *(f"MQTT/plant/{e}" for e in EVENTS)}),
        ("", signal.SIGINT, {"mqtt_log/log", *(f"plant/{e}" for e in EVENTS)}),
    ],
)
def test_the_extension_records_the_trace_layout(tmp_path, demo_broker, prefix, stop, expected):
    """Mapped topics trace as the map's events, the rest (a JSON object, a retained message)
    as events named after the topic; the health event and the logs beside them."""
    config = {
        "brokers": [
            {
                "name": "plant",
                "host": "127.0.0.1",
                "port": demo_broker.port,
                "device_map_file": DEMO_MAP,
            }
        ],
        "advanced": {"prefix": prefix},
    }
    _, recorded = run_extension(tmp_path, config, stop=stop)
    assert set(recorded) == expected
    base = "MQTT/plant" if prefix else "plant"
    temps = [t for t in recorded[f"{base}/environment"]["ambient_temp"] if t is not None]
    assert len(temps) >= 10 and all(15 < t < 30 for t in temps)
    status = recorded[f"{base}/device/status"]
    assert set(status) == {"mode", "fan_rpm", "fan_on", "time_ns"} and len(status["mode"]) >= 10
    # amqtt sends a retained message twice to a later client; Mosquitto once (test_interop).
    assert set(recorded[f"{base}/device/info"]["model"]) == {"SIM-1"}
    assert recorded[f"{base}/_broker"]["connected"][0] is True
    assert recorded[f"{base}/_broker"]["connected"][-1] is False


def test_demo_mode_traces_the_built_in_broker_with_a_clean_log(tmp_path):
    """`demo` ignores the configured brokers; its broker and the client log no warnings."""
    config = {"brokers": [{"host": "127.0.0.1", "port": free_port()}]}
    output, recorded = run_extension(tmp_path, config, "demo")
    assert {f"MQTT/demo/{e}" for e in EVENTS} | {"MQTT/log"} == set(recorded)
    assert [line for line in output.splitlines() if " WARN" in line or " ERROR" in line] == []
