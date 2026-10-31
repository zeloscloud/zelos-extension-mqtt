# Contributing

## Prerequisites

- [Zelos](https://zeloscloud.io) 26.0.10 or later
- [Zelos CLI](https://docs.zeloscloud.io/getting-started/install-cli/) 0.1.12 or later
- [just](https://github.com/casey/just)
- [uv](https://docs.astral.sh/uv/)

## Layout

`extension.toml` describes everything the extension contributes:

| Part | Files | Manifest |
| ---- | ----- | -------- |
| Agent runtime | `main.py`, the Python package, `pyproject.toml`, `config.schema.json` | `[agent]`, `[config]` |

```
main.py                       # CLI: app mode (default, `--demo`, `--file`) and `demo`
zelos_extension_mqtt/
  client.py                   # MqttClient: subscribes to topics, writes trace events, registers actions
  mqtt_map.py                 # Node and NodeMap: topics, payload types and events
  cli/app.py                  # App mode runner
  demo/simulator.py           # Embedded amqtt broker and sensor simulator
  demo/demo_device.json       # Device map for the simulator
tests/test_mqtt.py            # Unit and integration tests
```

## Commands

| Command                | Description                                                     |
| ---------------------- | --------------------------------------------------------------- |
| `just install`         | Install dependencies and the pre-commit hooks                   |
| `just format`          | Format code with ruff                                           |
| `just check`           | Check formatting and lint (ruff)                                |
| `just test`            | Run tests (pytest)                                              |
| `just package`         | Package for the Zelos marketplace                               |
| `just release VERSION` | Bump version, check, test, commit, tag                          |
| `just clean`           | Remove build output, caches and packages                        |
| `just run`             | Run the extension against `./config.json`                       |
| `just dev-demo`        | Run the extension with an embedded broker and simulated sensors |
| `just demo`            | Run only the demo broker, on `127.0.0.1:11883`                  |

`just --list` also shows the recipes each part adds. `just install` installs the
pre-commit hooks in `.pre-commit-config.yaml`.

## Run it in Zelos

```bash
zelos extensions install-local .
zelos extensions start local.mqtt --config '{"demo": true}'
zelos extensions logs local.mqtt
```

A local install runs from your project directory, so you do not install again after a code
change:

- Agent runtime: stop and start the extension. After a dependency change, stop it and run
  `zelos extensions reinstall local.mqtt`.
- `extension.toml`: run `zelos extensions install-local .` again.

To run it outside the agent's process management, write a `config.json` and run `just run`.
It connects to the agent at `ZELOS_AGENT_URL`, else `http://localhost:2300`:

```bash
echo '{"demo": true}' > config.json
just run
```

## Demo mode

Demo mode starts an embedded broker with simulated sensors and connects to it. It needs no broker.
Turn it on with `"demo": true` in the config, or run `just dev-demo`. The broker listens on the
configured `port`, 1883 by default. `just demo` runs only the broker, with no extension and no Zelos.

## Testing

The tests start the embedded broker on a free localhost port and publish through it. They need no network.

## Packaging

```bash
just package
```

The archive lands next to `extension.toml` as `<name>-<version>.tar.gz`, where `<name>` is the
manifest name in lowercase with every other run of characters turned into one hyphen. It holds
`extension.toml`, the README, the icon, the config schema and the paths listed in
`[package] paths`. With an agent runtime it also holds `pyproject.toml`, `uv.lock` and
`actions.json`, the list of actions that run while the extension is stopped.

## Release

Add the release to `CHANGELOG.md` and commit it, then:

```bash
just release 1.0.0
git push --follow-tags
```

The release workflow checks, tests and packages the extension, then attaches the archive to a
GitHub release. CI runs the same check, test and package steps on every push and pull request.

To list the extension in the Zelos marketplace, once: push the project to a GitHub repository,
submit it at [marketplace.zeloscloud.io](https://marketplace.zeloscloud.io/), and install the
Zelos GitHub App on the repository when asked. After approval, every release you publish reaches
the marketplace. See
[Publish to the Marketplace](https://docs.zeloscloud.io/sdk/how-to/package-extensions/#publish-to-the-marketplace).
