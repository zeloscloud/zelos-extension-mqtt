# Zelos extension for MQTT

## Features

- 📊 **Real-time MQTT monitoring** - Subscribe to topics and stream data as Zelos trace events
- 📄 **Flexible payload parsing** - JSON objects, numbers, booleans, and raw strings
- ⚙️ **Device map configuration** - Map MQTT topics to typed, named signals with units
- 📤 **Publish and subscribe** - Send messages and add topics at runtime from the Zelos App
- 🔄 **Dynamic topic tracing** - Runtime-subscribed topics auto-traced with schema inference
- 🚀 **Demo mode** - Built-in MQTT broker with simulated IoT sensors for testing

## Quick Start

1. **Install** the extension from the Zelos App
2. **Configure** your MQTT broker connection and provide a device map (`.json`)
3. **Start** the extension to begin streaming data
4. **View** real-time data in your Zelos App

## Configuration

All configuration is managed through the Zelos App settings interface.

### Required Settings
- **Broker Host**: MQTT broker hostname or IP address
- **Broker Port**: MQTT broker port (default: 1883)

### Optional Settings
- **Demo Mode**: Run with embedded broker and simulated IoT sensors
- **Device Map File**: JSON file defining MQTT topics to subscribe to
- **Client ID**: MQTT client identifier (auto-generated if empty)
- **Username / Password**: Broker authentication credentials
- **Log Interval**: How often to flush cached values to the trace (default: 1.0s)
- **Log Level**: Control log verbosity (DEBUG, INFO, WARNING, ERROR)

## Device Map Format

Define MQTT topics grouped into trace events:

```json
{
  "name": "my_device",
  "events": {
    "temperature": [
      { "topic": "sensors/temp/ambient", "name": "ambient_temp", "datatype": "float32", "unit": "°C" }
    ],
    "controls": [
      { "topic": "device/setpoint", "name": "setpoint", "datatype": "float32", "unit": "°C", "writable": true }
    ]
  }
}
```

Supported data types: `bool`, `uint8`, `int8`, `uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64`, `float64`, `string`

## Actions

The extension provides several actions accessible from the Zelos App:

- **Get Status**: View MQTT connection state, message count, and error stats
- **Publish Message**: Send a message to any MQTT topic with configurable QoS
- **Subscribe to Topic**: Subscribe to an additional topic at runtime (auto-traced via schema inference)

## What is MQTT?

[See this introduction](https://mqtt.org/getting-started/)

## Development

Want to contribute or modify this extension? See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete developer guide.

## Links

- **Repository**: [github.com/zeloscloud/zelos-extension-mqtt](https://github.com/zeloscloud/zelos-extension-mqtt)
- **Issues**: [Report bugs or request features](https://github.com/zeloscloud/zelos-extension-mqtt/issues)

## CLI Usage

The extension includes a command-line interface for advanced use cases. No installation required - just use `uv run`:

```bash
# Run extension with Zelos app configuration
uv run main.py

# Run in demo mode (embedded broker + simulated sensors)
uv run main.py --demo

# Run in demo mode and record to .trz file
uv run main.py --demo --file

# Run standalone demo broker
uv run main.py demo --host 127.0.0.1 --port 11883
```

## Support

For help and support:
- 📖 [Zelos Documentation](https://docs.zeloscloud.io)
- 🐛 [GitHub Issues](https://github.com/zeloscloud/zelos-extension-mqtt/issues)
- 📧 help@zeloscloud.io

## License

MIT License - see [LICENSE](LICENSE) for details.

---

**Built with [Zelos](https://zeloscloud.io)**
