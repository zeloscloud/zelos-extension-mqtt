"""MQTT brokers traced into Zelos, and the runner that owns their event loop.

`BrokerClient` subscribes to one broker and logs every message at its receive
time: a topic the device map matches into the map's typed event, any other into
an event named after the topic (discovery). `MqttRunner` runs every client on one
loop: signals, the first-contact gate, action dispatch and bounded shutdown.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import signal
import socket
import ssl
import threading
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

import aiomqtt
import zelos_sdk
from aiomqtt.exceptions import MqttConnectError
from zelos_sdk.hooks.logging import TraceLoggingHandler

from zelos_extension_mqtt.constants import HEALTH_EVENT, trace_layout
from zelos_extension_mqtt.mqtt_map import (
    RESERVED_FIELDS,
    Node,
    NodeMap,
    check_overlaps,
    subscriptions,
)

logger = logging.getLogger(__name__)

RECONNECT_INITIAL = 3.0
RECONNECT_MAX = 60.0
#: Disconnects run concurrently under this bound: ~3 s in total.
SHUTDOWN_TIMEOUT = 3.0
HEALTH_SECONDS = 5.0
#: Publish and subscribe wait this long for each acknowledgement from the broker.
ACTION_TIMEOUT = 10.0

SDK_DATATYPES = {
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
    "string": zelos_sdk.DataType.String,
}
INT_RANGES = {f"int{b}": (-(2 ** (b - 1)), 2 ** (b - 1) - 1) for b in (8, 16, 32, 64)} | {
    f"uint{b}": (0, 2**b - 1) for b in (8, 16, 32, 64)
}
BOOL_TRUE = frozenset({"true", "1", "on", "yes"})
BOOL_FALSE = frozenset({"false", "0", "off", "no"})

#: Trace types of discovered fields: JSON has one number type.
DISCOVERED_TYPES = {
    "bool": zelos_sdk.DataType.Boolean,
    "float64": zelos_sdk.DataType.Float64,
    "string": zelos_sdk.DataType.String,
}

HEALTH_FIELDS = (
    ("connected", zelos_sdk.DataType.Boolean),
    ("reconnects", zelos_sdk.DataType.UInt32),
    ("messages", zelos_sdk.DataType.UInt64),
    ("published", zelos_sdk.DataType.UInt64),
)


def tls_context(ca_file: str = "", cert_file: str = "", key_file: str = "") -> ssl.SSLContext:
    """Server certificate and host name verified against `ca_file`, else the system trust
    store; a client certificate and key make it mutual TLS.

    Raises:
        ValueError: A file is missing or unreadable, or the key does not match
    """
    try:
        context = ssl.create_default_context(cafile=ca_file or None)
        if cert_file:
            context.load_cert_chain(cert_file, key_file)
    except (OSError, ssl.SSLError) as e:
        raise ValueError(f"cannot load TLS files: {e}") from e
    return context


class PahoErrors(logging.Handler):
    """paho's last ERROR on one connection: a TLS 1.3 alert (the broker requires a client
    certificate) reaches only paho's log, and the connect then just times out."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.last = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.last = record.getMessage()


def paho_logger(name: str) -> tuple[logging.Logger, PahoErrors]:
    """A logger for one aiomqtt client: its ERRORs feed the line we log, so a failure is
    one line; at DEBUG it also reaches the log as `mqtt.<name>`."""
    errors = PahoErrors()
    log = logging.Logger(f"mqtt.{name}", logging.ERROR)  # unregistered: no root propagation
    log.addHandler(errors)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        log.setLevel(logging.DEBUG)
        log.parent = logging.getLogger("mqtt")
    return log, errors


