"""Tests for Zelos MQTT extension.

Tests core functionality:
- Node map parsing and validation
- Payload parsing and type conversion
- Simulator physics
- Integration tests with real MQTT broker
"""

import asyncio
import inspect
import json
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiomqtt
import pytest

from zelos_extension_mqtt.client import MqttClient
from zelos_extension_mqtt.demo.simulator import DemoServer, IoTSimulator
from zelos_extension_mqtt.mqtt_map import Node, NodeMap

DEMO_DEVICE_PATH = (
    Path(__file__).parent.parent / "zelos_extension_mqtt" / "demo" / "demo_device.json"
)

# =============================================================================
# Node & NodeMap Tests
# =============================================================================


class TestNode:
    """Test Node dataclass validation."""

    def test_defaults(self):
        """Minimal required fields use sensible defaults."""
        node = Node(topic="sensors/temp", name="temp")
        assert node.datatype == "float32"
        assert node.unit == ""
        assert node.qos == 0
        assert node.payload_type == "auto"
        assert node.json_key == "value"

    def test_all_valid_datatypes(self):
        """All supported datatypes are accepted."""
        for dt in [
            "bool",
            "uint8",
            "int8",
            "uint16",
            "int16",
            "uint32",
            "int32",
            "float32",
            "uint64",
            "int64",
            "float64",
            "string",
        ]:
            node = Node(topic="t", name="n", datatype=dt)
            assert node.datatype == dt

    def test_invalid_datatype_raises(self):
        """Invalid datatype raises ValueError."""
        with pytest.raises(ValueError, match="Invalid datatype"):
            Node(topic="t", name="n", datatype="complex128")

    def test_invalid_qos_raises(self):
        """Invalid QoS raises ValueError."""
        with pytest.raises(ValueError, match="Invalid QoS"):
            Node(topic="t", name="n", qos=3)

    def test_invalid_payload_type_raises(self):
        """Invalid payload_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid payload_type"):
            Node(topic="t", name="n", payload_type="xml")


class TestNodeMap:
    """Test NodeMap parsing and lookup."""

    def test_from_dict_creates_events(self):
        """Events are correctly parsed from dict."""
        data = {
            "name": "test_device",
            "events": {
                "temperature": [{"topic": "sensors/temp", "name": "ambient", "unit": "C"}],
                "power": [
                    {"topic": "sensors/voltage", "name": "voltage", "datatype": "float32"},
                    {"topic": "sensors/current", "name": "current"},
                ],
            },
        }
        nmap = NodeMap.from_dict(data)
        assert nmap.name == "test_device"
        assert len(nmap.events) == 2
        assert len(nmap.events["temperature"]) == 1
        assert len(nmap.events["power"]) == 2

    def test_log_event_name_is_reserved(self):
        with pytest.raises(ValueError, match="'log' is reserved"):
            NodeMap.from_dict({"events": {"log": [{"topic": "a/b", "name": "x"}]}})

    def test_from_file(self):
        """Node map loads correctly from demo device JSON."""
        nmap = NodeMap.from_file(DEMO_DEVICE_PATH)
        assert nmap.name == "iot_gateway"
        assert len(nmap.events) == 3
        assert len(nmap.nodes) == 10

    def test_nodes_property(self):
        """All nodes are flattened correctly."""
        data = {
            "events": {
                "a": [{"topic": "t1", "name": "n1"}],
                "b": [{"topic": "t2", "name": "n2"}, {"topic": "t3", "name": "n3"}],
            }
        }
        nmap = NodeMap.from_dict(data)
        assert len(nmap.nodes) == 3

    def test_from_file_nonexistent_raises(self):
        """Loading from nonexistent file raises."""
        with pytest.raises(FileNotFoundError):
            NodeMap.from_file("/nonexistent/path.json")

    def test_from_dict_empty(self):
        """Empty dict creates empty map."""
        nmap = NodeMap.from_dict({})
        assert nmap.name == ""
        assert len(nmap.nodes) == 0

    def test_from_dict_with_all_fields(self):
        """All node fields are parsed correctly."""
        data = {
            "events": {
                "test": [
                    {
                        "topic": "t",
                        "name": "n",
                        "datatype": "uint32",
                        "unit": "s",
                        "qos": 1,
                        "payload_type": "json",
                        "json_key": "data",
                    }
                ]
            }
        }
        nmap = NodeMap.from_dict(data)
        node = nmap.nodes[0]
        assert node.datatype == "uint32"
        assert node.unit == "s"
        assert node.qos == 1
        assert node.payload_type == "json"
        assert node.json_key == "data"


# =============================================================================
# Payload Parsing Tests
# =============================================================================


class TestPayloadParsing:
    """Test MqttClient._parse_payload and _convert_value."""

    def _make_client(self):
        with patch("zelos_sdk.TraceSourceCacheLast"):
            return MqttClient(node_map=NodeMap())

    def test_parse_json_number(self):
        """Plain JSON number parses correctly."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float32")
        assert client._parse_payload(b"23.5", node) == 23.5

    def test_parse_json_integer(self):
        """JSON integer parses as int for integer datatypes."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="uint32")
        assert client._parse_payload(b"42", node) == 42

    def test_parse_json_object_auto(self):
        """JSON object in auto mode extracts value key."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float32", json_key="value")
        payload = json.dumps({"value": 99.9, "unit": "C"}).encode()
        assert client._parse_payload(payload, node) == 99.9

    def test_parse_json_object_custom_key(self):
        """Custom json_key extracts the right field."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float32", json_key="temp")
        payload = json.dumps({"temp": 22.1}).encode()
        assert client._parse_payload(payload, node) == 22.1

    def test_parse_json_object_missing_key(self):
        """Missing key in JSON object returns None."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float32", json_key="missing")
        payload = json.dumps({"value": 1.0}).encode()
        assert client._parse_payload(payload, node) is None

    def test_parse_raw_string(self):
        """Raw text payload with string datatype."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="string", payload_type="raw")
        assert client._parse_payload(b"hello world", node) == "hello world"

    def test_parse_bool_true_variants(self):
        """Boolean parsing handles various true representations."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="bool")
        for val in [b"true", b"1", b"True"]:
            assert client._parse_payload(val, node) is True

    def test_parse_bool_false(self):
        """Boolean parsing handles false."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="bool")
        assert client._parse_payload(b"false", node) is False

    def test_parse_float_from_string(self):
        """Float value from string representation."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float64")
        assert client._parse_payload(b"3.14159", node) == pytest.approx(3.14159)

    def test_parse_int_from_float_string(self):
        """Integer datatype handles float string (truncates)."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="int32")
        assert client._parse_payload(b"23.7", node) == 23

    def test_parse_invalid_payload(self):
        """Invalid payload returns None gracefully."""
        client = self._make_client()
        node = Node(topic="t", name="n", datatype="float32")
        assert client._parse_payload(b"not_a_number", node) is None

    def test_convert_value_all_types(self):
        """Type conversion works for all datatypes."""
        assert MqttClient._convert_value(1.5, "float32") == 1.5
        assert MqttClient._convert_value(1.5, "float64") == 1.5
        assert MqttClient._convert_value(42, "uint32") == 42
        assert MqttClient._convert_value(-5, "int16") == -5
        assert MqttClient._convert_value(1, "bool") is True
        assert MqttClient._convert_value(0, "bool") is False
        assert MqttClient._convert_value(42, "string") == "42"
        assert MqttClient._convert_value(None, "float32") is None


# =============================================================================
# Simulator Tests
# =============================================================================


class TestIoTSimulator:
    """Test IoTSimulator value generation."""

    def test_update_returns_all_topics(self):
        """All expected topics are present in update output."""
        sim = IoTSimulator()
        values = sim.update()
        expected_topics = {
            "sensors/temperature/ambient",
            "sensors/temperature/surface",
            "sensors/humidity",
            "sensors/pressure",
            "sensors/power/voltage",
            "sensors/power/current",
            "sensors/power/watts",
            "device/uptime",
            "device/errors",
            "device/setpoint",
        }
        assert set(values.keys()) == expected_topics

    def test_temperature_range(self):
        """Temperature values are in reasonable range."""
        sim = IoTSimulator()
        for _ in range(50):
            values = sim.update()
            assert 10.0 < values["sensors/temperature/ambient"] < 35.0
            assert 15.0 < values["sensors/temperature/surface"] < 45.0

    def test_humidity_bounds(self):
        """Humidity stays within 20-95%."""
        sim = IoTSimulator()
        for _ in range(50):
            values = sim.update()
            assert 20.0 <= values["sensors/humidity"] <= 95.0

    def test_voltage_range(self):
        """Voltage stays near nominal 12V."""
        sim = IoTSimulator()
        for _ in range(50):
            values = sim.update()
            assert 10.0 < values["sensors/power/voltage"] < 14.0

    def test_uptime_increases(self):
        """Uptime increases monotonically."""
        sim = IoTSimulator()
        prev = 0
        for _ in range(10):
            time.sleep(0.01)
            values = sim.update()
            assert values["device/uptime"] >= prev
            prev = values["device/uptime"]

    def test_values_change_over_time(self):
        """Simulated values actually change between updates."""
        sim = IoTSimulator()
        v1 = sim.update()
        time.sleep(0.1)
        v2 = sim.update()
        # At least one analog value should differ (randomness + time)
        analog_keys = [
            "sensors/temperature/ambient",
            "sensors/humidity",
            "sensors/power/current",
        ]
        changed = any(v1[k] != v2[k] for k in analog_keys)
        assert changed, "No values changed between updates"


# =============================================================================
# Integration Tests with Real MQTT Broker
# =============================================================================


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def demo_server():
    """Start a real MQTT broker with simulator for integration tests."""
    port = _find_free_port()
    server = DemoServer(host="127.0.0.1", port=port, publish_interval=0.2)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def node_map():
    """Load demo device node map."""
    return NodeMap.from_file(DEMO_DEVICE_PATH)


@pytest.fixture
def client(demo_server, node_map):
    """Create a MqttClient connected to the demo broker."""
    with patch("zelos_sdk.TraceSourceCacheLast"), patch("zelos_sdk.TraceEventFieldMetadata"):
        return MqttClient(
            host=demo_server.host,
            port=demo_server.port,
            node_map=node_map,
            poll_interval=0.5,
        )


class TestDemoServerIntegration:
    """Integration tests using real MQTT broker."""

    def test_broker_logs_only_its_own_lines(self, caplog):
        """The embedded broker's connection and state machine lines stay out of the log."""
        import logging

        server = DemoServer(host="127.0.0.1", port=_find_free_port(), publish_interval=0.1)
        with caplog.at_level(logging.INFO):
            server.start()
            time.sleep(0.3)
            server.stop()
        assert {r.name for r in caplog.records} == {"zelos_extension_mqtt.demo.simulator"}

    @pytest.mark.asyncio
    async def test_broker_accepts_connections(self, demo_server):
        """Broker accepts an MQTT connection as soon as start() returns."""
        async with aiomqtt.Client(demo_server.host, demo_server.port):
            pass

    @pytest.mark.asyncio
    async def test_subscribe_and_receive(self, demo_server, node_map):
        """Client receives messages on subscribed topics."""
        received = {}

        async with aiomqtt.Client(demo_server.host, demo_server.port) as client:
            for node in node_map.nodes:
                await client.subscribe(node.topic)

            # Collect messages for a few seconds
            deadline = asyncio.get_running_loop().time() + 3.0
            async for message in client.messages:
                topic = str(message.topic)
                received[topic] = message.payload
                if len(received) >= len(node_map.nodes):
                    break
                if asyncio.get_running_loop().time() > deadline:
                    break

        # Should have received at least some messages
        assert len(received) > 0, "No messages received from broker"

    @pytest.mark.asyncio
    async def test_message_payloads_are_valid_json(self, demo_server, node_map):
        """All simulator messages contain valid JSON payloads."""
        async with aiomqtt.Client(demo_server.host, demo_server.port) as client:
            for node in node_map.nodes:
                await client.subscribe(node.topic)

            count = 0
            deadline = asyncio.get_running_loop().time() + 3.0
            async for message in client.messages:
                payload = message.payload.decode()
                value = json.loads(payload)  # Should not raise
                assert value is not None
                count += 1
                if count >= 5 or asyncio.get_running_loop().time() > deadline:
                    break

    @pytest.mark.asyncio
    async def test_publish_and_receive(self, demo_server):
        """Published message is received by subscriber."""
        test_topic = "test/roundtrip"
        test_payload = "hello_mqtt"
        received = None

        async with aiomqtt.Client(demo_server.host, demo_server.port) as client:
            await client.subscribe(test_topic)
            await client.publish(test_topic, test_payload.encode())

            deadline = asyncio.get_running_loop().time() + 3.0
            async for message in client.messages:
                if str(message.topic) == test_topic:
                    received = message.payload.decode()
                    break
                if asyncio.get_running_loop().time() > deadline:
                    break

        assert received == test_payload

    def test_client_handle_message(self, client):
        """MqttClient correctly parses and caches received messages."""
        # Simulate receiving a message
        mock_msg = MagicMock()
        mock_msg.topic = "sensors/temperature/ambient"
        mock_msg.payload = b"22.5"

        client._handle_message(mock_msg)

        assert ("environment", "ambient_temp") in client._cache
        assert client._cache[("environment", "ambient_temp")] == 22.5
        assert client._message_count == 1

    def test_client_handle_unknown_topic(self, client):
        """Unknown topics are silently ignored."""
        mock_msg = MagicMock()
        mock_msg.topic = "unknown/topic"
        mock_msg.payload = b"42"

        client._handle_message(mock_msg)
        assert client._message_count == 0

    def test_client_caches_multiple_values(self, client):
        """Multiple messages update the cache correctly."""
        topics_and_values = [
            ("sensors/temperature/ambient", b"21.0"),
            ("sensors/temperature/surface", b"28.5"),
            ("sensors/power/voltage", b"12.1"),
            ("device/uptime", b"3600"),
        ]

        for topic, payload in topics_and_values:
            mock_msg = MagicMock()
            mock_msg.topic = topic
            mock_msg.payload = payload
            client._handle_message(mock_msg)

        assert client._cache[("environment", "ambient_temp")] == 21.0
        assert client._cache[("environment", "surface_temp")] == 28.5
        assert client._cache[("power", "voltage")] == 12.1
        assert client._cache[("device", "uptime")] == 3600

    def test_client_overwrites_cached_value(self, client):
        """Newer messages overwrite older cached values."""
        for value in [b"20.0", b"21.0", b"22.0"]:
            mock_msg = MagicMock()
            mock_msg.topic = "sensors/temperature/ambient"
            mock_msg.payload = value
            client._handle_message(mock_msg)

        assert client._cache[("environment", "ambient_temp")] == 22.0

    @pytest.mark.asyncio
    async def test_full_subscribe_receive_cache_cycle(self, demo_server, node_map):
        """End-to-end: subscribe to broker, receive, parse, cache."""
        with patch("zelos_sdk.TraceSourceCacheLast"), patch("zelos_sdk.TraceEventFieldMetadata"):
            mqtt_client = MqttClient(
                host=demo_server.host,
                port=demo_server.port,
                node_map=node_map,
                poll_interval=0.5,
            )

        async with aiomqtt.Client(demo_server.host, demo_server.port) as client:
            for node in node_map.nodes:
                await client.subscribe(node.topic)

            deadline = asyncio.get_running_loop().time() + 3.0
            async for message in client.messages:
                mqtt_client._handle_message(message)
                if len(mqtt_client._cache) >= 5:
                    break
                if asyncio.get_running_loop().time() > deadline:
                    break

        assert len(mqtt_client._cache) >= 5, f"Only cached {len(mqtt_client._cache)} values"

        nodes_by_name = {node.name: node for node in node_map.nodes}
        for (_event_name, node_name), value in mqtt_client._cache.items():
            node = nodes_by_name[node_name]
            if "float" in node.datatype:
                assert isinstance(value, float), f"{node_name} should be float, got {type(value)}"
            elif "int" in node.datatype:
                assert isinstance(value, int), f"{node_name} should be int, got {type(value)}"


