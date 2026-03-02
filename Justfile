# Zelos MQTT Extension

# Install dependencies and pre-commit hooks
install:
    uv sync --all-extras
    uv run pre-commit install || true

# Run linter
check:
    uv run ruff check .

# Format code
format:
    uv run ruff format .
    uv run ruff check --fix .

# Run tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run extension in demo mode
dev:
    uv run python main.py --demo

# Run standalone demo broker
demo:
    uv run python main.py demo

# Package extension for distribution
package:
    tar -czf zelos-extension-mqtt-0.1.0.tar.gz \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='.ruff_cache' \
        --exclude='tests' \
        main.py extension.toml config.schema.json pyproject.toml uv.lock \
        zelos_extension_mqtt/
