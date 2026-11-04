#!/usr/bin/env python3
"""Zelos MQTT Extension - CLI entry point.

Modes:

1. App mode (default): configuration comes from the Zelos App
2. Demo mode: built-in broker and simulated sensors, no broker needed

Examples:
    uv run main.py                          # app mode
    uv run main.py demo                     # built-in broker and simulator
    uv run main.py --file run.trz demo      # ... and record to a .trz file
    uv run main.py demo-server --port 1883  # standalone broker and simulator
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

import rich_click as click

from zelos_extension_mqtt import ACTION_PREFIX as _ACTION_PREFIX
from zelos_extension_mqtt.cli import app as app_mode

#: Re-exported so a packaging-time action inventory - which reads this entry
#: module - sees the same namespace the live registration uses. See the
#: definition in `zelos_extension_mqtt/__init__.py`.
ACTION_PREFIX = _ACTION_PREFIX

# UTC ISO 8601 with ms, matching the SDK's Rust tracing lines in the same log stream
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03dZ %(levelname)5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# aiomqtt logs every packet through paho, and amqtt every connection: only DEBUG
# re-opens them (cli/app.apply_log_level).
for _name in app_mode.CHATTY_LOGGERS:
    logging.getLogger(_name).setLevel(logging.WARNING)


@click.group(invoke_without_command=True)
@click.option("--demo", is_flag=True, help="Run against the built-in broker and simulator")
@click.option(
    "--file",
    type=click.Path(path_type=Path),
    default=None,
    is_flag=False,
    flag_value=".",
    help="Record the trace to a .trz file (a UTC-named one when no name is given)",
)
@click.pass_context
def cli(ctx: click.Context, demo: bool, file: Path | None) -> None:
    """Zelos MQTT Extension - trace MQTT topics, publish and subscribe.

    Without a subcommand this starts in app mode using the configuration saved
    by the Zelos App. Use --demo or the 'demo' subcommand for the built-in broker
    and simulator.
    """
    if ctx.invoked_subcommand is None:
        app_mode.run_app_mode(demo=demo, trace_file=file)


@cli.command()
@click.pass_context
def demo(ctx: click.Context) -> None:
    """Run against the built-in broker and simulator.

    Starts an MQTT broker on 127.0.0.1:11883 publishing simulated temperature,
    humidity, pressure, power and device topics, then traces it as broker `demo`.
    No broker required.
    """
    app_mode.run_app_mode(demo=True, trace_file=ctx.parent.params["file"])


@cli.command("demo-server")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address")
@click.option("--port", type=int, default=app_mode.DEMO_PORT, show_default=True, help="TCP port")
@click.option(
    "--interval", type=float, default=0.5, show_default=True, help="Seconds between publishes"
)
def demo_server(host: str, port: int, interval: float) -> None:
    """Run the built-in broker and simulator standalone until Ctrl-C."""
    from zelos_extension_mqtt.demo.simulator import DemoServer

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    server = DemoServer(host=host, port=port, publish_interval=interval)
    try:
        server.start()
    except OSError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Demo MQTT broker on {host}:{port}; Ctrl-C to stop")
    while not stop.wait(0.5):  # a timed wait, so signals land on Windows too
        pass
    server.stop()


if __name__ == "__main__":
    cli()