# =============================================================================
# Action Tests
# =============================================================================


class TestActions:
    """Test action methods."""

    def test_action_flags(self):
        """Pins each action's flags, so a new action or a changed flag is a deliberate choice."""
        actual = {
            name: (member._action.read_only, member._action.hidden)
            for name, member in inspect.getmembers(MqttClient, inspect.isfunction)
            if hasattr(member, "_action")
        }
        assert actual == {
            "get_status": (True, True),
            "publish_message": (False, False),
            "subscribe_topic": (False, False),
        }

    def test_get_status_disconnected(self, client):
        """Get status reports disconnected state."""
        status = client.get_status()
        assert status["connected"] is False
        assert "broker" in status
        assert status["subscriptions"] == 10  # demo device has 10 nodes

    def test_publish_when_disconnected(self, client):
        """Publish fails gracefully when not connected."""
        result = client.publish_message("test/topic", "hello")
        assert result["success"] is False
        assert "error" in result

    def test_subscribe_when_disconnected(self, client):
        """Subscribe fails gracefully when not connected."""
        result = client.subscribe_topic("test/#")
        assert result["success"] is False
        assert "error" in result


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_parse_non_utf8_payload(self):
        """Non-UTF8 payload returns None."""
        with patch("zelos_sdk.TraceSourceCacheLast"):
            client = MqttClient()
        node = Node(topic="t", name="n")
        # Invalid UTF-8 sequence - should handle gracefully
        client._parse_payload(b"\xff\xfe\x00\x01", node)

    def test_empty_node_map(self):
        """Client works with empty node map."""
        with patch("zelos_sdk.TraceSourceCacheLast"):
            client = MqttClient(node_map=NodeMap())
        assert len(client._topic_lookup) == 0
        assert client._message_count == 0