def describe_error(error: BaseException, detail: str = "") -> tuple[str, str]:
    """(reason, what the user changes) for a failed or lost connection; `detail` is paho's
    last ERROR on it."""
    if str(error) == "Operation timed out":  # no CONNACK
        if "certificate required" in detail.lower():
            return "the broker requires a client certificate", (
                "set Client Certificate and Client Key"
            )
        return "no answer to MQTT CONNECT", "check Port, and turn on TLS for a TLS port"
    # aiomqtt re-raises a connect OSError as MqttError(str(e)) from None: the type is the context.
    cause = error.__context__ if isinstance(error.__context__, OSError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        reason = f"TLS certificate not accepted: {cause.verify_message or cause.reason}"
        if "hostname" in reason.lower() or "ip address" in reason.lower():
            return reason, "set Host to a name the broker's certificate lists"
        return reason, "set CA File to the CA that signed the broker's certificate"
    if isinstance(cause, ssl.SSLError):
        return (
            f"TLS failed: {cause.reason or cause}",
            "check that Port is the broker's TLS port and that it accepts the client certificate",
        )
    if isinstance(cause, socket.gaierror):
        return f"cannot resolve host: {cause.strerror}", "check Host"
    if isinstance(cause, ConnectionRefusedError):
        return "connection refused", "check Host and Port"
    if isinstance(cause, TimeoutError):
        return "timed out", "check that the broker is reachable at Host and Port"
    if isinstance(error, MqttConnectError):
        code = getattr(error.rc, "value", error.rc)
        if code in (4, 5, 134, 135):  # bad user name or password, not authorized (3.1.1, 5)
            return (
                f"the broker refused the connection: {error.rc}",
                "this extension logs in without a user name or password: allow anonymous "
                "clients or TLS client certificate login on the broker",
            )
        if code in (2, 133):
            return f"the broker refused the connection: {error.rc}", "set a different Client ID"
        return f"the broker refused the connection: {error.rc}", ""
    if isinstance(cause, OSError):
        return cause.strerror or str(cause), "check Host and Port"
    reason = str(error) or type(error).__name__
    return (f"{reason}: {detail}" if detail else reason), ""


def convert(value: Any, datatype: str) -> Any:
    """A decoded payload value as `datatype`; ValueError says why it cannot be."""
    if value is None:
        raise ValueError("the value is null")
    if datatype == "string":
        return value if isinstance(value, str) else json.dumps(value)
    if datatype == "bool":
        if isinstance(value, str):
            if value.lower() in BOOL_TRUE | BOOL_FALSE:
                return value.lower() in BOOL_TRUE
        elif isinstance(value, bool | int | float):
            return bool(value)
        raise ValueError(f"{value!r} is not a boolean")
    if isinstance(value, dict | list):
        raise ValueError(f"{json.dumps(value)} is not a number")
    try:
        if datatype.startswith("float"):
            return float(value)
        if isinstance(value, str):
            value = int(value) if value.strip().lstrip("+-").isdigit() else float(value)
        number = int(value)  # truncates a float, as before
    except (OverflowError, TypeError, ValueError) as e:
        raise ValueError(f"{value!r} is not a number") from e
    low, high = INT_RANGES[datatype]
    if not low <= number <= high:
        raise ValueError(f"{number} is out of range for {datatype}")
    return number


def node_value(text: str, node: Node) -> Any:
    """`text` read as `node` declares it; ValueError says why it cannot be."""
    if node.payload_type == "json" or (node.payload_type == "auto" and text.startswith("{")):
        value = json.loads(text)
        if isinstance(value, dict):
            for key in node.json_path:
                if not isinstance(value, dict) or key not in value:
                    raise ValueError(f"the JSON has no key {'.'.join(node.json_path)!r}")
                value = value[key]
    else:
        try:
            value = json.loads(text)
        except ValueError:
            value = text
    return convert(value, node.datatype)


def kind_of(value: Any) -> str:
    """The DISCOVERED_TYPES key a decoded value takes."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "float64"
    return "string"


def payload_fields(text: str) -> tuple[dict[str, tuple[str, tuple[str, ...], Any]], list[str]]:
    """Fields a discovered message carries, and names skipped.

    A JSON object gives one field per leaf, nested keys joined with `_`; anything
    else is one field `value`. Nulls are left out; the reserved `time_ns`, and a
    name an earlier key already took, are skipped.

    Returns:
        ({name: (kind, json path, value)}, [skipped names])
    """
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    fields: dict[str, tuple[str, tuple[str, ...], Any]] = {}
    skipped: list[str] = []

    def add(path: tuple[str, ...], value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                add((*path, key), item)
            return
        if value is None:
            return
        name = "_".join(zelos_sdk.sanitize_name(k, kind="field") for k in path) or "value"
        if name in fields or name in RESERVED_FIELDS:
            skipped.append(name)
            return
        fields[name] = (kind_of(value), path, value)

    add((), data)
    return fields, skipped


def coerce(kind: str, value: Any) -> Any:
    """`value` as a discovered field of `kind` takes it; ValueError when it cannot."""
    if kind == "string":
        return value if isinstance(value, str) else json.dumps(value)
    if kind == "bool" and isinstance(value, bool):
        return value
    if kind == "float64" and isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return float(value)
        except OverflowError:
            pass
    raise ValueError(f"{json.dumps(value)} is not {kind}")


def discovery_event(topic: str) -> str:
    """The event an unmapped topic traces as: each level sanitized, joined with `/`."""
    return "/".join(zelos_sdk.sanitize_name(level, kind="event") for level in topic.split("/"))


def install_log_handler(source: zelos_sdk.TraceSource | str) -> TraceLoggingHandler:
    """Route INFO and above into the trace; DEBUG would flood it with library chatter."""
    handler = TraceLoggingHandler(source)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    return handler


@dataclass
class _Seen:
    """A received topic: the event it traces as ("" when not traced) and its count."""

    event: str
    mapped: bool
    count: int = 0


@dataclass
class _Discovered:
    """An unmapped topic's event: fields fixed by its first message."""

    topic: str
    event: Any  # TraceSourceEvent
    fields: dict[str, tuple[str, tuple[str, ...]]]  # name -> (kind, json path)
    extra: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)  # seen later


