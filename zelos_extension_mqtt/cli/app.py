"""App mode: run from the Zelos App's saved config, or demo mode.

Startup problems are one logger.error line plus exit 1; tracebacks are for bugs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import zelos_sdk
from zelos_sdk.extensions import ConfigValidationError, load_config

from zelos_extension_mqtt import ACTION_PREFIX
from zelos_extension_mqtt import actions as mqtt_actions
from zelos_extension_mqtt.client import BrokerClient, MqttRunner, install_log_handler, tls_context
from zelos_extension_mqtt.constants import (
    DEFAULT_PREFIX,
    LOG_SOURCE_NAME,
    RESERVED_NAMES,
    name_error,
)
from zelos_extension_mqtt.mqtt_map import NodeMap, filter_error

logger = logging.getLogger(__name__)

DEMO_HOST = "127.0.0.1"
DEMO_PORT = 11883

#: Defaults of the schema's `advanced` object: settings shared by every broker.
ADVANCED_DEFAULTS: dict[str, Any] = {
    "prefix": DEFAULT_PREFIX,
    "log_level": "INFO",
    "retry_unreachable_at_start": False,
    "keepalive": 60,
    "ca_file": "",
    "cert_file": "",
    "key_file": "",
}

#: Defaults of one `brokers[]` entry. "" TLS files inherit from `advanced`.
BROKER_DEFAULTS: dict[str, Any] = {
    "name": "",
    "host": "localhost",
    "port": 1883,
    "topics": ["#"],
    "device_map_file": "",
    "client_id": "",
    "tls": False,
    "ca_file": "",
    "cert_file": "",
    "key_file": "",
}

# `demo`: the removed Demo Mode toggle, still in older saved configs; ignored.
TOP_LEVEL_KEYS = frozenset({"demo", "brokers", "advanced"})

#: Libraries that log per connection or packet at INFO: only DEBUG opens them.
CHATTY_LOGGERS = ("mqtt", "amqtt", "transitions")


def resolve_advanced(config: dict[str, Any]) -> dict[str, Any]:
    """`advanced` over its defaults; an absent `prefix` is the default, an empty one clears it."""
    return {**ADVANCED_DEFAULTS, **(config.get("advanced") or {})}


def read_raw_config() -> dict[str, Any]:
    """config.json as saved: an old config fails the schema without saying the format changed."""
    path = Path(os.environ.get("ZELOS_CONFIG_PATH") or "config.json")
    return json.loads(path.read_text()) if path.is_file() else {}


def reject_legacy_shape(config: dict[str, Any]) -> None:
    """Exit on a config from before `brokers[]`, rather than misread it."""
    legacy = sorted(k for k in config if k not in TOP_LEVEL_KEYS)
    if legacy:
        logger.error(
            "Config format changed: brokers are now listed under brokers[] with shared "
            "settings under advanced, and user name / password login is removed (found "
            "top-level %s); reconfigure the extension",
            ", ".join(legacy),
        )
        sys.exit(1)


def _exit_on(message: str | None) -> None:
    if message:
        logger.error("%s", message)
        sys.exit(1)


def broker_settings(entry: dict[str, Any], advanced: dict[str, Any]) -> dict[str, Any]:
    """One `brokers[]` entry's effective settings; `name` is "" when left to default.

    The client certificate and key inherit as a pair: a mix would fail as a key mismatch.

    Raises:
        ValueError: A setting that cannot work, named
    """
    broker = {**BROKER_DEFAULTS, **entry}
    host = str(broker["host"]).strip()
    if not host:
        raise ValueError("Host is empty")
    name = str(broker["name"]).strip()
    if error := name_error(name, "broker Name", RESERVED_NAMES):
        raise ValueError(error)
    topics = [str(t) for t in broker["topics"] or []]
    for topic in topics:
        if reason := filter_error(topic):
            raise ValueError(f"Invalid topic filter {topic!r}: {reason}")
    pair = ("cert_file", "key_file")
    source = broker if any(broker[k] for k in pair) else advanced
    files = {"ca_file": broker["ca_file"] or advanced["ca_file"], **{k: source[k] for k in pair}}
    if bool(files["cert_file"]) != bool(files["key_file"]):
        raise ValueError("set Client Certificate and Client Key together, or neither")
    return {
        "name": name,
        "host": host,
        "port": int(broker["port"]),
        "topics": topics,
        "device_map_file": str(broker["device_map_file"] or ""),
        "client_id": str(broker["client_id"] or ""),
        "tls": bool(broker["tls"]),
        **files,
    }


def resolve_brokers(config: dict[str, Any], advanced: dict[str, Any]) -> list[dict[str, Any]]:
    """Every broker's settings, uniquely named, or exit.

    Default names (the sanitized host) that collide take a `_<port>` suffix;
    explicit duplicates exit.
    """
    reject_legacy_shape(config)
    entries = config.get("brokers") or []
    if not entries:
        _exit_on("No brokers configured: add one under Brokers")
    brokers = []
    for i, entry in enumerate(entries):
        try:
            brokers.append(broker_settings(entry, advanced))
        except ValueError as e:
            label = str(entry.get("name") or entry.get("host") or f"brokers[{i}]").strip()
            _exit_on(f"Broker '{label}': {e}")
    named = [bool(b["name"]) for b in brokers]
    for b in brokers:
        b["name"] = b["name"] or zelos_sdk.sanitize_name(b["host"], kind="source")
    defaults = [b["name"] for b, n in zip(brokers, named, strict=True) if not n]
    for b, n in zip(brokers, named, strict=True):
        if not n and defaults.count(b["name"]) > 1:
            b["name"] = f"{b['name']}_{b['port']}"
    for i, b in enumerate(brokers):
        _exit_on(name_error(b["name"], "broker Name", RESERVED_NAMES))
        if any(other["name"] == b["name"] for other in brokers[:i]):
            _exit_on(f"Duplicate broker name '{b['name']}'; set Name on one of them.")
    return brokers


def demo_broker() -> dict[str, Any]:
    """The built-in broker and simulator as the one broker `demo`: plain, its demo map."""
    return {
        **BROKER_DEFAULTS,
        "name": "demo",
        "host": DEMO_HOST,
        "port": DEMO_PORT,
        "device_map_file": str(get_demo_device_map_path()),
    }


def get_demo_device_map_path() -> Path:
    """Path to the bundled demo device map."""
    with resources.as_file(
        resources.files("zelos_extension_mqtt.demo").joinpath("demo_device.json")
    ) as path:
        return path


def load_device_map(path: str, broker: str) -> NodeMap | None:
    """The broker's device map, or exit: a map that does not load would trace the wrong events."""
    if not path:
        return None
    try:
        node_map = NodeMap.from_file(path)
    except (OSError, ValueError) as e:
        reason = e.strerror if isinstance(e, OSError) and e.strerror else e
        _exit_on(f"Broker '{broker}': invalid device map {path}: {reason}")
    logger.info("[%s] Loaded device map %s (%d topics)", broker, path, len(node_map.nodes))
    return node_map


