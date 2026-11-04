# CLAUDE.md

## Commands

```bash
just install      # deps + pre-commit hooks
just format       # ruff format + ruff check --fix
just check        # ruff format --check + ruff check
just test         # pytest (integration tests start the real demo broker)
just e2e-interop  # Mosquitto in Docker: plain, TLS, mutual TLS, refusals; manual, pre-release
just run          # app mode, reads the Zelos App config
just demo         # built-in broker and simulator
just sim *ARGS    # standalone broker and simulator: --host, --port, --interval
just package      # zelos extensions package . (needs Zelos CLI 0.1.12+ for the [agent] manifest)
```

## Key Files

| Path | Role |
|------|------|
| `main.py` | Click entry point; re-exports `ACTION_PREFIX` |
| `zelos_extension_mqtt/__init__.py` | `ACTION_PREFIX = "MQTT"` - must match `name` in `extension.toml` |
| `zelos_extension_mqtt/constants.py` | `trace_layout()`, `name_error()`, `DEFAULT_PREFIX`, `LOG_SOURCE_NAME`, `HEALTH_EVENT` |
| `zelos_extension_mqtt/mqtt_map.py` | Node/NodeMap parsing and validation; topic filter rules (`covers`, `intersection`, `check_overlaps`, `subscriptions`); most specific match |
| `zelos_extension_mqtt/client.py` | `BrokerClient` (one broker: connection, subscriptions, decoding, discovery, health) and `MqttRunner` (the loop, signals, first contact, action dispatch, shutdown); TLS context; error descriptions |
| `zelos_extension_mqtt/actions.py` | Free-function action surface + `register_actions()` |
| `zelos_extension_mqtt/autoconfig.py` | `auto_config` sources: the form's brokers, localhost 1883/8883, mDNS |
| `zelos_extension_mqtt/cli/app.py` | Config load, `brokers[]` / `advanced` resolution, startup validation, `serve()` |
| `zelos_extension_mqtt/demo/simulator.py` | Embedded amqtt broker + sensor simulator (`DemoServer`) |
| `zelos_extension_mqtt/demo/demo_device.json` | Demo device map |
| `tests/conftest.py` | Free ports, `demo_broker` fixture, `read_trace`, `make_pki` |
| `tests/test_config.py` | Defaults vs schema, manifest/autoconfig wiring, action flags, one-line config errors, names, TLS inheritance, matching |
| `tests/test_mqtt.py` | Against the demo broker: every message traced, discovery, decode warnings, actions, first contact, retry and reconnect |
| `tests/test_tls.py` | In-process TLS broker with a test CA: verified connect, refusals |
| `tests/test_autoconfig.py` | `auto_config` on the form's brokers and on this machine |
| `tests/test_e2e.py` | `main.py` subprocess with a saved config and `--file`: the recorded layout, prefix and cleared |
| `tests/test_interop.py` | Mosquitto in Docker; `ZELOS_INTEROP=1` only |

## Architecture

### Startup

- `cli/app.serve()` is the only place that starts clients: config -> clients -> `set_runner` -> `register_actions` -> `open_sources(prefix)` -> `zelos_sdk.init(name=ACTION_PREFIX)` -> `client.start` -> `runner.run()`.
- `open_sources` makes the SDK's global source (`prefix`, cleared: `mqtt_log`) before `init()`, so init reuses it instead of adding an empty one, and installs the trace log handler (INFO+) on its `log` event. Earlier records reach stderr only.
- Registration must precede `init()` (later actions may never be advertised).
- Startup problems (bad config, device map, TLS files, overlapping filters, unreachable broker) are one `logger.error` + `sys.exit(1)`; tracebacks are for bugs.
- `--file` opens the `TraceWriter` before any source exists; `--file` with no name records `<UTC>.trz`.

### Config

- `run_app_mode` checks the raw file for keys outside `brokers` / `advanced` / `demo` before `load_config`: an old flat config (host, username, password, poll_interval) is "Config format changed", not a schema error. `demo` is the removed toggle: ignored.
- Per broker, `""` TLS files inherit `advanced`; the client cert and key as a pair. `tls_context` is built at config load: verification on (CA file, else the system store); cert + key = mTLS.
- Names: typed names are rejected (`name_error`), never rewritten; default = `sanitize_name(host)`, colliding defaults take `_<port>`; duplicates and `log` / `mqtt_log` exit.
- Demo replaces `brokers` with one broker `demo` on the built-in broker (127.0.0.1:11883) and the demo map; `advanced` still applies.

### Topics and matching