class BrokerClient:
    """One broker: connection, subscriptions, decoding into trace events, health.

    Runs in an MqttRunner's loop; actions reach it through the runner.
    """

    def __init__(
        self,
        name: str,
        host: str = "localhost",
        port: int = 1883,
        topics: Sequence[str] = ("#",),
        node_map: NodeMap | None = None,
        client_id: str = "",
        keepalive: int = 60,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.node_map = node_map or NodeMap()
        self.client_id = client_id
        self.keepalive = keepalive
        self.tls = tls
        self._paho_log, self._paho_errors = paho_logger(name)
        # Wanted filters -> QoS: the configured topics, the map's, and the subscribe action's.
        self.filters: dict[str, int] = dict.fromkeys(topics, 0)
        for node in self.node_map.nodes:
            self.filters[node.topic] = max(node.qos, self.filters.get(node.topic, 0))
        check_overlaps(self.filters, "Topic filters")

        self.connected = False
        self.ever_connected = False
        self.reconnects = 0
        self.messages = 0
        self.published = 0
        self.last_error = ""
        self.fix = ""
        #: Set after the first connect attempt, whatever its outcome.
        self.contacted = asyncio.Event()

        self._client: aiomqtt.Client | None = None
        self._active: dict[str, int] = {}  # subscribed on this connection
        self._subscribing = asyncio.Lock()
        self._source: zelos_sdk.TraceSource | None = None
        self._event_prefix = ""
        self._events: dict[str, Any] = {}  # map event -> TraceSourceEvent
        self._last: dict[str, dict[str, Any]] = {}  # map event -> its fields' last values
        self._health: Any = None
        self._discovered: dict[str, _Discovered] = {}  # discovery event name -> its topic
        self._seen: dict[str, _Seen] = {}
        self._warned: set[tuple[str, ...]] = set()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}{', TLS' if self.tls else ''}"

    def _warn_once(self, key: tuple[str, ...], msg: str, *args: Any) -> None:
        """One WARNING per key per run: per message would flood the log and the trace."""
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(f"[{self.name}] {msg}", *args)

    # ─── Trace ──────────────────────────────────────────────────────────────

    def start(self, shared: zelos_sdk.TraceSource | None = None) -> None:
        """Declare the health event and the map's events: on the prefix's shared source,
        events nested as `<broker>/<event>`, or on a source of this broker's own."""
        source_name, self._event_prefix = trace_layout(shared.name if shared else "", self.name)
        self._source = shared if shared is not None else zelos_sdk.TraceSource(source_name)
        self._health = self._add_event(HEALTH_EVENT, list(HEALTH_FIELDS))
        for event, nodes in self.node_map.events.items():
            if nodes:
                fields = [(n.name, SDK_DATATYPES[n.datatype], n.unit) for n in nodes]
                self._events[event] = self._add_event(event, fields)

    def _add_event(self, name: str, fields: list[tuple[Any, ...]]) -> Any:
        assert self._source is not None
        return self._source.add_event(
            f"{self._event_prefix}{name}",
            [zelos_sdk.TraceEventFieldMetadata(*f) for f in fields],
        )

    def _log_health(self) -> None:
        if self._health is not None:
            self._health.log_at(
                time.time_ns(),
                connected=self.connected,
                reconnects=self.reconnects,
                messages=self.messages,
                published=self.published,
            )

    def on_message(self, topic: str, payload: Any, received_ns: int) -> None:
        """Trace one message: into the most specific map entry, else discovered."""
        self.messages += 1
        entries = self.node_map.match(topic)
        seen = self._seen.setdefault(topic, _Seen("", bool(entries)))
        seen.count += 1
        try:
            raw = payload if isinstance(payload, bytes | bytearray) else str(payload).encode()
            text = bytes(raw).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._warn_once((topic,), "Topic '%s': payload is not UTF-8 text; not traced", topic)
            return
        try:
            if entries:
                seen.event = entries[0][0]
                self._log_mapped(topic, entries, text, received_ns)
            else:
                seen.event = self._log_discovered(topic, text, received_ns) or seen.event
        except Exception:  # a bug: keep tracing the other topics
            if (topic, "bug") not in self._warned:
                self._warned.add((topic, "bug"))
                logger.exception("[%s] Topic '%s': not traced", self.name, topic)

    def _log_mapped(
        self, topic: str, entries: list[tuple[str, Node]], text: str, received_ns: int
    ) -> None:
        """One row per event the topic maps into, every field carried: the ones this
        message sets, the rest at their last value this session (as OPC UA does), so a
        latest-value read of the event is whole rather than the newest topic alone."""
        rows: dict[str, dict[str, Any]] = {}
        for event, node in entries:
            try:
                rows.setdefault(event, {})[node.name] = node_value(text, node)
            except ValueError as e:
                self._warn_once(
                    (topic, event, node.name),
                    "Topic '%s': %s.%s not recorded: %s; warned once",
                    topic,
                    event,
                    node.name,
                    e,
                )
        for event, values in rows.items():
            if values:
                last = self._last.setdefault(event, {})
                last.update(values)
                self._events[event].log_at(received_ns, **last)

    def _log_discovered(self, topic: str, text: str, received_ns: int) -> str:
        """Log an unmapped topic; returns its event name, "" when it is not traced."""
        name = discovery_event(topic)
        fields, skipped = payload_fields(text)
        if skipped:
            self._warn_once(
                (topic, "skipped"),
                "Topic '%s': field(s) %s not traced (a duplicate name, or reserved)",
                topic,
                ", ".join(sorted(set(skipped))),
            )
        known = self._discovered.get(name)
        if known is None:
            if name in self._events:
                self._warn_once(
                    (topic, "event"),
                    "Topic '%s' would trace as event '%s', which the device map declares; "
                    "not traced",
                    topic,
                    name,
                )
                return ""
            if not fields:
                return ""
            layout = {n: (kind, path) for n, (kind, path, _) in fields.items()}
            event = self._add_event(
                name, [(n, DISCOVERED_TYPES[kind]) for n, (kind, _) in layout.items()]
            )
            known = self._discovered[name] = _Discovered(topic, event, layout)
        elif known.topic != topic:
            self._warn_once(
                (topic, "event"),
                "Topics '%s' and '%s' both trace as event '%s'; '%s' is not traced: "
                "map it in a device map",
                known.topic,
                topic,
                name,
                topic,
            )
            return ""
        values = {}
        for field_name, (kind, path, value) in fields.items():
            declared = known.fields.get(field_name)
            if declared is None:
                known.extra.setdefault(field_name, (kind, path))
                self._warn_once(
                    (topic, field_name),
                    "Topic '%s': field '%s' was not in its first message, which fixes the "
                    "event's fields; not traced (list_topics includes it)",
                    topic,
                    field_name,
                )
                continue
            try:
                values[field_name] = coerce(declared[0], value)
            except ValueError as e:
                self._warn_once(
                    (topic, field_name),
                    "Topic '%s': field '%s' not recorded: %s; warned once",
                    topic,
                    field_name,
                    e,
                )
        if values:
            known.event.log_at(received_ns, **values)
        return name

    # ─── Connection ─────────────────────────────────────────────────────────

    def client_kwargs(self) -> dict[str, Any]:
        """aiomqtt.Client arguments."""
        return {
            "hostname": self.host,
            "port": self.port,
            "identifier": self.client_id or None,
            "keepalive": self.keepalive,
            "tls_context": self.tls,
            "logger": self._paho_log,
        }

    async def run(self) -> None:
        """Connect, subscribe and trace until cancelled; reconnect with backoff."""
        try:
            await self._reconnect_forever()
        finally:  # a crash (a bug) must not hold the runner's first-contact wait
            self.contacted.set()

    async def _reconnect_forever(self) -> None:
        backoff = RECONNECT_INITIAL
        while True:
            up = False
            self._paho_errors.last = ""
            client = aiomqtt.Client(**self.client_kwargs())
            try:
                async with client:
                    up = True
                    await self._serve(client)
            except aiomqtt.MqttError as e:
                self.last_error, self.fix = describe_error(e, self._paho_errors.last)
            finally:
                if up:
                    self._down()
                _retrieve(client._disconnected)  # noqa: SLF001
            self.contacted.set()
            if up:
                backoff = RECONNECT_INITIAL
                logger.warning(
                    "[%s] Connection to %s lost: %s; reconnecting in %.0f s",
                    self.name,
                    self.address,
                    self.last_error,
                    backoff,
                )
            elif self.ever_connected:
                logger.debug("[%s] Reconnect failed: %s", self.name, self.last_error)
            await asyncio.sleep(backoff)
            if not up:
                backoff = min(backoff * 2, RECONNECT_MAX)

    async def _serve(self, client: aiomqtt.Client) -> None:
        self._client = client
        if self.ever_connected:
            self.reconnects += 1
            logger.info("[%s] Reconnected to %s", self.name, self.address)
        else:
            logger.info("[%s] Connected to %s", self.name, self.address)
        self.connected = self.ever_connected = True
        self.last_error = self.fix = ""
        self.contacted.set()
        self._log_health()
        wanted = subscriptions(self.filters)
        if wanted:
            granted = await client.subscribe(list(wanted.items()))
            for topic, code in zip(wanted, granted, strict=False):
                if _refused(code):
                    logger.error(
                        "[%s] The broker refused the subscription to '%s'; nothing on it is traced",
                        self.name,
                        topic,
                    )
            logger.info("[%s] Subscribed to %s", self.name, _describe(wanted))
        else:
            logger.warning("[%s] No topics and no device map: subscribed to nothing", self.name)
        self._active = wanted
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            async for message in client.messages:
                self.on_message(message.topic.value, message.payload, time.time_ns())
        finally:
            heartbeat.cancel()

    def _down(self) -> None:
        self.connected = False
        self._client = None
        self._active = {}
        self._log_health()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_SECONDS)
            self._log_health()

    # ─── Action support (coroutines, run on the loop) ───────────────────────

    def _require_client(self) -> aiomqtt.Client:
        if not self.connected or self._client is None:
            why = f": {self.last_error}" if self.last_error else ""
            raise RuntimeError(f"Broker '{self.name}' is not connected{why}")
        return self._client

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        client = self._require_client()
        await client.publish(topic, payload, qos=qos, retain=retain, timeout=ACTION_TIMEOUT)
        self.published += 1

    async def subscribe(self, topic: str, qos: int) -> list[str]:
        """Add `topic` to the wanted filters for this run; returns the filters subscribed.

        One at a time: each reads the filters the last one left. The new cover is
        recorded once the broker grants it, before the covered ones are dropped.
        """
        async with self._subscribing:
            client = self._require_client()
            filters = {**self.filters, topic: max(qos, self.filters.get(topic, 0))}
            check_overlaps(filters, "Topic filters")
            wanted = subscriptions(filters)
            new = [(f, q) for f, q in wanted.items() if self._active.get(f) != q]
            if new:
                granted = await client.subscribe(new, timeout=ACTION_TIMEOUT)
                refused = [f for (f, _), code in zip(new, granted, strict=False) if _refused(code)]
                if refused:
                    raise RuntimeError(
                        f"The broker refused the subscription to {', '.join(refused)}"
                    )
            self.filters = filters
            self._active = {**self._active, **dict(new)}
            gone = [f for f in self._active if f not in wanted]
            if gone:
                await client.unsubscribe(gone, timeout=ACTION_TIMEOUT)
            self._active = wanted
            logger.info("[%s] Subscribed to %s", self.name, _describe(wanted))
            return list(wanted)

    # ─── Introspection ──────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        """ok, connecting (the first connect) or disconnected (was up, retrying)."""
        if self.connected:
            return "ok"
        return "disconnected" if self.ever_connected else "connecting"

    def status(self) -> dict[str, Any]:
        return {
            "broker": self.name,
            "host": self.host,
            "port": self.port,
            "tls": self.tls is not None,
            "connected": self.connected,
            "state": self.state,
            "last_error": self.last_error,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "published": self.published,
            "subscriptions": list(self._active or subscriptions(self.filters)),
            "topics_seen": len(self._seen),
            "discovered_events": len(self._discovered),
        }

    def topics(self) -> list[dict[str, Any]]:
        """Every topic received, with where it traces and its fields' types."""
        out = []
        for topic, seen in sorted(self._seen.items()):
            if seen.mapped:
                fields = {n.name: n.datatype for _, n in self.node_map.match(topic)}
            elif (known := self._discovered.get(seen.event)) and known.topic == topic:
                fields = {n: kind for n, (kind, _) in {**known.fields, **known.extra}.items()}
            else:
                fields = {}
            out.append(
                {
                    "topic": topic,
                    "event": seen.event,
                    "mapped": seen.mapped,
                    "messages": seen.count,
                    "fields": fields,
                }
            )
        return out

    def device_map(self) -> dict[str, Any]:
        """The device map in use plus one event per discovered topic, ready to save."""
        events = self.node_map.to_dict()["events"]
        for name, known in sorted(self._discovered.items()):
            if name in events:
                continue
            nodes = []
            for field_name, (kind, path) in {**known.fields, **known.extra}.items():
                node: dict[str, Any] = {"topic": known.topic, "name": field_name, "datatype": kind}
                if path:
                    node["json_key"] = path[0] if len(path) == 1 else list(path)
                    node["payload_type"] = "json"
                nodes.append(node)
            events[name] = nodes
        return {"events": events}