def build_clients(brokers: list[dict[str, Any]], advanced: dict[str, Any]) -> list[BrokerClient]:
    """One client per broker, or exit on a device map, TLS file or topic overlap problem."""
    clients = []
    for b in brokers:
        node_map = load_device_map(b["device_map_file"], b["name"])
        try:
            tls = tls_context(b["ca_file"], b["cert_file"], b["key_file"]) if b["tls"] else None
            clients.append(
                BrokerClient(
                    name=b["name"],
                    host=b["host"],
                    port=b["port"],
                    topics=b["topics"],
                    node_map=node_map,
                    client_id=b["client_id"],
                    keepalive=int(advanced["keepalive"]),
                    tls=tls,
                )
            )
        except ValueError as e:
            _exit_on(f"Broker '{b['name']}': {e}")
    return clients


def apply_log_level(level_name: str) -> None:
    """Apply the configured log level, falling back to INFO on a bad value."""
    level = getattr(logging, str(level_name).upper(), None)
    if not isinstance(level, int):
        logger.warning("Invalid log level '%s', using INFO", level_name)
        level = logging.INFO
    logging.getLogger().setLevel(level)
    quiet = logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    for name in CHATTY_LOGGERS:
        logging.getLogger(name).setLevel(quiet)


def open_sources(prefix: str) -> zelos_sdk.TraceSource | None:
    """The prefix's shared source with the logs on its `log` event; cleared, None and the
    logs on their own source. The SDK's global source, so init() reuses it rather than
    adding an empty one named after the action namespace. Earlier records reach stderr only."""
    source = zelos_sdk.init_global_source(prefix or LOG_SOURCE_NAME)
    install_log_handler(source)
    if not prefix:
        logger.info("Trace prefix cleared: one trace source per broker")
        return None
    logger.info("Trace prefix: %s", prefix)
    return source


