"""TLS against an in-process broker with a test CA: verification on, no plaintext fallback."""

from __future__ import annotations

import asyncio
import threading

import aiomqtt
import pytest
from amqtt.broker import Broker

from zelos_extension_mqtt.client import BrokerClient, MqttRunner, tls_context

from .conftest import free_port, make_pki, problems, wait_until


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> dict[str, str]:
    return make_pki(tmp_path_factory.mktemp("pki"))


@pytest.fixture(scope="module")
def tls_broker(pki) -> int:
    """A TLS-only broker on localhost serving the test CA's certificate; yields its port."""
    port, ready, stop = free_port(), threading.Event(), None
    listener = {
        "type": "tcp",
        "bind": f"127.0.0.1:{port}",
        "ssl": True,
        "certfile": pki["server_cert"],
        "keyfile": pki["server_key"],
        "cafile": pki["ca"],
    }
    config = {
        "listeners": {"default": listener},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    }

    async def serve() -> None:
        nonlocal stop
        stop = asyncio.Event()
        broker = Broker(config)
        await broker.start()
        ready.set()
        await stop.wait()
        await broker.shutdown()

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(serve(),), daemon=True)
    thread.start()
    assert ready.wait(10)
    yield port
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)


async def test_tls_with_the_ca_file_verifies_the_broker_and_traces(pki, tls_broker):
    """CA file plus a client certificate (mutual TLS where the broker asks for one)."""
    secure = tls_context(pki["ca"], pki["client_cert"], pki["client_key"])
    client = BrokerClient("secure", "localhost", tls_broker, ["tls/#"], tls=secure)
    client.start(None)
    runner = MqttRunner([client])
    task = asyncio.create_task(runner._run_async())
    try:
        await wait_until(lambda: client.connected)
        async with aiomqtt.Client("localhost", tls_broker, tls_context=tls_context(pki["ca"])) as p:
            await p.publish("tls/temp", "21.5", qos=1)
        await wait_until(lambda: client.messages == 1)
        assert client.status()["tls"] is True
        assert client.topics()[0]["fields"] == {"value": "float64"}
    finally:
        runner.stop()
        await asyncio.wait_for(task, 10)


@pytest.mark.parametrize(
    ("host", "ca", "reason", "fix"),
    [
        (
            "localhost",
            False,
            "TLS certificate not accepted: ",  # how OpenSSL names it depends on the chain sent
            "set CA File to the CA that signed the broker's certificate",
        ),
        (
            "127.0.0.1",
            True,
            "TLS certificate not accepted: IP address mismatch, certificate is not valid for "
            "'127.0.0.1'.",
            "set Host to a name the broker's certificate lists",
        ),
    ],
)
async def test_an_unverified_broker_stops_the_start_and_never_falls_back(
    pki, tls_broker, caplog, host, ca, reason, fix
):
    """System trust store without the test CA, or a host the certificate does not name:
    one ERROR, and the client never connects in plain text."""
    client = BrokerClient("secure", host, tls_broker, tls=tls_context(pki["ca"] if ca else ""))
    client.start(None)
    runner = MqttRunner([client])
    await asyncio.wait_for(runner._run_async(), 15)
    assert runner.failed_at_start == [client] and client.messages == 0
    [error] = problems(caplog)
    assert error.startswith(f"Broker 'secure' ({host}:{tls_broker}, TLS): cannot connect: {reason}")
    assert error.endswith(f"; {fix}, then start the extension again")