class _DaemonExecutor(concurrent.futures.ThreadPoolExecutor):
    """Each call on a daemon thread, not the pool's. aiomqtt runs paho's blocking connect in
    the loop's default executor (TCP up to 5 s, the TLS handshake up to keepalive); a cancel
    does not stop it, and asyncio.run and interpreter exit join pool threads, so a stalled
    connect would hold the stop for that long. A ThreadPoolExecutor only because
    `set_default_executor` takes nothing else."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def run() -> None:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as e:
                    future.set_exception(e)

        threading.Thread(target=run, daemon=True, name="mqtt-connect").start()
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Nothing to join: a connect still running dies with the process."""


def _retrieve(future: asyncio.Future[Any]) -> None:
    """Read a done future's exception so asyncio never logs it as unretrieved. A stop that
    races the broker dropping us leaves aiomqtt's `_disconnected` set and unread; asyncio
    reports that as an ERROR ("Future exception was never retrieved") when it is collected."""
    if future.done() and not future.cancelled():
        future.exception()


def _describe(filters: dict[str, int]) -> str:
    return ", ".join(filters) if len(filters) <= 10 else f"{len(filters)} topic filters"


def _refused(code: Any) -> bool:
    """A SUBACK failure: 0x80 in 3.1.1, any code >= 0x80 in 5."""
    return getattr(code, "value", code) >= 0x80


