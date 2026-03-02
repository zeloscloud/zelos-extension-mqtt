"""MQTT topic map: defines addressable nodes grouped into trace events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

VALID_DATATYPES = {
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
}


@dataclass
class Node:
    """A single MQTT topic mapped to a trace signal."""

    topic: str
    name: str
    datatype: str = "float32"
    unit: str = ""
    qos: int = 0
    payload_type: str = "auto"  # "auto", "json", "raw"
    json_key: str = "value"
    writable: bool = False

    def __post_init__(self):
        if self.datatype not in VALID_DATATYPES:
            raise ValueError(f"Invalid datatype: {self.datatype!r} (valid: {VALID_DATATYPES})")
        if self.qos not in (0, 1, 2):
            raise ValueError(f"Invalid QoS: {self.qos} (must be 0, 1, or 2)")
        if self.payload_type not in ("auto", "json", "raw"):
            raise ValueError(
                f"Invalid payload_type: {self.payload_type!r} (valid: auto, json, raw)"
            )


@dataclass
class NodeMap:
    """Collection of nodes grouped by trace event name."""

    events: dict[str, list[Node]] = field(default_factory=dict)
    name: str = ""

    @classmethod
    def from_file(cls, path: str | Path) -> NodeMap:
        """Load a node map from a JSON file."""
        path = Path(path)
        with path.open() as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> NodeMap:
        """Build a node map from a dictionary."""
        events: dict[str, list[Node]] = {}
        for event_name, node_list in data.get("events", {}).items():
            nodes = []
            for item in node_list:
                nodes.append(
                    Node(
                        topic=item["topic"],
                        name=item["name"],
                        datatype=item.get("datatype", "float32"),
                        unit=item.get("unit", ""),
                        qos=item.get("qos", 0),
                        payload_type=item.get("payload_type", "auto"),
                        json_key=item.get("json_key", "value"),
                        writable=item.get("writable", False),
                    )
                )
            events[event_name] = nodes
        return cls(events=events, name=data.get("name", ""))

    @property
    def nodes(self) -> list[Node]:
        """All nodes across all events."""
        return [n for nodes in self.events.values() for n in nodes]

    @property
    def topics(self) -> list[str]:
        """All unique topics."""
        return list({n.topic for n in self.nodes})

    def node_by_topic(self, topic: str) -> Node | None:
        """Find a node by its MQTT topic."""
        for node in self.nodes:
            if node.topic == topic:
                return node
        return None

    def node_by_name(self, name: str) -> Node | None:
        """Find a node by its signal name."""
        for node in self.nodes:
            if node.name == name:
                return node
        return None

    def event_for_node(self, node: Node) -> str | None:
        """Find the event name that contains a given node."""
        for event_name, nodes in self.events.items():
            if node in nodes:
                return event_name
        return None

    def writable_nodes(self) -> list[Node]:
        """All nodes marked as writable."""
        return [n for n in self.nodes if n.writable]
