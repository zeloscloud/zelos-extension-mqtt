#!/usr/bin/env python3
"""Zelos MQTT extension - MQTT broker monitoring and IoT data collection."""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

import rich_click as click

if TYPE_CHECKING:
    from zelos_extension_mqtt.client import MqttClient

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.USE_MARKDOWN = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_client: MqttClient | None = None


def shutdown_handler(signum: int, frame: FrameType | None) -> None:
    """Handle graceful shutdown on SIGTERM or SIGINT."""
    logger.info("Shutting down...")
    if _client:
        _client.stop()
    sys.exit(0)


@click.group(invoke_without_command=True)
@click.option("--demo", is_flag=True, help="Run in demo mode with embedded broker and simulator")
@click.option(
    "--file",
    type=click.Path(path_type=Path),
    default=None,
    is_flag=False,
    flag_value=".",
    help="Record trace to .trz file",
)
@click.pass_context
def cli(ctx: click.Context, demo: bool, file: Path | None) -> None:
    """MQTT broker monitoring and IoT data collection.

    Subscribes to MQTT topics and streams data as Zelos trace events.
    Configure via Zelos extension settings or use --demo for testing.
    """
    if ctx.invoked_subcommand is not None:
        return

    run_app_mode(demo=demo, trace_file=file)


def run_app_mode(demo: bool = False, trace_file: Path | None = None) -> None:
    """Run in app mode — delegates to app runner for SDK init and client setup."""
    # Register signal handlers
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Delegate to app mode runner (handles SDK init, action registration, client lifecycle)
    from zelos_extension_mqtt.cli.app import run_app_mode as _run_app_mode

    _run_app_mode(demo=demo, trace_file=trace_file, set_client=_set_client)


def _set_client(client: MqttClient) -> None:
    """Store client reference for shutdown handler."""
    global _client
    _client = client


@cli.command()
@click.option("--host", default="127.0.0.1", help="Broker host")
@click.option("--port", default=11883, type=int, help="Broker port")
@click.option("--interval", default=0.5, type=float, help="Publish interval in seconds")
def demo(host: str, port: int, interval: float) -> None:
    """Run standalone demo broker with simulated IoT data."""
    from zelos_extension_mqtt.demo.simulator import DemoServer

    server = DemoServer(host=host, port=port, publish_interval=interval)
    server.start()
    click.echo(f"Demo MQTT broker running on {host}:{port}")
    click.echo("Press Ctrl+C to stop")

    def shutdown(signum, frame):
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        signal.pause()
    except AttributeError:
        import time

        while True:
            time.sleep(1)


if __name__ == "__main__":
    cli()