class MqttRunner:
    """The one event loop every broker runs on: signals, first contact, actions, shutdown."""

    def __init__(self, clients: Sequence[BrokerClient], retry_unreachable: bool = False) -> None:
        """`clients`: one per broker, names unique (checked at config load)."""
        self.clients = {c.name: c for c in clients}
        self.retry_unreachable = retry_unreachable
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = asyncio.Event()
        # Brokers whose first connect failed: the caller exits 1.
        self.failed_at_start: list[BrokerClient] = []
        #: A client task ended with an exception (a bug): the caller exits 1.
        self.crashed = False

    def is_running(self) -> bool:
        return self._loop is not None and self._loop.is_running()

    def run(self) -> None:
        """Run every client until shutdown (blocking)."""
        # aiomqtt needs add_reader/add_writer, which Windows' default proactor loop lacks.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(self._run_async())

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop.set_default_executor(_DaemonExecutor())
        remove_signal_handlers = self._install_signal_handlers()
        tasks = [asyncio.create_task(c.run(), name=c.name) for c in self.clients.values()]
        try:
            if await self._first_contact():
                stop = asyncio.ensure_future(self._stop_event.wait())
                done, _ = await asyncio.wait({stop, *tasks}, return_when=asyncio.FIRST_COMPLETED)
                stop.cancel()
                for task in done - {stop}:  # a client only ends on a bug
                    self.crashed = True
                    logger.error("Broker '%s' stopped: %r", task.get_name(), task.exception())
        finally:
            for task in tasks:
                task.cancel()
            _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT) if tasks else ((), ())
            for task in pending:
                logger.warning("Broker '%s' did not disconnect within 3 s", task.get_name())
            remove_signal_handlers()
            self._loop = None

    async def _first_contact(self) -> bool:
        """Wait for every broker's first connect; log each that failed it.

        First contact is required unless retry_unreachable: a broker that cannot be
        reached at start is a config error, fixed before starting again. Once a
        broker has connected, drops go through the reconnect backoff.

        Returns:
            Whether to keep running
        """
        # Plain tasks, not a gather: a cancelled gather logs "exception was never retrieved".
        stop = asyncio.ensure_future(self._stop_event.wait())
        waiting = {asyncio.ensure_future(c.contacted.wait()) for c in self.clients.values()}
        try:
            while waiting and not stop.done():
                _, waiting = await asyncio.wait(
                    waiting | {stop}, return_when=asyncio.FIRST_COMPLETED
                )
                waiting.discard(stop)
        finally:
            for future in waiting | {stop}:
                future.cancel()
        if self._stop_event.is_set():
            return False
        down = [c for c in self.clients.values() if not c.ever_connected]
        for c in down:
            fix = f"; {c.fix}" if c.fix else ""
            then = "; retrying" if self.retry_unreachable else ", then start the extension again"
            logger.error(
                "Broker '%s' (%s): cannot connect: %s%s%s",
                c.name,
                c.address,
                c.last_error,
                fix,
                then if fix or self.retry_unreachable else "",
            )
        if down and not self.retry_unreachable:
            self.failed_at_start = down
            return False
        return True

    def stop(self) -> None:
        """Request shutdown. Safe from any thread, including a signal handler."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._stop_event.set()

    def _install_signal_handlers(self) -> Callable[[], None]:
        """Set the stop event on SIGTERM/SIGINT; returns the uninstaller.

        sys.exit from a signal.signal handler would drop the connections uncleanly.
        """
        loop, event = self._loop, self._stop_event
        if loop is None:
            return lambda: None

        def request_stop() -> None:
            logger.info("Shutdown signal received, stopping the MQTT extension")
            event.set()

        installed: list[int] = []
        replaced: dict[int, Any] = {}  # signal -> the handler we displaced
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, request_stop)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows, or off the main thread: signal.signal where it works,
                # else the owner calls stop().
                with contextlib.suppress(ValueError, OSError):
                    replaced[sig] = signal.signal(
                        sig, lambda *_: loop.call_soon_threadsafe(request_stop)
                    )

        def remove() -> None:
            for sig in installed:
                with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                    loop.remove_signal_handler(sig)
            # A stopped runner must not handle signals into a dead loop.
            for sig, prior in replaced.items():
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(sig, prior if prior is not None else signal.SIG_DFL)

        return remove

    def run_coro(self, coro: Coroutine[Any, Any, Any], timeout: float = ACTION_TIMEOUT + 1) -> Any:
        """Run an action's coroutine on the live loop, where the connections are.

        Raises:
            RuntimeError: If the loop is not running
            TimeoutError: If the coroutine outlives `timeout`, having cancelled it
        """
        if self._loop is None or not self._loop.is_running():
            coro.close()
            raise RuntimeError("extension is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise
