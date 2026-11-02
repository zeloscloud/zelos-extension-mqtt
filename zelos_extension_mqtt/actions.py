"""MQTT actions, free functions registered under `<ACTION_PREFIX>/<name>`.

Free functions, not client methods: one surface whether or not a client runs.
`broker` may be omitted with one broker. Failures raise: the protocol reads its
verdict from an exception, so {"success": False} would report a successful run.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from typing import TYPE_CHECKING, Any

import zelos_sdk

from zelos_extension_mqtt.mqtt_map import filter_error

if TYPE_CHECKING:
    from zelos_sdk.actions import ActionsRegistry

    from zelos_extension_mqtt.client import BrokerClient, MqttRunner

logger = logging.getLogger(__name__)

_runner: MqttRunner | None = None


def set_runner(runner: MqttRunner | None) -> None:
    """Bind the runner whose clients every action in this module operates on."""
    global _runner
    _runner = runner


def _get_runner() -> MqttRunner:
    if _runner is None:
        raise RuntimeError("No MQTT broker is configured on this extension")
    return _runner


def _get_client(broker: str = "") -> BrokerClient:
    """The named broker's client; the only one when `broker` is empty."""
    clients = _get_runner().clients
    names = ", ".join(clients)
    if not broker:
        if len(clients) == 1:
            return next(iter(clients.values()))
        raise ValueError(f"Several brokers are configured; set broker to one of: {names}")
    # Sanitized like the default names, so `192.168.1.10` finds `192_168_1_10`.
    client = clients.get(zelos_sdk.sanitize_name(broker.strip(), kind="source"))
    if client is None:
        raise ValueError(f"Unknown broker '{broker}'. Brokers: {names}")
    return client


def _clients_for(broker: str) -> list[BrokerClient]:
    """The named broker, or every broker when omitted."""
    return [_get_client(broker)] if broker else list(_get_runner().clients.values())


def _broker_field(func: Any) -> Any:
    return zelos_sdk.action.text(
        "broker",
        title="Broker",
        required=False,
        default="",
        description="Broker name; may be omitted when only one is configured",
    )(func)


def _run(coro: Any, what: str) -> Any:
    """Dispatch onto the broker loop; anything not self-describing becomes a RuntimeError."""
    try:
        return _get_runner().run_coro(coro)
    except (ValueError, RuntimeError):
        raise
    except TimeoutError as e:
        raise TimeoutError(f"{what}: the broker did not acknowledge in time") from e
    except Exception as e:
        raise RuntimeError(f"{what} failed: {e}") from e


def _qos(qos: Any) -> int:
    value = int(qos)
    if value not in (0, 1, 2):
        raise ValueError(f"QoS must be 0, 1 or 2, not {qos}")
    return value


# ─── Status and discovery ───────────────────────────────────────────────────


@zelos_sdk.action(
    "Get Status",
    "Connection state, subscriptions and message counts; every broker when omitted",
    read_only=True,
    hidden=True,
)
@_broker_field
def get_status(broker: str = "") -> dict[str, Any]:
    if broker:
        return _get_client(broker).status()
    statuses = [c.status() for c in _get_runner().clients.values()]
    return {"brokers": statuses, "count": len(statuses)}


@zelos_sdk.action(
    "List Topics",
    "Topics received so far, where each traces and its fields' types, plus a device map "
    "of them ready to save as a device_map_file; every broker when omitted",
    read_only=True,
    hidden=True,
)
@_broker_field
def list_topics(broker: str = "") -> dict[str, Any]:
    out = [
        {"broker": c.name, "topics": c.topics(), "device_map": c.device_map()}
        for c in _clients_for(broker)
    ]
    return {"brokers": out, "count": sum(len(b["topics"]) for b in out)}


# ─── Publish and subscribe (dispatched onto the running loop) ───────────────


@zelos_sdk.action("Publish", "Publish a message to a topic")
@_broker_field
@zelos_sdk.action.text("topic", title="Topic", description="e.g. devices/pump1/setpoint")
@zelos_sdk.action.text(
    "payload", title="Payload", required=False, default="", description='e.g. {"value": 23.5}'
)
@zelos_sdk.action.integer("qos", title="QoS", minimum=0, maximum=2, default=0)
@zelos_sdk.action.boolean("retain", title="Retain", default=False)
def publish(
    topic: str, payload: str = "", qos: int = 0, retain: bool = False, broker: str = ""
) -> dict[str, Any]:
    if not topic or "+" in topic or "#" in topic:
        raise ValueError(f"Invalid topic {topic!r}: a topic to publish is not empty, no + or #")
    client = _get_client(broker)
    data = str(payload).encode("utf-8")
    level = _qos(qos)
    _run(client.publish(topic, data, level, bool(retain)), f"Publish to '{topic}'")
    return {
        "broker": client.name,
        "topic": topic,
        "qos": level,
        "retain": bool(retain),
        "bytes": len(data),
    }


@zelos_sdk.action(
    "Subscribe",
    "Add a topic filter for this run; its messages are traced like the configured topics",
)
@_broker_field
@zelos_sdk.action.text("topic", title="Topic Filter", description="e.g. sensors/# or $SYS/#")
@zelos_sdk.action.integer("qos", title="QoS", minimum=0, maximum=2, default=0)
def subscribe(topic: str, qos: int = 0, broker: str = "") -> dict[str, Any]:
    if reason := filter_error(topic):
        raise ValueError(f"Invalid topic filter {topic!r}: {reason}")
    client = _get_client(broker)
    subscribed = _run(client.subscribe(topic, _qos(qos)), f"Subscribe to '{topic}'")
    return {"broker": client.name, "topic": topic, "subscriptions": subscribed}


# ─── Config-form hook (standalone: runs with the extension stopped) ─────────


@zelos_sdk.action(
    "Auto-configure",
    "Check each broker in the form (unsaved edits included; older apps: the saved config) "
    "with an MQTT connect. With none, look for brokers on this machine (1883, and 8883 "
    "with TLS) and announced over mDNS. Review, then save and start.",
    # Read-only (connect and disconnect, nothing subscribed), and wanted before a first start.
    standalone=True,
    hidden=True,
    read_only=True,
)
@zelos_sdk.action.object(
    "config",
    properties={},
    title="Config",
    description="The config form's current (possibly unsaved) data. Empty: the saved config",
    required=False,
)
def auto_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """`config` keys replace the form's: only `brokers`, so Advanced survives.

    Its own asyncio.run: called from the actions thread, never the broker loop.
    """
    from zelos_extension_mqtt.autoconfig import check_brokers, probe

    if config is None:
        config = _saved_config()
    brokers = [b for b in config.get("brokers") or [] if isinstance(b, dict)]
    advanced = config.get("advanced") or {}
    return asyncio.run(check_brokers(brokers, advanced) if brokers else probe(advanced))


def _saved_config() -> dict[str, Any]:
    """The saved config, or {} when there is none."""
    try:
        from zelos_sdk.extensions import load_config

        return load_config() or {}
    except Exception:  # no config yet, or it does not validate
        return {}


# ─── Registration helper ────────────────────────────────────────────────────


def register_actions(registry: ActionsRegistry) -> list[str]:
    """Register every @action function here by bare name; `init(name=ACTION_PREFIX)`
    adds the `MQTT/` prefix. Returns the registered names."""
    module = sys.modules[__name__]
    registered: list[str] = []
    for name, obj in inspect.getmembers(module):
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) and hasattr(obj, "_action"):
            registry.register(obj, name=name)
            registered.append(name)
    logger.info("Registered %d MQTT actions", len(registered))
    return registered
