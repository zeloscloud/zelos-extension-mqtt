# Changelog

## Unreleased

### Changed

- Needs Zelos 26.0.10 or later.
- The marketplace listing describes what the extension does, in the words people search for.
- Get Status no longer shows in the app's action list; Zelos AI can still run it.

### Fixed

- The demo broker starts without errors or deprecation warnings in the log.
- A topic mapped with `"datatype": "string"` records its text, and no longer stops recording the other events.
- Every device map entry on a topic records its message, so two entries can read different JSON keys of one topic.
- A topic with a `+` or `#` wildcard records the messages it matches, whether added with Subscribe to Topic or listed in the device map. A topic added with Subscribe to Topic by its exact name still records as its own event.
- Logs record as a `log` event next to the topic data, on the device map's source (`mqtt` without a map), instead of on a separate `zelos_extension_mqtt_logger` source. A device map event can no longer be named `log`, and a topic named `log` that Subscribe to Topic picks up is not traced.
- The demo broker no longer writes its connection and internal state lines to the log.
- Saved settings that fail the settings check stop the extension with one `Invalid configuration: ...` error line and exit status 1, instead of a Python traceback.
- A device map file that does not parse (invalid JSON, an unknown datatype, an entry without a topic or name, an event named `log`) stops the extension with one `Invalid device map <file>: ...` error line and exit status 1, instead of a Python traceback.
- Outside demo mode, a device map file that does not exist stops the extension with one `Invalid device map <file>: No such file or directory` error line and exit status 1, instead of being skipped. Demo mode still falls back to the demo map.

## 0.1.1

- The marketplace listing shows the extension's icon, author and repository, and lists it under Networking too.
- The README is the extension's marketplace page: features, quick start, settings, device map format and actions.

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
