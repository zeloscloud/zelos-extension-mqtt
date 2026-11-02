"""Device map (topics as typed fields, grouped into events) and the MQTT topic filter rules."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiomqtt

from zelos_extension_mqtt.constants import name_error

DATATYPES = (
    "bool",
    "uint8",
    "int8",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "float32",
    "uint64",
    "int64",
    "float64",
    "string",
)
PAYLOAD_TYPES = ("auto", "json", "raw")
#: The SDK keeps this field name for the time column.
RESERVED_FIELDS = ("time_ns",)


def filter_error(topic: str) -> str | None:
    """Why `topic` is not a valid MQTT topic filter (spec 4.7.1), or None."""
    if not topic:
        return "it is empty"
    if "\x00" in topic:
        return "it contains a NUL character"
    levels = topic.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return "'#' must be a whole level and the last one"
        if "+" in level and level != "+":
            return "'+' must be a whole level"
    return None


def is_wildcard(topic: str) -> bool:
    return "+" in topic or "#" in topic


def covers(a: str, b: str) -> bool:
    """Whether filter `a` matches every topic filter `b` matches.

    Exact but at the root: `+/#` matches all `#` does, yet only `#` is found to
    cover; it then counts as the wider of the two.
    """
    al, bl = a.split("/"), b.split("/")
    for i, x in enumerate(al):
        if x == "#":
            return True
        if i == len(bl) or bl[i] == "#" or (x != "+" and x != bl[i]):
            return False
    return len(al) == len(bl)


def intersection(a: str, b: str) -> str | None:
    """The filter matching exactly the topics both `a` and `b` match; None if they share none."""
    al, bl = a.split("/"), b.split("/")
    out: list[str] = []
    for i in range(max(len(al), len(bl))):
        x = al[i] if i < len(al) else None
        y = bl[i] if i < len(bl) else None
        if x == "#":
            return "/".join(out + bl[i:])
        if y == "#":
            return "/".join(out + al[i:])
        if x is None or y is None or (x != y and "+" not in (x, y)):
            return None
        out.append(y if x == "+" else x)
    return "/".join(out)


def check_overlaps(filters: Iterable[str], what: str) -> None:
    """Refuse two filters that share topics with neither containing the other.

    Such a topic has no most specific map entry, and a broker delivers it once per
    matching subscription. Exact topics never conflict: they are disjoint or
    contained, so only wildcard pairs are compared.

    Raises:
        ValueError: naming the pair and a topic both match
    """
    wild = sorted({f for f in filters if is_wildcard(f)})
    for i, a in enumerate(wild):
        for b in wild[i + 1 :]:
            shared = intersection(a, b)
            if shared is not None and covers(a, b) == covers(b, a):
                raise ValueError(
                    f"{what} '{a}' and '{b}' both match '{shared}' and neither contains "
                    "the other; narrow one"
                )


def subscriptions(filters: dict[str, int]) -> dict[str, int]:
    """The filters to subscribe: none another one covers, each at the highest QoS it covers.

    A broker delivers one copy per matching subscription, so a covered filter would
    record its messages twice. Overlaps without containment are refused earlier.
    """
    wild = [f for f in filters if is_wildcard(f)]
    return {
        f: max(q for g, q in filters.items() if covers(f, g))
        for f in filters
        if not any(g != f and covers(g, f) for g in wild)
    }


@dataclass
class Node:
    """One topic (or filter) read into one typed field."""

    topic: str
    name: str
    datatype: str = "float32"
    unit: str = ""
    qos: int = 0
    payload_type: str = "auto"
    #: Key of a JSON object payload, or a list of keys into nested objects.
    json_key: str | list[str] = "value"

    def __post_init__(self) -> None:
        if not isinstance(self.topic, str) or not isinstance(self.name, str):
            raise ValueError(f"topic and name must be text: {self.topic!r}, {self.name!r}")
        if self.datatype not in DATATYPES:
            raise ValueError(
                f"Invalid datatype: {self.datatype!r} (valid: {', '.join(sorted(DATATYPES))})"
            )
        if self.qos not in (0, 1, 2):
            raise ValueError(f"Invalid QoS: {self.qos} (must be 0, 1, or 2)")
        if self.payload_type not in PAYLOAD_TYPES:
            raise ValueError(
                f"Invalid payload_type: {self.payload_type!r} (valid: {', '.join(PAYLOAD_TYPES)})"
            )
        path = [self.json_key] if isinstance(self.json_key, str) else self.json_key
        if not path or not all(isinstance(k, str) for k in path):
            raise ValueError(f"Invalid json_key: {self.json_key!r} (a key or a list of keys)")
        if reason := filter_error(self.topic):
            raise ValueError(f"Invalid topic {self.topic!r}: {reason}")
        if self.name in RESERVED_FIELDS:
            raise ValueError(f"Invalid name {self.name!r}: reserved for the trace's time column")
        if error := name_error(self.name, "name", kind="field"):
            raise ValueError(error)

    @property
    def json_path(self) -> list[str]:
        return [self.json_key] if isinstance(self.json_key, str) else list(self.json_key)

    def to_dict(self) -> dict[str, Any]:
        """The map entry, defaults left out."""
        out: dict[str, Any] = {"topic": self.topic, "name": self.name, "datatype": self.datatype}
        for key, default in (("unit", ""), ("qos", 0), ("payload_type", "auto")):
            if getattr(self, key) != default:
                out[key] = getattr(self, key)
        if self.json_key != "value":
            out["json_key"] = self.json_key
        return out


@dataclass
class NodeMap:
    """Nodes grouped by trace event; matches a received topic to its most specific filter."""

    events: dict[str, list[Node]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._exact: dict[str, list[tuple[str, Node]]] = {}
        self._wild: dict[str, list[tuple[str, Node]]] = {}
        for event, nodes in self.events.items():
            for node in nodes:
                index = self._wild if is_wildcard(node.topic) else self._exact
                index.setdefault(node.topic, []).append((event, node))

    @classmethod
    def from_file(cls, path: str | Path) -> NodeMap:
        """Load a device map from a JSON file."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: Any) -> NodeMap:
        """Build a device map, or raise ValueError naming the first problem.

        A top-level `name` (earlier versions named the trace source after it) is ignored.
        """
        if not isinstance(data, dict) or not isinstance(data.get("events", {}), dict):
            raise ValueError("expected an object with an 'events' object")
        events: dict[str, list[Node]] = {}
        for event, entries in data.get("events", {}).items():
            if error := name_error(event, "event name", kind="event"):
                raise ValueError(error)
            if not isinstance(entries, list):
                raise ValueError(f"Event {event!r} must be a list of entries")
            nodes = []
            for item in entries:
                for key in ("topic", "name"):
                    if not isinstance(item, dict) or key not in item:
                        raise ValueError(f"An entry in event {event!r} has no {key!r}")
                node = Node(
                    topic=item["topic"],
                    name=item["name"],
                    datatype=item.get("datatype", "float32"),
                    unit=item.get("unit", ""),
                    qos=item.get("qos", 0),
                    payload_type=item.get("payload_type", "auto"),
                    json_key=item.get("json_key", "value"),
                )
                if any(n.name == node.name for n in nodes):
                    raise ValueError(f"Duplicate name {node.name!r} in event {event!r}")
                nodes.append(node)
            events[event] = nodes
        node_map = cls(events=events)
        check_overlaps((n.topic for n in node_map.nodes), "Device map topics")
        return node_map

    @property
    def nodes(self) -> list[Node]:
        """All nodes across all events."""
        return [n for nodes in self.events.values() for n in nodes]

    def match(self, topic: str) -> list[tuple[str, Node]]:
        """(event, node) of the most specific filter matching a received `topic`.

        Several entries on that one filter all record. Filters that share topics
        with neither containing the other are refused at load, so the matches
        form a chain and the innermost is unique.
        """
        exact = self._exact.get(topic)
        if exact:
            return exact
        best = None
        received = aiomqtt.Topic(topic)
        for wildcard in self._wild:
            if received.matches(wildcard) and (best is None or covers(best, wildcard)):
                best = wildcard
        return self._wild[best] if best else []

    def to_dict(self) -> dict[str, Any]:
        return {"events": {e: [n.to_dict() for n in nodes] for e, nodes in self.events.items()}}
