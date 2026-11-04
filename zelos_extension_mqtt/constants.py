"""Trace naming: the one layout rule and the name check, shared by config, client and map."""

from __future__ import annotations

import zelos_sdk

DEFAULT_PREFIX = "MQTT"
#: Log source when the prefix is cleared.
LOG_SOURCE_NAME = "mqtt_log"
#: A broker may not take these: `log` is the log event on the prefix source.
RESERVED_NAMES = ("log", LOG_SOURCE_NAME)
#: Per-broker health event; sanitized names never start with `_`, so no topic takes it.
HEALTH_EVENT = "_broker"


def trace_layout(prefix: str, broker: str) -> tuple[str, str]:
    """The one trace-naming rule: (source name, event prefix) for a broker.

    With a prefix, one shared source carries every broker and a broker's events
    read `<broker>/<event>`. Cleared, each broker owns its source and the events
    are unprefixed.
    """
    if prefix:
        return prefix, f"{broker}/"
    return broker, ""


def name_error(
    value: str, label: str, reserved: tuple[str, ...] = (), kind: str = "source"
) -> str | None:
    """Why user-typed `value` is not a legal trace name, or None if it is.

    Names become trace path segments, so a separator (`/ . @ :`) would silently
    re-nest the tree. Anything the SDK sanitizer rewrites is rejected, not renamed.
    """
    if not value:
        return None
    if value in reserved:
        return f"Invalid {label} {value!r}: reserved for the extension's own logs."
    clean = zelos_sdk.sanitize_name(value, kind=kind)
    if clean == value:
        return None
    offender = next((c for c, ok in zip(value, clean, strict=False) if c != ok), value[-1])
    slash = ", '/'" if kind == "event" else ""
    return (
        f"Invalid {label} {value!r}: {offender!r} is not allowed. "
        f"Use letters, digits, space, '_'{slash} or '-'."
    )