- Wanted filters = `topics` (QoS 0) + every map topic (its `qos`) + the `subscribe` action's. `check_overlaps` refuses two wildcard filters that share topics with neither containing the other (exact topics are disjoint or contained, so only wildcard pairs are compared).
- `subscriptions()` drops covered filters and gives the cover the highest QoS: brokers (amqtt, Mosquitto 2) deliver one copy per matching subscription, so a covered filter would record twice.
- A received topic records into the most specific map filter: exact first, else the innermost of the matching wildcards (they form a chain once overlaps are refused). All entries on that one filter record.
- Spec 4.7.2 is applied in `matches` / `covers` / `intersection` (aiomqtt's `Topic.matches` does not): a first-level `+` or `#` never reaches a `$` topic, so `#` neither receives, covers nor overlaps `$SYS/...`; such topics trace only under a filter that names them.
- Unmapped topics are discovered: event = topic levels each through `sanitize_name(kind="event")`. Collisions (two topics, one event name; a map event's name) are not traced, warned once.

### Tracing

- Every message is one row at its receive time (`log_at(time.time_ns())`); no cache, no flush interval. Rows carry only the fields that message decoded.
- Discovery fields come from the first message (`payload_fields`): JSON object leaves, nested keys joined `_`, arrays as JSON text, nulls skipped; numbers are float64 (JSON has one number type). A declared schema is fixed per `TraceSource`, so a key that appears later is warned once and not traced (no source rotation); `list_topics` reports it and the device map it returns declares it.
- Decode failures warn once per (topic, field): `_warn_once`. A bug in decoding is one `logger.exception` per topic; the other topics keep tracing.
- `_broker` health: `connected`, `reconnects`, `messages`, `published`, logged on connect, on disconnect and every `HEALTH_SECONDS`.

### Actions

- Free functions in `actions.py`, bound via `set_runner`. Optional `broker`: `ValueError` listing names when omitted with several; `get_status` / `list_topics` cover all.
- Failure convention: **raise**. Input errors `ValueError`; not connected / not running `RuntimeError`; aiomqtt errors become `RuntimeError` via `_run`.
- `publish` / `subscribe` dispatch via `MqttRunner.run_coro` onto the loop that owns the connection; no connect-per-action. The SDK runs each call on its own thread, so `subscribe` holds a per-broker lock (concurrent calls otherwise unsubscribed each other) and records the new cover once granted, before UNSUBSCRIBE of the covered ones; it may wait for two acknowledgements.
- `auto_config(config)` is standalone (own `asyncio.run`); the schema's `ui:options.autoconfig` names it, keep in step. It connects (CONNECT/CONNACK, DISCONNECT) and subscribes to nothing. mDNS falls back to `InterfaceChoice.Default` where binding every interface fails (macOS: the system responder owns 127.0.0.1:5353).

### Connection

- aiomqtt (paho) per broker, MQTT 3.1.1, clean session: every (re)connect re-subscribes the wanted filters; retained messages arrive again and are traced again.
- Connect errors are typed through aiomqtt's `MqttError.__context__` (it re-raises OSError `from None`); `describe_error` maps them to a reason and the fix. A TLS 1.3 alert (client certificate required) reaches only paho's log, so each client has its own unregistered paho logger whose last ERROR feeds the line (`paho_logger`); paho's lines reach the log at DEBUG only.
- aiomqtt private relied on, recheck on a bump: `Client._client` (the paho client) for `connect_timeout` in `auto_config`.

### Shutdown

- `MqttRunner` owns the one loop; signals via `loop.add_signal_handler` only set the stop event (Windows: `signal.signal` fallback); never `sys.exit` from a handler.
- First contact is required unless `retry_unreachable_at_start`: one ERROR per broker naming the fix; `serve()` exits 1. Afterwards drops reconnect with backoff 3 s doubling to 60 s (reset on a connect).
- Stop cancels every client; disconnects run concurrently under `SHUTDOWN_TIMEOUT` (3 s). aiomqtt runs paho's blocking connect (TCP up to 5 s, TLS handshake up to keepalive) in the loop's default executor, which a cancel does not stop and `asyncio.run` / interpreter exit would join: the runner's loop gets `_DaemonExecutor` (daemon threads, nothing joined).
- First contact waits on plain tasks, not a gather: a cancelled gather logs "exception was never retrieved" (an ERROR with a traceback, into the trace too).
- A client task that ends (a bug) stops the runner and `serve()` exits 1.

### Demo broker

- amqtt sends a retained message twice to a later client; Mosquitto once (`test_interop`). Do not assert retained counts against the demo broker.
- `DemoServer.start()` raises OSError at once when the port is taken.

## Code Style

- ruff, line length 100, google docstrings
- `contextlib.suppress(Exception)` over bare `try/except: pass`
- comments state the constraint, not the code
