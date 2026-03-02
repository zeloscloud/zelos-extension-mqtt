# Changelog

## 0.1.0

- Initial release
- MQTT broker connection with configurable host, port, auth
- Device map (JSON) for mapping MQTT topics to Zelos trace events
- Supported data types: bool, uint8-64, int8-64, float32, float64, string
- JSON and raw payload parsing with auto-detection
- Actions: Get Status, Publish Message, Subscribe to Topic
- Dynamic topic subscription with SDK schema inference via `log_dict`
- Demo mode with embedded broker and simulated IoT sensors
- Automatic reconnection on broker disconnect
