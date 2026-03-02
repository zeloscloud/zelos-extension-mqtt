# CLAUDE.md - Zelos MQTT Extension

## Project Overview

Zelos extension for MQTT broker monitoring and IoT data collection. Subscribes to MQTT topics, decodes payloads, and streams data as Zelos trace events.

## Structure

```
zelos-extension-mqtt/
├── main.py                      # Entry point (CLI with app/demo modes)
├── extension.toml               # Zelos extension manifest
├── config.schema.json           # Config UI schema
├── pyproject.toml               # Dependencies
├── Justfile                     # Task runner
├── zelos_extension_mqtt/
│   ├── client.py                # MqttClient - core client with SDK integration
│   ├── mqtt_map.py              # Node/NodeMap data model
│   ├── cli/app.py               # App mode runner
│   └── demo/
│       ├── simulator.py         # Embedded MQTT broker + IoT simulator
│       └── demo_device.json     # Demo device map (10 signals)
└── tests/
    └── test_mqtt.py             # 51 tests (unit + integration)
```

## Quick Commands

```bash
just install     # Install deps
just check       # Lint (ruff)
just format      # Format (ruff)
just test        # Run tests
just dev         # Run in demo mode
```

## Architecture

- **Client** (`client.py`): Async MQTT client using `aiomqtt`. Subscribes to topics from a `NodeMap`, caches latest values, and periodically flushes them to `TraceSourceCacheLast`.
- **Data Model** (`mqtt_map.py`): `Node` (topic + metadata) and `NodeMap` (events grouping nodes). Loaded from JSON files.
- **Simulator** (`simulator.py`): Embedded `amqtt` broker + `IoTSimulator` that publishes realistic sensor data. Used for demo mode and integration tests.
- **Actions**: Get Status, Publish Message, Subscribe to Topic. Async operations from sync action handlers use `asyncio.run_coroutine_threadsafe()`.

## Key Patterns

- MQTT is event-driven (push), not poll-based. Messages arrive via subscription callbacks and are cached. A periodic task logs cached values to the trace source at `poll_interval`.
- Payload parsing supports: plain numbers (`23.5`), JSON objects (`{"value": 23.5}`), and raw strings. Controlled by `payload_type` and `json_key` on each Node.
- `DemoServer` runs broker + publisher in a background thread with its own event loop. TCP readiness is verified by socket polling, not sleep.

## Testing

Integration tests use a real `amqtt` broker (no mocks). The `demo_server` fixture starts an embedded broker with simulated IoT data. Tests verify: data model, payload parsing, simulator physics, real MQTT pub/sub, client caching, actions, and error handling.

## Dependencies

- `aiomqtt` - Async MQTT client (wraps paho-mqtt)
- `amqtt` - Embedded MQTT broker for demo/tests
- `zelos-sdk` - Zelos trace and action SDK
- `rich-click` - CLI framework
