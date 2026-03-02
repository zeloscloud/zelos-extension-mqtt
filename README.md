# Zelos MQTT Extension

MQTT broker monitoring and IoT data collection for [Zelos](https://zeloscloud.io).

Subscribes to MQTT topics, decodes message payloads, and streams data as Zelos trace events. Supports JSON and raw payloads, configurable QoS, and automatic reconnection.

## Features

- Subscribe to any number of MQTT topics with per-topic QoS
- Automatic payload parsing (JSON objects, numbers, raw strings)
- Periodic trace logging with `TraceSourceCacheLast` semantics
- Built-in demo mode with embedded MQTT broker and IoT simulator
- Actions: publish messages, subscribe to topics, view connection status

## Installation

### From Local Development

```bash
zelos extensions install-local /path/to/zelos-extension-mqtt
```

### Start the Extension

```bash
# With configuration from the Zelos app
zelos extensions start local.zelos-extension-mqtt

# In demo mode (embedded broker + simulated sensors)
zelos extensions start local.zelos-extension-mqtt --config '{"demo": true}'
```

## Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `demo` | boolean | `false` | Run with embedded broker and simulated sensors |
| `host` | string | `localhost` | MQTT broker hostname |
| `port` | integer | `1883` | MQTT broker port |
| `client_id` | string | *(auto)* | MQTT client identifier |
| `username` | string | | Broker authentication username |
| `password` | string | | Broker authentication password |
| `device_map_file` | string | | Path to JSON device map file |
| `poll_interval` | number | `1.0` | Trace logging interval in seconds |
| `log_level` | string | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

## Device Map Format

Define MQTT topics grouped into trace events:

```json
{
  "name": "my_device",
  "events": {
    "temperature": [
      {
        "topic": "sensors/temp/ambient",
        "name": "ambient_temp",
        "datatype": "float32",
        "unit": "C"
      }
    ],
    "controls": [
      {
        "topic": "device/setpoint",
        "name": "setpoint",
        "datatype": "float32",
        "unit": "C",
        "writable": true
      }
    ]
  }
}
```

### Node Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `topic` | string | *(required)* | MQTT topic to subscribe to |
| `name` | string | *(required)* | Signal name in the trace |
| `datatype` | string | `float32` | Data type (bool, uint8-64, int8-64, float32/64, string) |
| `unit` | string | `""` | Unit of measurement |
| `qos` | integer | `0` | MQTT QoS level (0, 1, or 2) |
| `payload_type` | string | `auto` | Payload format: `auto`, `json`, or `raw` |
| `json_key` | string | `value` | Key to extract from JSON payloads |
| `writable` | boolean | `false` | Whether this topic can be published to |

## Actions

| Action | Description |
|--------|-------------|
| **Get Status** | View connection state, message count, and error stats |
| **Publish Message** | Publish a message to any MQTT topic |
| **Subscribe to Topic** | Subscribe to an additional topic at runtime |

## Development

```bash
# Install dependencies
just install

# Run linter
just check

# Format code
just format

# Run tests (51 tests, ~1s)
just test

# Run in demo mode
just dev
```

## CLI

```bash
# Run extension with Zelos app configuration
python main.py

# Run in demo mode
python main.py --demo

# Run standalone demo broker
python main.py demo --host 127.0.0.1 --port 11883
```
