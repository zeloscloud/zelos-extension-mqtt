"""Interop against Mosquitto (an independent broker) in Docker: plain, TLS, mutual TLS.

Manual, pre-release: `just e2e-interop` (needs docker; host ports 18831-18834 free).
Messages are published with the container's own mosquitto_pub.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import zelos_sdk

from zelos_extension_mqtt import actions
from zelos_extension_mqtt.client import BrokerClient, MqttRunner, tls_context

from .conftest import make_pki, problems, read_trace, wait_until

pytestmark = pytest.mark.skipif(
    os.environ.get("ZELOS_INTEROP") != "1" or not shutil.which("docker"),
    reason="Mosquitto interop: ZELOS_INTEROP=1 and docker",
)

IMAGE = "eclipse-mosquitto:2"
PLAIN, TLS, MTLS, LOGIN = 18831, 18832, 18833, 18834
CONFIG = """per_listener_settings true
listener 1883
allow_anonymous true
listener 8883
allow_anonymous true
cafile /mosquitto/config/ca.pem
certfile /mosquitto/config/server.pem
keyfile /mosquitto/config/server.key
listener 8884
allow_anonymous true
cafile /mosquitto/config/ca.pem
certfile /mosquitto/config/server.pem
keyfile /mosquitto/config/server.key
require_certificate true
listener 1884
allow_anonymous false
"""


def pub(name: str, topic: str, payload: str, retain: bool = False) -> None:
    flags = ["-r"] if retain else []
    subprocess.run(
        ["docker", "exec", name, "mosquitto_pub", "-p", "1883", "-q", "1", "-t", topic,
         "-m", payload, *flags],
        check=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def mosquitto(tmp_path_factory) -> tuple[str, dict[str, str]]:
    conf = tmp_path_factory.mktemp("mosquitto")
    pki = make_pki(conf)
    for path in pki.values():
        Path(path).chmod(0o644)  # the container's mosquitto user reads them
    (conf / "mosquitto.conf").write_text(CONFIG)
    name = f"zelos-mqtt-interop-{os.getpid()}"
    ports = [f"-p{h}:{c}" for h, c in ((PLAIN, 1883), (TLS, 8883), (MTLS, 8884), (LOGIN, 1884))]
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, *ports, "-v", f"{conf}:/mosquitto/config",
         IMAGE],
        check=True,
        capture_output=True,
    )  # fmt: skip
    time.sleep(1.5)
    yield name, pki
    subprocess.run(["docker", "stop", name], capture_output=True)


async def test_plain_tls_and_mutual_tls_trace_mosquitto(tmp_path, mosquitto):
    name, pki = mosquitto
    pub(name, "interop/retained", "7", retain=True)
    clients = [
        BrokerClient("plain", "localhost", PLAIN, ["interop/#"]),
        BrokerClient("tls", "localhost", TLS, ["interop/#"], tls=tls_context(pki["ca"])),
        BrokerClient(
            "mtls",
            "localhost",
            MTLS,
            ["interop/#"],
            tls=tls_context(pki["ca"], pki["client_cert"], pki["client_key"]),
        ),
    ]
    trace = tmp_path / "interop.trz"
    with zelos_sdk.TraceWriter(str(trace)):
        shared = zelos_sdk.TraceSource("Interop")
        for c in clients:
            c.start(shared)
        runner = MqttRunner(clients)
        task = asyncio.create_task(runner._run_async())
        try:
            await wait_until(lambda: all(c.connected for c in clients))
            actions.set_runner(runner)
            sent = await asyncio.to_thread(actions.publish, "interop/ours", "1.5", 1, broker="mtls")
            assert sent["bytes"] == 3
            await asyncio.to_thread(pub, name, "interop/json", '{"a": {"b": 2}, "s": "ok"}')
            await wait_until(lambda: all(c.messages >= 3 for c in clients))
        finally:
            runner.stop()
            await asyncio.wait_for(task, 10)
    pub(name, "interop/retained", "", retain=True)
    recorded = read_trace(trace)
    for c in ("plain", "tls", "mtls"):
        assert recorded[f"Interop/{c}/interop/retained"]["value"] == [7.0]  # retained: once
        assert recorded[f"Interop/{c}/interop/ours"]["value"] == [1.5]
        assert recorded[f"Interop/{c}/interop/json"]["a_b"] == [2.0]
        assert recorded[f"Interop/{c}/interop/json"]["s"] == ["ok"]


@pytest.mark.parametrize(
    ("port", "tls", "reason"),
    [
        (MTLS, "ca", "the broker requires a client certificate; set Client Certificate and "),
        (TLS, "", "TLS certificate not accepted: "),  # the test CA is not in the system store
        (LOGIN, None, "the broker refused the connection: Not authorized; this extension logs "),
        (TLS, None, "no answer to MQTT CONNECT; check Port, and turn on TLS for a TLS port"),
    ],
)
async def test_mosquitto_refusals_are_one_error(mosquitto, caplog, port, tls, reason):
    _, pki = mosquitto
    context = None if tls is None else tls_context(pki["ca"] if tls else "")
    client = BrokerClient("refused", "localhost", port, tls=context)
    client.start(None)
    runner = MqttRunner([client])
    await asyncio.wait_for(runner._run_async(), 20)
    [error] = problems(caplog)
    assert runner.failed_at_start == [client] and reason in error, error


def test_auto_config_checks_mosquitto(mosquitto):
    _, pki = mosquitto
    form = [
        {"name": "plain", "host": "localhost", "port": PLAIN},
        {"name": "tls", "host": "localhost", "port": TLS, "tls": True},
        {
            "name": "mtls",
            "host": "localhost",
            "port": MTLS,
            "tls": True,
            "cert_file": pki["client_cert"],
            "key_file": pki["client_key"],
        },
        {"name": "login", "host": "localhost", "port": LOGIN},
    ]
    result = actions.auto_config({"brokers": form, "advanced": {"ca_file": pki["ca"]}})
    assert result["status"] == "success", result
    assert result["message"].startswith(
        f"Connected to plain at localhost:{PLAIN} (plain); tls at localhost:{TLS} (TLS); "
        f"mtls at localhost:{MTLS} (TLS). Couldn't connect to login at localhost:{LOGIN} "
        "(plain): the broker refused the connection: Not authorized"
    )
