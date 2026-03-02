"""MQTT client with Zelos SDK trace integration.

Connects to an MQTT broker, subscribes to topics defined in a node map,
caches received values, and periodically logs them to Zelos trace events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import aiomqtt
import zelos_sdk

from zelos_extension_mqtt.mqtt_map import Node, NodeMap

logger = logging.getLogger(__name__)

SDK_DATATYPE_MAP = {
    "bool": zelos_sdk.DataType.Boolean,
    "uint8": zelos_sdk.DataType.UInt8,
    "int8": zelos_sdk.DataType.Int8,
    "uint16": zelos_sdk.DataType.UInt16,
    "int16": zelos_sdk.DataType.Int16,
    "uint32": zelos_sdk.DataType.UInt32,
    "int32": zelos_sdk.DataType.Int32,
    "float32": zelos_sdk.DataType.Float32,
    "uint64": zelos_sdk.DataType.UInt64,
    "int64": zelos_sdk.DataType.Int64,
    "float64": zelos_sdk.DataType.Float64,
}


class MqttClient:
    """MQTT client that logs received messages as Zelos trace events.

    Subscribes to topics from a NodeMap, caches the latest value per topic,
    and periodically flushes cached values to the Zelos trace source.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        node_map: NodeMap | None = None,
        poll_interval: float = 1.0,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.node_map = node_map or NodeMap()
        self.poll_interval = poll_interval
        self.client_id = client_id
        self.username = username
        self.password = password

        self._connected = False
        self._running = False
        self._mqtt_client: aiomqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._source: zelos_sdk.TraceSourceCacheLast | None = None

        # Value cache: (event_name, node_name) -> value
        self._cache: dict[tuple[str, str], Any] = {}
        # Lookup: topic -> (event_name, node)
        self._topic_lookup: dict[str, tuple[str, Node]] = {}
        # Topics subscribed at runtime (not in node map); traced via log_dict
        self._dynamic_topics: set[str] = set()

        self._message_count = 0
        self._error_count = 0
        self._poll_count = 0

        self._build_topic_lookup()

    def _build_topic_lookup(self) -> None:
        """Build topic -> (event_name, node) lookup from node map."""
        self._topic_lookup = {}
        for event_name, nodes in self.node_map.events.items():
            for node in nodes:
                self._topic_lookup[node.topic] = (event_name, node)

    def _init_trace_source(self) -> None:
        """Create Zelos trace source from node map."""
        source_name = self.node_map.name if self.node_map.name else "mqtt"
        self._source = zelos_sdk.TraceSourceCacheLast(source_name)

        if not self.node_map.events:
            self._source.add_event(
                "raw",
                [
                    zelos_sdk.TraceEventFieldMetadata("topic", zelos_sdk.DataType.String),
                    zelos_sdk.TraceEventFieldMetadata("payload", zelos_sdk.DataType.String),
                ],
            )
            return

        for event_name, nodes in self.node_map.events.items():
            if not nodes:
                continue
            fields = []
            for node in nodes:
                dtype = SDK_DATATYPE_MAP.get(node.datatype, zelos_sdk.DataType.Float32)
                fields.append(zelos_sdk.TraceEventFieldMetadata(node.name, dtype, node.unit))
            self._source.add_event(event_name, fields)

    def _parse_payload(self, payload: bytes, node: Node) -> Any:
        """Parse MQTT message payload into the correct Python type."""
        try:
            text = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            logger.warning(f"Non-UTF8 payload on {node.topic}, skipping")
            return None

        try:
            if node.payload_type == "json" or (
                node.payload_type == "auto" and text.startswith("{")
            ):
                data = json.loads(text)
                if isinstance(data, dict):
                    value = data.get(node.json_key)
                    if value is None:
                        logger.debug(f"Key {node.json_key!r} not found in JSON on {node.topic}")
                        return None
                else:
                    value = data
            else:
                # Try to parse as JSON number/bool first (handles "23.5", "true")
                try:
                    value = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    value = text

            return self._convert_value(value, node.datatype)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse payload on {node.topic}: {e}")
            return None

    @staticmethod
    def _convert_value(value: Any, datatype: str) -> Any:
        """Convert a parsed value to the correct Python type for the datatype."""
        if value is None:
            return None
        if datatype == "bool":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "on", "yes")
            return bool(value)
        if datatype == "string":
            return str(value)
        if "float" in datatype:
            return float(value)
        if "int" in datatype or "uint" in datatype:
            return int(float(value))
        return value

    def _handle_message(self, message: aiomqtt.Message) -> None:
        """Process an incoming MQTT message and cache its value."""
        topic = str(message.topic)

        # Dynamic topics: log directly via log_dict with schema inference
        if topic in self._dynamic_topics:
            self._handle_dynamic_message(topic, message.payload)
            return

        lookup = self._topic_lookup.get(topic)
        if not lookup:
            return

        event_name, node = lookup
        value = self._parse_payload(message.payload, node)
        if value is not None:
            self._cache[(event_name, node.name)] = value
            self._message_count += 1

    def _handle_dynamic_message(self, topic: str, payload: bytes) -> None:
        """Handle a message from a dynamically subscribed topic.

        Parses the payload as JSON and logs it via log_dict, which infers the
        schema from the dict keys/values on first call.
        """
        try:
            text = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return

        # Use topic path as event name (slashes replaced for compatibility)
        event_name = topic.replace("/", "_")

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                data = {"value": data}
        except (json.JSONDecodeError, ValueError):
            data = {"value": text}

        if self._source:
            self._source.log_dict(event_name, data)
        self._message_count += 1

    def _log_cached_values(self) -> None:
        """Flush cached values to the Zelos trace source."""
        if not self._source or not self._cache:
            return

        for event_name, nodes in self.node_map.events.items():
            event = getattr(self._source, event_name, None)
            if not event:
                continue
            values = {}
            for node in nodes:
                key = (event_name, node.name)
                if key in self._cache:
                    values[node.name] = self._cache[key]
            if values:
                event.log(**values)

        self._poll_count += 1
        if self._poll_count % 10 == 0:
            logger.debug(f"Poll #{self._poll_count}, messages received: {self._message_count}")

    async def connect(self) -> bool:
        """Test connectivity to the MQTT broker.

        Returns True if a connection can be established.
        """
        try:
            async with aiomqtt.Client(
                self.host,
                self.port,
                username=self.username,
                password=self.password,
            ):
                return True
        except (aiomqtt.MqttError, OSError) as e:
            logger.error(f"Connection test failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Signal the client to stop. Actual cleanup happens in _run_async."""
        self._running = False

    async def _subscribe_all(self, client: aiomqtt.Client) -> None:
        """Subscribe to all topics in the node map."""
        for node in self.node_map.nodes:
            await client.subscribe(node.topic, qos=node.qos)
            logger.debug(f"Subscribed to {node.topic} (QoS {node.qos})")
        logger.info(f"Subscribed to {len(self.node_map.nodes)} topics")

    async def _run_async(self) -> None:
        """Main loop: connect, subscribe, receive messages, log periodically."""
        self._loop = asyncio.get_running_loop()
        reconnect_interval = 3.0

        # Start periodic logging task
        log_task = asyncio.create_task(self._periodic_log())

        try:
            while self._running:
                try:
                    async with aiomqtt.Client(
                        self.host,
                        self.port,
                        identifier=self.client_id,
                        username=self.username,
                        password=self.password,
                    ) as client:
                        self._mqtt_client = client
                        self._connected = True
                        logger.info(f"Connected to MQTT broker at {self.host}:{self.port}")

                        await self._subscribe_all(client)

                        async for message in client.messages:
                            self._handle_message(message)
                            if not self._running:
                                break

                except aiomqtt.MqttError as e:
                    self._error_count += 1
                    self._connected = False
                    self._mqtt_client = None
                    if self._running:
                        logger.warning(f"MQTT error: {e}, reconnecting in {reconnect_interval}s")
                        await asyncio.sleep(reconnect_interval)
                except OSError as e:
                    self._error_count += 1
                    self._connected = False
                    self._mqtt_client = None
                    if self._running:
                        logger.warning(
                            f"Connection refused: {e}, retrying in {reconnect_interval}s"
                        )
                        await asyncio.sleep(reconnect_interval)
        finally:
            log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await log_task
            self._connected = False
            self._mqtt_client = None

    async def _periodic_log(self) -> None:
        """Periodically flush cached values to the trace source."""
        while self._running:
            await asyncio.sleep(self.poll_interval)
            self._log_cached_values()

    def start(self) -> None:
        """Initialize trace source and mark as running."""
        self._running = True
        self._init_trace_source()
        logger.info("MqttClient started")

    def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False
        logger.info("MqttClient stopping")

    def run(self) -> None:
        """Run the client (blocking)."""
        asyncio.run(self._run_async())

    # ── Actions ──────────────────────────────────────────────────────────

    @zelos_sdk.action("Get Status", "View MQTT connection and message stats")
    def get_status(self) -> dict[str, Any]:
        """Get current client status."""
        return {
            "connected": self._connected,
            "broker": f"{self.host}:{self.port}",
            "subscriptions": len(self.node_map.nodes),
            "messages_received": self._message_count,
            "polls_logged": self._poll_count,
            "errors": self._error_count,
            "cached_values": len(self._cache),
        }

    @zelos_sdk.action("Publish Message", "Publish a message to an MQTT topic")
    @zelos_sdk.action.text("topic", title="Topic", placeholder="sensors/temperature/ambient")
    @zelos_sdk.action.text("payload", title="Payload", placeholder='{"value": 23.5}')
    @zelos_sdk.action.number("qos", minimum=0, maximum=2, default=0, title="QoS")
    def publish_message(self, topic: str, payload: str, qos: int = 0) -> dict[str, Any]:
        """Publish a message to an MQTT topic."""
        if not self._connected or not self._mqtt_client or not self._loop:
            return {"error": "Not connected to broker", "success": False}

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._mqtt_client.publish(topic, payload.encode(), qos=int(qos)),
                self._loop,
            )
            future.result(timeout=5.0)
            return {"status": "published", "topic": topic, "qos": int(qos), "success": True}
        except Exception as e:
            return {"error": str(e), "success": False}

    @zelos_sdk.action("Subscribe to Topic", "Subscribe to an additional MQTT topic")
    @zelos_sdk.action.text("topic", title="Topic", placeholder="sensors/#")
    @zelos_sdk.action.number("qos", minimum=0, maximum=2, default=0, title="QoS")
    def subscribe_topic(self, topic: str, qos: int = 0) -> dict[str, Any]:
        """Subscribe to an additional MQTT topic at runtime.

        If the topic is already in the node map, messages are handled normally.
        Otherwise, messages are traced via log_dict with schema inferred from
        the payload.
        """
        if not self._connected or not self._mqtt_client or not self._loop:
            return {"error": "Not connected to broker", "success": False}

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._mqtt_client.subscribe(topic, qos=int(qos)),
                self._loop,
            )
            future.result(timeout=5.0)

            # Track as dynamic if not already in the node map
            if topic not in self._topic_lookup:
                self._dynamic_topics.add(topic)

            return {
                "status": "subscribed",
                "topic": topic,
                "qos": int(qos),
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}
