# Zelos MQTT

A Zelos extension for MQTT. Trace every topic on one or more brokers as it arrives, or map topics to typed, named signals with a device map, and publish or subscribe from the Zelos App.

## Features

- 📡 **Every message**: Each message is traced at its receive time; nothing is sampled or dropped
- 🔍 **Discovery**: No device map needed; every topic received becomes an event named after it, JSON keys its fields
- 📄 **Device maps**: Map topics (with `+` and `#` wildcards) to typed fields with units, grouped into events
- 🔒 **Secure connections**: TLS with server verification, client certificates for mutual TLS
- 🏭 **Multiple brokers**: Any number in one extension; one unreachable broker never stalls the others
- ✏️ **Publish & subscribe actions**: Send messages and add topic filters from the Zelos App
- 🧪 **Demo mode**: Built-in broker with simulated sensors for testing without a broker

## Quick Start

From the CLI, on the agent that can reach the broker:

```bash
zelos extensions install zeloscloud/zelos-extension-mqtt
zelos extensions start zeloscloud.zelos-extension-mqtt \
  --config '{"brokers": [{"host": "192.168.1.100"}]}'
```

In the app:

1. **Install** the extension from the Zelos App
2. **Configure** your brokers (host, and a device map file or nothing: discovery), or press Auto-configure
3. **Start** the extension to begin streaming data
4. **View** real-time topic values in your Zelos App

## Configuration

### Brokers

| Setting | Default | Description |
|---------|---------|-------------|
| Name | host | Trace name (`plant`, `192_168_1_10`); unique |
| Host | `localhost` | Broker host name or IP address |
| Port | `1883` | `1883` plain, `8883` TLS |
| Topics | `#` | Topic filters subscribed to; cleared = only the device map's |
| Device Map File | | JSON [device map](#device-map); empty = [discovery](#discovery) |
| Client ID | generated | MQTT client identifier |
| TLS | off | See [Secure connections](#secure-connections) |
| CA File / Client Certificate / Client Key | | Per-broker TLS files; empty = Advanced's |

### Advanced

Applies to every broker unless the broker overrides it.

| Setting | Default | Description |
|---------|---------|-------------|
| `prefix` | `MQTT` | Trace source: `MQTT/plant/environment`; cleared = one source per broker |
| `log_level` | `INFO` | Logging verbosity |
| `retry_unreachable_at_start` | off | Retry a broker down at start instead of stopping |
| `keepalive` | `60` | MQTT keepalive (s) |
| `ca_file` | | CA certificate (PEM) for TLS brokers; empty = system trust store |
| `cert_file` / `key_file` | | Client certificate and key (PEM) for TLS brokers |

Every broker must connect at start, or the extension stops with an error naming it and the fix; `retry_unreachable_at_start` instead retries it while the others trace. Once running, a dropped connection is retried with backoff (3 s doubling to 60 s).

### Secure connections

Turn on TLS for a broker: its certificate and host name are verified against the CA File (or the system trust store), and a failed check stops the connection; it never falls back to plain MQTT. Add a client certificate and key for brokers that require mutual TLS. There is no user name / password login.

### Discovery

Without a device map, every topic received on the subscribed filters is traced as an event named after the topic, each level sanitized (`sensors/temp/ambient`). A JSON object payload gives one field per key, nested keys joined with `_` (`fan_rpm`); a number, boolean or text payload is the field `value`. The first message on a topic fixes its fields; `list_topics` returns them as a device map ready to save.

### Auto-configure

The config form's Auto-configure button checks the brokers in the form with an MQTT connect, or with none, finds brokers on this machine (ports 1883 and 8883) and announced over mDNS, and adds them.

### Trace layout

With the default prefix, one source carries every broker.

| What | Prefix `MQTT` | Prefix cleared |
|---|---|---|
| Device map event | `MQTT/plant/environment` | `plant/environment` |
| Discovered topic `sensors/temp` | `MQTT/plant/sensors/temp` | `plant/sensors/temp` |
| Broker health | `MQTT/plant/_broker` | `plant/_broker` |
| Extension logs | `MQTT/log` | `mqtt_log/log` |

`_broker` records `connected`, `reconnects`, `messages` and `published` on connect, on disconnect and every 5 s.

## Device Map

```json
{
  "events": {
    "environment": [
      {"topic": "sensors/temperature", "name": "temperature", "datatype": "float32", "unit": "°C"},
      {"topic": "sensors/env", "name": "humidity", "json_key": "humidity", "unit": "%"}
    ],
    "pumps": [
      {"topic": "plant/+/speed", "name": "rpm", "datatype": "uint16", "json_key": ["drive", "rpm"]}
    ]
  }
}
```

Event keys become trace events, entry names their fields. A topic matched by several entries' filters records into the most specific one (`plant/pump1/speed` beats `plant/+/speed` beats `plant/#`); filters that share topics with neither containing the other are refused at load. Several entries on one topic each read their own key.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `topic` | Yes | | Topic or filter (`+` one level, `#` the rest) |
| `name` | Yes | | Field name |
| `datatype` | No | `float32` | bool, int8-64, uint8-64, float32, float64, string |
| `unit` | No | | Display unit |
| `json_key` | No | `value` | Key of a JSON object payload, or a list of keys into nested objects |
| `payload_type` | No | `auto` | `auto` (JSON object if it starts with `{`), `json`, `raw` |
| `qos` | No | `0` | Subscription QoS |

## Actions

| Action | Description |
|--------|-------------|
| `get_status` | Connection state, subscriptions, counters |
| `list_topics` | Topics received, their fields and types, and a device map of them |
| `publish` | Publish a message (QoS 0-2, retain) |
| `subscribe` | Add a topic filter for this run |
| `auto_config` | Find brokers for the config form |

With several brokers, pass `broker` to pick one.

```bash
zelos actions execute MQTT/publish --params '{"topic":"plant/pump1/setpoint","payload":"42","qos":1,"broker":"plant"}'
```

## Development

```bash
just install      # deps + pre-commit hooks
just format       # ruff format + fix
just check        # ruff lint + format check
just test         # pytest
just run          # run app mode locally
just demo         # built-in broker and simulator
just sim          # standalone broker and simulator: --host, --port, --interval
```

## Links

- [Zelos Documentation](https://docs.zeloscloud.io)
- [SDK Guide](https://docs.zeloscloud.io/sdk)
- [MQTT 3.1.1 specification](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)
- [GitHub Issues](https://github.com/zeloscloud/zelos-extension-mqtt/issues)

## CLI Usage

The extension includes a command-line interface for running without the Zelos App. No installation required, just use `uv run`:

```bash
uv run main.py                                 # app mode (Zelos App config)
uv run main.py demo                            # built-in broker and simulator
uv run main.py --file run.trz demo             # ... and record to a .trz file
uv run main.py demo-server --port 1883         # standalone broker and simulator
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