def serve(clients: list[BrokerClient], prefix: str, retry_unreachable: bool = False) -> None:
    """Publish the action surface, then trace every broker until shutdown.

    Actions register before `init()`: registered later they may never be advertised.
    """
    runner = MqttRunner(clients, retry_unreachable)
    mqtt_actions.set_runner(runner)
    mqtt_actions.register_actions(zelos_sdk.actions_registry)
    shared = open_sources(prefix)
    zelos_sdk.init(name=ACTION_PREFIX, actions=True)
    for client in clients:
        client.start(shared)
    runner.run()
    if runner.failed_at_start or runner.crashed:
        sys.exit(1)


def trace_path(trace_file: Path) -> Path:
    """`--file` with no name (".") records to `<UTC time>.trz`."""
    return Path(f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.trz") if str(trace_file) == "." else trace_file


def run_config(config: dict[str, Any], demo: bool = False, trace_file: Path | None = None) -> None:
    """Resolve an app-shaped config (brokers[], advanced) and serve it; `demo` (the CLI's
    `demo` / `--demo`) replaces the brokers with the built-in broker and simulator."""
    advanced = resolve_advanced(config)
    apply_log_level(advanced["log_level"])
    prefix = str(advanced["prefix"]).strip()
    _exit_on(name_error(prefix, "Prefix"))
    brokers = [demo_broker()] if demo else resolve_brokers(config, advanced)
    clients = build_clients(brokers, advanced)

    with contextlib.ExitStack() as stack:
        if trace_file is not None:
            path = trace_path(trace_file)
            stack.enter_context(zelos_sdk.TraceWriter(str(path)))
            logger.info("Recording to %s", path)
        if demo:
            from zelos_extension_mqtt.demo.simulator import DemoServer

            server = DemoServer(host=DEMO_HOST, port=DEMO_PORT)
            try:
                server.start()
            except OSError as e:
                _exit_on(str(e))
            stack.callback(server.stop)
            logger.info("Demo mode: built-in broker and simulator on %s:%d", DEMO_HOST, DEMO_PORT)
        serve(clients, prefix, bool(advanced["retry_unreachable_at_start"]))


def run_app_mode(demo: bool = False, trace_file: Path | None = None) -> None:
    """Run the extension with configuration from the Zelos App.

    Args:
        demo: Run the built-in broker and simulator as the only broker
        trace_file: Also record the trace to this .trz file
    """
    reject_legacy_shape(read_raw_config())
    try:
        config = load_config()
    except ConfigValidationError as e:
        logger.error("Invalid configuration: %s", "; ".join(m.strip(" •") for m in e.errors))
        sys.exit(1)
    run_config(config, demo, trace_file)
