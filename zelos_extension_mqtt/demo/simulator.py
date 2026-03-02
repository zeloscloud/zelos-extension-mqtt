"""Demo MQTT broker and IoT device simulator.

Runs an embedded MQTT broker (amqtt) and publishes realistic sensor data
that changes over time. Used for demo mode and integration tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import socket
import threading
import time

import aiomqtt
from amqtt.broker import Broker

logger = logging.getLogger(__name__)


class IoTSimulator:
    """Simulates IoT sensor readings with realistic variation."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.error_count = 0
        self.setpoint = 22.0

    def update(self, dt: float) -> dict[str, float | int]:
        """Generate current sensor readings.

        Returns:
            Dictionary mapping topic suffixes to values.
        """
        t = time.time() - self.start_time

        # Temperature: ambient with slow sinusoidal drift + noise
        ambient_temp = 22.0 + 3.0 * math.sin(t * 0.05) + random.gauss(0, 0.2)
        surface_temp = ambient_temp + 5.0 + 1.5 * math.sin(t * 0.08) + random.gauss(0, 0.3)

        # Humidity: inversely correlated with temperature
        humidity = 55.0 - 10.0 * math.sin(t * 0.05) + random.gauss(0, 1.0)
        humidity = max(20.0, min(95.0, humidity))

        # Pressure: slow atmospheric drift
        pressure = 1013.25 + 5.0 * math.sin(t * 0.02) + random.gauss(0, 0.5)

        # Power: voltage with small ripple, current varies with load
        voltage = 12.0 + 0.3 * math.sin(t * 0.1) + random.gauss(0, 0.05)
        load_factor = 1.0 + 0.4 * math.sin(t * 0.03)
        current = max(0, 2.5 * load_factor + random.gauss(0, 0.1))
        power = voltage * current

        # Device: uptime and occasional simulated errors
        uptime = int(t)
        if random.random() < 0.002:
            self.error_count += 1

        return {
            "sensors/temperature/ambient": round(ambient_temp, 2),
            "sensors/temperature/surface": round(surface_temp, 2),
            "sensors/humidity": round(humidity, 2),
            "sensors/pressure": round(pressure, 2),
            "sensors/power/voltage": round(voltage, 3),
            "sensors/power/current": round(current, 3),
            "sensors/power/watts": round(power, 2),
            "device/uptime": uptime,
            "device/errors": self.error_count,
            "device/setpoint": round(self.setpoint, 1),
        }


class DemoServer:
    """Combined MQTT broker + IoT simulator for demo and testing.

    Runs an amqtt broker and a publisher that sends simulated sensor data.
    The publisher connects to the broker as a normal MQTT client.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11883,
        publish_interval: float = 0.5,
    ) -> None:
        self.host = host
        self.port = port
        self.publish_interval = publish_interval
        self.endpoint = f"{host}:{port}"
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self.simulator = IoTSimulator()

    def start(self) -> None:
        """Start broker and publisher in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait for TCP readiness instead of sleeping
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError("Demo server failed to start within 10s")
        logger.info(f"Demo MQTT server ready on {self.host}:{self.port}")

    def _run(self) -> None:
        """Run broker + publisher event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()

        try:
            self._loop.run_until_complete(self._run_async())
        except Exception:
            logger.exception("Demo server error")
        finally:
            self._loop.close()

    async def _run_async(self) -> None:
        """Start broker, wait for readiness, then run publisher."""
        broker_config = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self.host}:{self.port}",
                }
            },
            "sys_interval": 0,
            "auth": {"allow-anonymous": True, "plugins": ["auth_anonymous"]},
            "topic-check": {"enabled": False},
        }

        broker = Broker(broker_config)
        await broker.start()

        # Poll for TCP readiness
        self._wait_for_tcp_ready()
        self._ready.set()

        try:
            await self._publish_loop()
        finally:
            await broker.shutdown()

    def _wait_for_tcp_ready(self) -> None:
        """Poll until the broker accepts TCP connections."""
        for _ in range(100):
            try:
                sock = socket.create_connection((self.host, self.port), timeout=0.1)
                sock.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"Broker not accepting connections on {self.host}:{self.port}")

    async def _publish_loop(self) -> None:
        """Connect to broker and publish simulated sensor data."""
        async with aiomqtt.Client(self.host, self.port) as client:
            while not self._stop_event.is_set():
                values = self.simulator.update(self.publish_interval)
                for topic, value in values.items():
                    payload = json.dumps(value)
                    await client.publish(topic, payload.encode(), qos=0)

                try:
                    await asyncio.wait_for(self._stop_event.wait(), self.publish_interval)
                    break  # Stop event was set
                except TimeoutError:
                    pass  # Continue publishing

    def stop(self) -> None:
        """Stop the demo server."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("Demo MQTT server stopped")
