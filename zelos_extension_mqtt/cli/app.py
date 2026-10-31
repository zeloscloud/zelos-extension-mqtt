"""App mode runner for the MQTT extension.

Loads configuration, creates the client, registers its actions and initializes the SDK.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path

import zelos_sdk
from zelos_sdk.extensions import ConfigValidationError, load_config
from zelos_sdk.hooks.logging import TraceLoggingHandler

from zelos_extension_mqtt.client import MqttClient
from zelos_extension_mqtt.demo.simulator import DemoServer
from zelos_extension_mqtt.mqtt_map import NodeMap

logger = logging.getLogger(__name__)

DEMO_DEVICE_PATH = Path(__file__).parent.parent / "demo" / "demo_device.json"


def run_app_mode(
    demo: bool = False,
    trace_file: Path | None = None,
    set_client: Callable | None = None,
) -> None:
    """Run the MQTT extension in app-based configuration mode.

    Args:
        demo: If True, start embedded broker + simulator.
        trace_file: Optional path to write trace data to a .trz file.
        set_client: Callback to store client reference for shutdown handling.
    """
    try:
        config = load_config()
    except ConfigValidationError as e:
        logger.error("Invalid configuration: %s", "; ".join(m.strip(" •") for m in e.errors))
        sys.exit(1)

    log_level = config.get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    host = config.get("host", "localhost")
    port = int(config.get("port", 1883))
    poll_interval = float(config.get("poll_interval", 1.0))
    client_id = config.get("client_id") or None
    username = config.get("username") or None
    password = config.get("password") or None

    node_map = None
    device_map_file = config.get("device_map_file")
    if device_map_file and Path(device_map_file).exists():
        try:
            node_map = NodeMap.from_file(device_map_file)
        except (OSError, ValueError) as e:
            logger.error("Invalid device map %s: %s", device_map_file, e)
            sys.exit(1)
        logger.info(f"Loaded device map: {device_map_file} ({len(node_map.nodes)} nodes)")

    demo_server = None
    if demo or config.get("demo", False):
        demo_server = DemoServer(host="127.0.0.1", port=port, publish_interval=poll_interval)
        demo_server.start()
        host = "127.0.0.1"
        if not node_map:
            node_map = NodeMap.from_file(str(DEMO_DEVICE_PATH))
            logger.info(f"Using demo device map ({len(node_map.nodes)} nodes)")

    if not node_map:
        node_map = NodeMap(name="mqtt")

    client = MqttClient(
        host=host,
        port=port,
        node_map=node_map,
        poll_interval=poll_interval,
        client_id=client_id,
        username=username,
        password=password,
    )

    zelos_sdk.actions_registry.register(client)

    zelos_sdk.init(name="zelos_extension_mqtt", actions=True)

    if set_client:
        set_client(client)

    client.start()
    handler = TraceLoggingHandler(client.source)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

    try:
        if trace_file:
            with zelos_sdk.TraceWriter(str(trace_file)):
                client.run()
        else:
            client.run()
    finally:
        client.stop()
        if demo_server:
            demo_server.stop()
