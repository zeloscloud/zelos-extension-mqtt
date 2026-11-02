# Changelog

## Unreleased

Saved settings from 0.2.0 stop the extension with one `Config format changed` line: reconfigure it.

### Added

- Several brokers in one extension (`brokers[]`), with shared settings and per-broker defaults under `advanced`; one unreachable broker never stalls the others.
- Discovery: without a device map every topic received is traced, as an event named after the topic; a JSON object payload gives one field per key (nested keys joined with `_`), anything else the field `value`. `Topics` (default `#`) sets the filters subscribed to; a device map's topics are subscribed too.
- TLS per broker (`tls`), the broker's certificate and host name verified against a CA file or the system trust store, and client certificates for mutual TLS. A failed check stops the connection, never falling back to plain MQTT.
- `_broker` health event per broker: `connected`, `reconnects`, `messages`, `published`.
- `list_topics` action: the topics received, their fields and types, and a device map of them ready to save.
- Auto-configure (the config form's button, `auto_config`): checks the brokers in the form, or finds brokers on this machine (ports 1883 and 8883) and announced over mDNS.
- `advanced.retry_unreachable_at_start` and `advanced.keepalive`.
- A device map `json_key` can be a list of keys into nested JSON objects.
- `demo` runs the extension against the built-in broker and simulator; `demo-server` runs them standalone; `--file` with no name records to a UTC-named `.trz`.

### Changed

- Trace layout: `MQTT/<broker>/<event>`, logs on `MQTT/log`. Clearing `advanced.prefix` gives each broker its own source (`<broker>/<event>`, logs on `mqtt_log/log`). The device map's `name` no longer names the source and is ignored.
- Every message is traced at its receive time; the cached last value per `Log Interval` is gone, so a burst is every message, not one.
- A topic matched by several device map entries records into the most specific one (`a/b/c` beats `a/+/c` beats `a/#`); entries on that one topic all record. Filters that share topics with neither containing the other are refused at load, as are invalid filters, field and event names the trace would rename, and duplicate field names.
- Actions are `MQTT/publish` (now with `retain`) and `MQTT/subscribe`, were `publish_message` and `subscribe_topic`; failures raise instead of returning `success: false`. A topic filter whose messages the subscriptions already receive is not subscribed twice: brokers deliver a copy per matching subscription.
- Payload decode problems (an out-of-range number, a missing JSON key, text that is not UTF-8) warn once per topic and field instead of being dropped silently; a bool field reads only true/false, 1/0, on/off, yes/no.
- `demo` (the subcommand) now traces the demo; the standalone broker moved to `demo-server`.
- Log lines carry UTC ISO 8601 timestamps with milliseconds.

### Removed

- User name and password login; use TLS client certificates.
- The Demo Mode toggle (use `demo` / `--demo`) and `Log Interval` (`poll_interval`).

## 0.2.0

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
