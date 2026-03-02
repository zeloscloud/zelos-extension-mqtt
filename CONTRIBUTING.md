# Contributing

## Development Workflow

```bash
just install  # Install dependencies and setup
just dev      # Run locally
just test     # Run tests
just check    # Lint code
```

1. Make your changes
2. Run `just format` to auto-format
3. Run `just test` to verify tests pass
4. Commit your changes (pre-commit hooks run automatically)

## Project Structure

```
zelos-extension-mqtt/
├── extension.toml                  # Extension metadata and version
├── config.schema.json              # Configuration UI schema
├── main.py                         # Entry point
├── pyproject.toml                  # Python dependencies
├── uv.lock                         # Locked dependency versions
├── Justfile                        # Development commands
├── LICENSE                         # MIT License
├── README.md                       # User documentation
├── CHANGELOG.md                    # Version history
├── CONTRIBUTING.md                 # This file
├── .pre-commit-config.yaml         # Git hook configuration
├── .gitignore                      # Git ignore rules
├── zelos_extension_mqtt/           # Extension package
│   ├── __init__.py
│   ├── client.py                   # MQTT client with SDK trace integration
│   ├── mqtt_map.py                 # Topic-to-signal mapping (NodeMap)
│   ├── cli/
│   │   ├── __init__.py
│   │   └── app.py                  # App mode runner
│   └── demo/
│       ├── __init__.py
│       ├── simulator.py            # Embedded broker + IoT simulator
│       └── demo_device.json        # Demo device map
├── tests/                          # Test suite
│   ├── __init__.py
│   └── test_mqtt.py
├── scripts/                        # Build and release scripts
│   ├── package_extension.py        # Creates marketplace tarball
│   └── bump_version.py             # Updates version numbers
├── assets/                         # Icons and media
│   └── logo.svg                    # Marketplace icon
├── .github/                        # GitHub automation
│   ├── workflows/
│   │   ├── CI.yml                  # Run tests on PR
│   │   └── release.yml             # Publish releases
│   └── dependabot.yml              # Dependency updates
└── .vscode/                        # VSCode settings
    ├── settings.json
    └── extensions.json
```

## Common Tasks

### Run Locally

```bash
just dev       # Run with default config
just dev-demo  # Run with embedded broker + simulated sensors
just demo      # Run standalone demo broker only
```

Press Ctrl+C to stop.

### Add a Dependency

```bash
uv add package-name        # Runtime dependency
uv add --dev package-name  # Dev dependency
```

### Package for Marketplace

```bash
just package
```

This creates a `.tar.gz` file ready to upload to the Zelos Marketplace (automatically happens in CI!)

### Create a Release

```bash
just release 1.0.0
git push --follow-tags
```

This updates version numbers, runs tests, and creates a git tag.

## Testing

### Write Tests

```python
# tests/test_feature.py
from zelos_extension_mqtt.client import MqttClient

def test_something():
    client = MqttClient(host="127.0.0.1", port=11883)
    assert client.host == "127.0.0.1"
```

### Run Tests

```bash
just test           # Run all tests
uv run pytest -v    # Verbose output
uv run pytest -k test_name  # Run specific test
```

## Code Quality

### Formatting & Linting

```bash
just format  # Auto-fix formatting
just check   # Check for issues
```

Pre-commit hooks run automatically on `git commit` and will:
- Format code with ruff
- Check for common issues
- Validate YAML/TOML/JSON files

### Type Hints

Use type hints on all function signatures:

```python
def my_function(name: str, count: int) -> list[str]:
    return [name] * count
```

## Getting Help

- [Zelos Docs](https://docs.zeloscloud.io)
- [SDK Guide](https://docs.zeloscloud.io/sdk)
- [GitHub Issues](https://github.com/zeloscloud/zelos-extension-mqtt/issues)

## License

MIT - see [LICENSE](LICENSE)
