"""Brokers end to end against the in-process demo broker, recorded and read back."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiomqtt
import pytest
import zelos_sdk

from zelos_extension_mqtt import actions
from zelos_extension_mqtt import client as client_mod
from zelos_extension_mqtt.client import SHUTDOWN_TIMEOUT, BrokerClient, MqttRunner, node_value
from zelos_extension_mqtt.demo.simulator import DemoServer
from zelos_extension_mqtt.mqtt_map import NodeMap

from .conftest import free_port, problems, read_trace, wait_until


@contextlib.asynccontextmanager
async def running(*clients: BrokerClient, prefix: str, retry: bool = False):
    """Clients on the prefix's layout, run on this test's loop until the block ends."""
    shared = zelos_sdk.TraceSource(prefix) if prefix else None
    for c in clients:
        c.start(shared)
    runner = MqttRunner(clients, retry)
    task = asyncio.create_task(runner._run_async())
    try:
        if not retry:
            await wait_until(lambda: all(c.connected for c in clients) or task.done())
        yield runner
    finally:
        runner.stop()
        await asyncio.wait_for(task, 10.0)


async def publish(port: int, *messages: tuple[str, bytes | str], retain: bool = False) -> None:
    async with aiomqtt.Client("127.0.0.1", port) as pub:
        for topic, payload in messages:
            await pub.publish(topic, payload, qos=1, retain=retain)


async def test_every_message_is_traced_at_its_receive_time(tmp_path, demo_broker):
    """No last-value flush: a burst of 50 on a (wildcard) mapped and an unmapped topic is
    50 rows each."""
    node_map = NodeMap.from_dict({"events": {"env": [{"topic": "burst/+/temp", "name": "temp"}]}})
    plant = BrokerClient("plant", "127.0.0.1", demo_broker.port, ["burst/#"], node_map)
    trace = tmp_path / "burst.trz"
    with zelos_sdk.TraceWriter(str(trace)):
        async with running(plant, prefix="Burst"):
            await publish(
                demo_broker.port,
                *[(t, str(i)) for i in range(1, 51) for t in ("burst/a/temp", "burst/raw")],
            )
            await wait_until(lambda: plant.messages >= 100)
    recorded = read_trace(trace)
    assert recorded["Burst/plant/env"]["temp"] == [float(i) for i in range(1, 51)]
    assert recorded["Burst/plant/burst/raw"]["value"] == [float(i) for i in range(1, 51)]
    stamps = recorded["Burst/plant/burst/raw"]["time_ns"]
    assert stamps == sorted(stamps) and stamps[-1] - stamps[0] < 5e9


async def test_unmapped_topics_trace_as_events_named_after_them(tmp_path, demo_broker):
    """Cleared prefix: the broker's own source. A JSON object is one field per leaf, nested
    keys joined with `_`; anything else is `value`. Retained messages trace on subscribe."""
    port = demo_broker.port
    await publish(port, ("lab/retained", "21.5"), retain=True)
    lab = BrokerClient("lab_b", "127.0.0.1", port, ["lab/#"])
    payload = {"a": 1, "b": {"c": True}, "d": [1, 2], "n": None}
    trace = tmp_path / "discovery.trz"
    with zelos_sdk.TraceWriter(str(trace)):
        async with running(lab, prefix="") as runner:
            actions.set_runner(runner)
            await publish(port, ("lab/x.y/z", json.dumps(payload)), ("lab/text", "on"))
            await wait_until(lambda: lab.messages >= 3)
            listed = (await asyncio.to_thread(actions.list_topics))["brokers"]
    await publish(port, ("lab/retained", ""), retain=True)  # clear it for the other tests

    recorded = read_trace(trace)
    assert {k: v for k, v in recorded["lab_b/lab/x_y/z"].items() if k != "time_ns"} == {
        "a": [1.0],
        "b_c": [True],
        "d": ["[1, 2]"],
    }
    assert recorded["lab_b/lab/text"]["value"] == ["on"]
    assert recorded["lab_b/lab/retained"]["value"] == [21.5]
    assert recorded["lab_b/_broker"]["connected"] == [True, False]

    # list_topics hands back a device map that reads the same fields from the same payload.
    [entry] = listed
    topics = {t["topic"]: t for t in entry["topics"]}
    assert topics["lab/x.y/z"] == {
        "topic": "lab/x.y/z",
        "event": "lab/x_y/z",
        "mapped": False,
        "messages": 1,
        "fields": {"a": "float64", "b_c": "bool", "d": "string"},
    }
    saved = NodeMap.from_dict(json.loads(json.dumps(entry["device_map"])))
    nodes = saved.match("lab/x.y/z")
    assert {e for e, _ in nodes} == {"lab/x_y/z"}
    assert {n.name: node_value(json.dumps(payload), n) for _, n in nodes} == {
        "a": 1.0,
        "b_c": True,
        "d": "[1, 2]",
    }


async def test_decode_failures_warn_once_per_topic_and_tracing_goes_on(
    tmp_path, demo_broker, caplog
):
    node_map = NodeMap.from_dict(
        {"events": {"tank": [{"topic": "bad/level", "name": "level", "datatype": "uint8"}]}}
    )
    bad = BrokerClient("bad", "127.0.0.1", demo_broker.port, ["bad/#"], node_map)
    trace = tmp_path / "bad.trz"
    with zelos_sdk.TraceWriter(str(trace)):
        async with running(bad, prefix="Bad"):
            await publish(
                demo_broker.port,
                *[("bad/level", "300")] * 2,
                ("bad/level", "7"),
                ("bad/shape", '{"a": 1}'),
                *[("bad/shape", '{"a": 2, "b": 3}')] * 2,
                ("bad/shape", '{"a": "x"}'),
                *[("bad/bin", b"\xff\xfe")] * 2,
            )
            await wait_until(lambda: bad.messages >= 9)
    assert problems(caplog) == [
        "[bad] Topic 'bad/level': tank.level not recorded: 300 is out of range for uint8; "
        "warned once",
        "[bad] Topic 'bad/shape': field 'b' was not in its first message, which fixes the "
        "event's fields; not traced (list_topics includes it)",
        "[bad] Topic 'bad/shape': field 'a' not recorded: \"x\" is not float64; warned once",
        "[bad] Topic 'bad/bin': payload is not UTF-8 text; not traced",
    ]
    recorded = read_trace(trace)
    assert recorded["Bad/bad/tank"]["level"] == [7]
    assert recorded["Bad/bad/bad/shape"]["a"] == [1.0, 2.0, 2.0]


async def test_actions_select_the_broker_publish_subscribe_and_stop_in_bounds(demo_broker):
    port = demo_broker.port
    a = BrokerClient("a", "127.0.0.1", port, ["sensors/#"])
    b = BrokerClient("b", "127.0.0.1", port, ["device/#"])
    async with running(a, b, prefix="Actions") as runner:
        actions.set_runner(runner)
        call = asyncio.to_thread
        assert (await call(actions.get_status))["count"] == 2
        assert (await call(actions.get_status, "b"))["subscriptions"] == ["device/#"]
        with pytest.raises(ValueError, match="set broker to one of: a, b"):
            await call(actions.publish, "act/ping", "1")
        with pytest.raises(ValueError, match="Unknown broker 'c'. Brokers: a, b"):
            await call(actions.get_status, "c")
        with pytest.raises(ValueError, match="no \\+ or #"):
            await call(actions.publish, "act/#", "1", broker="a")
        with pytest.raises(ValueError, match="'\\+/ping' and 'sensors/#' both match"):
            await call(actions.subscribe, "+/ping", broker="a")

        subscribed = await call(actions.subscribe, "act/#", broker="a")
        assert subscribed["subscriptions"] == ["sensors/#", "act/#"]
        covered = await call(actions.subscribe, "act/+/x", 1, broker="a")
        assert covered["subscriptions"] == ["sensors/#", "act/#"]
        sent = await call(actions.publish, "act/ping", '{"v": 1}', 1, True, broker="a")
        assert sent == {"broker": "a", "topic": "act/ping", "qos": 1, "retain": True, "bytes": 8}
        await wait_until(lambda: "act/ping" in {t["topic"] for t in a.topics()})
        assert a.status()["published"] == 1 and "act/ping" not in {t["topic"] for t in b.topics()}
        await publish(port, ("act/ping", ""), retain=True)

        started = time.monotonic()
    elapsed = time.monotonic() - started
    assert elapsed < SHUTDOWN_TIMEOUT and not a.connected and not b.connected
    with pytest.raises(RuntimeError, match="extension is not running"):
        await asyncio.to_thread(actions.publish, "act/ping", "1", broker="a")


async def test_unreachable_at_start_stops_the_extension(demo_broker, caplog):
    """First contact is required: one ERROR naming the broker and the fix."""
    gone_port = free_port()
    live = BrokerClient("live", "127.0.0.1", demo_broker.port, ["sensors/#"])
    gone = BrokerClient("gone", "127.0.0.1", gone_port)
    runner = MqttRunner([live, gone])
    for c in (live, gone):
        c.start(None)
    await asyncio.wait_for(runner._run_async(), 10.0)
    assert runner.failed_at_start == [gone]
    assert problems(caplog) == [
        f"Broker 'gone' (127.0.0.1:{gone_port}): cannot connect: connection refused; "
        "check Host and Port, then start the extension again"
    ]


async def test_retry_unreachable_at_start_connects_later_and_reconnects(monkeypatch, caplog):
    monkeypatch.setattr(client_mod, "RECONNECT_INITIAL", 0.2)
    monkeypatch.setattr(client_mod, "RECONNECT_MAX", 0.4)
    port = free_port()
    late = BrokerClient("late", "127.0.0.1", port, ["sensors/#"])
    async with running(late, prefix="Retry", retry=True) as runner:
        actions.set_runner(runner)
        await wait_until(lambda: late.contacted.is_set())
        with pytest.raises(RuntimeError, match="Broker 'late' is not connected: connection"):
            await asyncio.to_thread(actions.publish, "x", "1")
        server = DemoServer(port=port, publish_interval=0.1)
        server.start()
        try:
            await wait_until(lambda: late.connected and late.messages > 0)
        finally:
            server.stop()
        await wait_until(lambda: late.state == "disconnected")
        server = DemoServer(port=port, publish_interval=0.1)
        server.start()
        try:
            await wait_until(lambda: late.connected)
            assert late.reconnects == 1
        finally:
            server.stop()
    assert problems(caplog)[0] == (
        f"Broker 'late' (127.0.0.1:{port}): cannot connect: connection refused; "
        "check Host and Port; retrying"
    )