# =============================================================================
# Trace Logging Tests
# =============================================================================


class TestTraceLogging:
    """Test flushing cached values to a real trace source."""

    def test_string_node_logs_text_and_flush_continues(self):
        """A string node logs its text, and the flush still logs the events after it."""
        node_map = NodeMap.from_dict(
            {
                "name": "text_test",
                "events": {
                    "status": [{"topic": "device/state", "name": "state", "datatype": "string"}],
                    "power": [{"topic": "sensors/voltage", "name": "voltage"}],
                },
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()
        for topic, payload in [("device/state", b"running"), ("sensors/voltage", b"12.1")]:
            message = MagicMock()
            message.topic = topic
            message.payload = payload
            client._handle_message(message)

        client._log_cached_values()

        assert client.source.status.state.get() == "running"
        assert client.source.power.voltage.get() == pytest.approx(12.1)


class TestSharedTopics:
    """Test that every device map node on a received topic records the message."""

    def test_nodes_on_one_topic_all_record(self):
        """Two nodes that read different JSON keys of one topic both record."""
        node_map = NodeMap.from_dict(
            {
                "name": "shared_topic_test",
                "events": {
                    "env": [
                        {"topic": "sensors/env", "name": "temp", "json_key": "temp"},
                        {"topic": "sensors/env", "name": "humidity", "json_key": "humidity"},
                    ]
                },
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()
        message = MagicMock()
        message.topic = "sensors/env"
        message.payload = b'{"temp": 21.5, "humidity": 40}'

        client._handle_message(message)
        client._log_cached_values()

        assert client.source.env.temp.get() == pytest.approx(21.5)
        assert client.source.env.humidity.get() == pytest.approx(40)
        assert client._message_count == 1


# =============================================================================
# Wildcard Topic Tests
# =============================================================================


def _deliver(client: MqttClient, topic: str, payload: bytes) -> None:
    message = MagicMock()
    message.topic = topic
    message.payload = payload
    client._handle_message(message)


class TestWildcardTopics:
    """Test that topics with + and # wildcards record the messages they match."""

    def test_runtime_wildcard_records_matching_topics(self):
        """A topic added at runtime with + or # records each matching topic as its own event."""
        client = MqttClient(node_map=NodeMap.from_dict({"name": "runtime_wildcard_test"}))
        client.start()
        client._dynamic_topics.update({"lab/+/temp", "plant/#"})

        _deliver(client, "lab/bench/temp", b'{"celsius": 21.5}')
        _deliver(client, "plant/line1/speed", b"42")
        _deliver(client, "lab/bench/humidity", b"40")

        assert client.source.lab_bench_temp.celsius.get() == pytest.approx(21.5)
        assert client.source.plant_line1_speed.value.get() == 42
        assert getattr(client.source, "lab_bench_humidity", None) is None
        assert client._message_count == 2

    def test_configured_wildcard_records_matching_topics(self):
        """A device map topic with a wildcard records the latest message on any matching topic."""
        node_map = NodeMap.from_dict(
            {
                "name": "configured_wildcard_test",
                "events": {
                    "status": [{"topic": "devices/+/state", "name": "state", "datatype": "string"}],
                    "power": [{"topic": "sensors/voltage", "name": "voltage"}],
                },
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()

        _deliver(client, "devices/pump/state", b"running")
        _deliver(client, "sensors/voltage", b"12.1")
        _deliver(client, "devices/pump/state/extra", b"ignored")
        client._log_cached_values()

        assert client.source.status.state.get() == "running"
        assert client.source.power.voltage.get() == pytest.approx(12.1)
        assert client._message_count == 2

    def test_configured_topic_wins_over_runtime_wildcard(self):
        """A message on a device map topic records into its node, not a runtime wildcard event."""
        node_map = NodeMap.from_dict(
            {
                "name": "overlap_test",
                "events": {"power": [{"topic": "sensors/voltage", "name": "voltage"}]},
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()
        client._dynamic_topics.add("sensors/#")

        _deliver(client, "sensors/voltage", b"12.1")
        _deliver(client, "sensors/current", b"0.5")
        client._log_cached_values()

        assert client.source.power.voltage.get() == pytest.approx(12.1)
        assert getattr(client.source, "sensors_voltage", None) is None
        assert client.source.sensors_current.value.get() == pytest.approx(0.5)

    def test_runtime_topic_wins_over_configured_wildcard(self):
        """An exact runtime topic records as its own event, not into a matching wildcard node."""
        node_map = NodeMap.from_dict(
            {
                "name": "runtime_over_wildcard_test",
                "events": {"any": [{"topic": "sensors/+", "name": "reading"}]},
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()
        client._dynamic_topics.add("sensors/temp")

        _deliver(client, "sensors/temp", b"21.5")
        _deliver(client, "sensors/humidity", b"40")
        client._log_cached_values()

        assert client.source.sensors_temp.value.get() == pytest.approx(21.5)
        assert client.source.any.reading.get() == pytest.approx(40)

    def test_exact_and_wildcard_nodes_both_record(self):
        """An exact topic node and a wildcard node that match one message both record it."""
        node_map = NodeMap.from_dict(
            {
                "name": "exact_and_wildcard_test",
                "events": {
                    "pump": [
                        {"topic": "devices/pump/state", "name": "pump_state", "datatype": "string"}
                    ],
                    "any": [
                        {"topic": "devices/+/state", "name": "last_state", "datatype": "string"}
                    ],
                },
            }
        )
        client = MqttClient(node_map=node_map)
        client.start()

        _deliver(client, "devices/pump/state", b"running")
        client._log_cached_values()

        assert client.source.pump.pump_state.get() == "running"
        assert client.source.any.last_state.get() == "running"
        assert client._message_count == 1


class TestLogSource:
    """Test that the extension's logs share the client's trace source."""

    def test_app_mode_logs_on_the_client_source(self, monkeypatch):
        import logging
        from types import SimpleNamespace

        from zelos_sdk.hooks.logging import TraceLoggingHandler

        from zelos_extension_mqtt.cli import app

        clients: list[MqttClient] = []
        sdk = SimpleNamespace(
            actions_registry=SimpleNamespace(register=clients.append),
            init=lambda **_kwargs: None,
        )
        monkeypatch.setattr(app, "zelos_sdk", sdk)
        monkeypatch.setattr(app, "load_config", dict)
        monkeypatch.setattr(MqttClient, "run", lambda self: None)
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            app.run_app_mode()
            added = [h for h in root.handlers if h not in before]
        finally:
            for handler in [h for h in root.handlers if h not in before]:
                root.removeHandler(handler)
        assert [type(h) for h in added] == [TraceLoggingHandler]
        assert added[0].trace_source is clients[0].source

    def test_runtime_topic_named_log_is_not_traced(self):
        from zelos_sdk.hooks.logging import TraceLoggingHandler

        client = MqttClient(node_map=NodeMap.from_dict({"name": "log_topic_test"}))
        client.start()
        TraceLoggingHandler(client.source)
        client._dynamic_topics.add("#")

        _deliver(client, "log", b"1")
        _deliver(client, "logs", b"2")

        assert client.source.logs.value.get() == 2
        assert client._message_count == 1


def test_saved_config_off_the_schema_exits(tmp_path, monkeypatch, caplog):
    import logging

    from zelos_extension_mqtt.cli import app

    saved = tmp_path / "config.json"
    saved.write_text(json.dumps({"port": 0}))
    monkeypatch.setenv("ZELOS_CONFIG_PATH", str(saved))
    monkeypatch.chdir(Path(__file__).parents[1])
    with pytest.raises(SystemExit) as exc:
        app.run_app_mode()
    assert exc.value.code == 1
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
        "Invalid configuration: port: 0 is less than the minimum of 1"
    ]
