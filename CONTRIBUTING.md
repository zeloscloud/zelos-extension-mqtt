# Contributing

## Prerequisites

- [Zelos](https://zeloscloud.io) 26.0.10 or later
- [Zelos CLI](https://docs.zeloscloud.io/getting-started/install-cli/) 0.1.12 or later
- [just](https://github.com/casey/just) and [uv](https://docs.astral.sh/uv/)
- Docker, for `just e2e-interop` only

## Development Workflow

```bash
just install  # dependencies + pre-commit hooks
just run      # run app mode locally (reads ./config.json)
just demo     # run against the built-in broker and simulator
just test     # pytest
just check    # ruff lint + format check
just format   # auto-fix
```

1. Make your changes
2. `just format`
3. `just test`
4. Commit (pre-commit hooks run ruff automatically)

## Project Structure

```
zelos-extension-mqtt/
├── extension.toml                  # Extension manifest (name, version, agent runtime, package paths)
├── config.schema.json              # Configuration UI schema
├── main.py                         # Click entry point
├── pyproject.toml                  # Dependencies, ruff and pytest config
├── uv.lock                         # Locked dependency versions
├── Justfile                        # Development commands
├── LICENSE
├── README.md                       # User documentation (the marketplace page)
├── CHANGELOG.md
├── CLAUDE.md                       # Architecture notes
├── CONTRIBUTING.md                 # This file
├── zelos_extension_mqtt/
│   ├── __init__.py                 # ACTION_PREFIX, package exports
│   ├── constants.py                # Trace layout and name rules
│   ├── mqtt_map.py                 # Device map and topic filter rules
│   ├── client.py                   # Broker connection, tracing, runner, shutdown
│   ├── actions.py                  # Action functions + register_actions()
│   ├── autoconfig.py               # Auto-configure: the form's brokers, this machine, mDNS
│   ├── cli/
│   │   ├── __init__.py
│   │   └── app.py                  # Config load, startup validation, serve()
│   └── demo/
│       ├── __init__.py
│       ├── simulator.py            # Embedded broker and sensor simulator
│       └── demo_device.json        # Demo device map
├── tests/                          # See CLAUDE.md for what each file covers
├── assets/
│   └── icon.png                    # Marketplace icon
└── .github/
    ├── workflows/
    │   ├── CI.yml
    │   └── release.yml
    └── dependabot.yml
```

## Common Tasks

### Add a Dependency

```bash
uv add package-name        # runtime
uv add --dev package-name  # dev
```

Give a new floor an inline comment saying why it is that version.

### Run it in Zelos

```bash
zelos extensions install-local .
zelos extensions start local.mqtt --config '{"brokers": [{"host": "localhost"}]}'
zelos extensions logs local.mqtt
```

A local install runs from your project directory: after a code change, stop and start the
extension; after a dependency change, `zelos extensions reinstall local.mqtt`; after an
`extension.toml` change, `zelos extensions install-local .` again.

To run it outside the agent's process management, write a `config.json` and run `just run`. It
connects to the agent at `ZELOS_AGENT_URL`, else `http://localhost:2300`.

### Package for the Marketplace

```bash
just package   # zelos extensions package .
```

The archive (`mqtt-<version>.tar.gz`) holds `extension.toml`, the README, the icon, the config
schema, `pyproject.toml`, `uv.lock`, `actions.json` (the actions that run while the extension is
stopped: `auto_config`) and the `[package] paths`. CI does this on every push.

### Create a Release

Add the release to `CHANGELOG.md` and commit it, then:

```bash
just release 1.0.0
git push --follow-tags
```

The release workflow checks, tests and packages the extension and attaches the archive to a
GitHub release, which reaches the marketplace.

## Testing

Integration tests start the real demo broker on a loopback port, trace through it and read the
recorded `.trz` back, so they exercise the actual protocol path. `test_e2e.py` runs `main.py`
as the agent does: a saved config, `--file`, a signal.

```bash
just test                        # everything (about 10 s)
uv run pytest -k test_name       # one test
just e2e-interop                 # Mosquitto in Docker, before a release
```

Keep tests targeted. Prefer one integration test that proves a behavior end to end over several
unit tests that assert on internals.

```python
# tests/test_mqtt.py
async def test_every_message_is_traced_at_its_receive_time(tmp_path, demo_broker):
    ...
    assert recorded["Burst/plant/env"]["temp"] == [float(i) for i in range(1, 51)]
```

## Code Quality

- ruff, line length 100, google docstrings
- Type hints on every function signature
- Comments state the constraint or the failure prevented, not what the code does

## Getting Help

- [Zelos Docs](https://docs.zeloscloud.io)
- [SDK Guide](https://docs.zeloscloud.io/sdk)
- [GitHub Issues](https://github.com/zeloscloud/zelos-extension-mqtt/issues)

## License

MIT - see [LICENSE](LICENSE)
