"""Config, device map and action surface: no broker needed."""

from __future__ import annotations

import json
import logging
import tomllib

import pytest
from zelos_sdk.actions import ActionsRegistry
from zelos_sdk.extensions.actions import get_standalone_actions

from zelos_extension_mqtt import ACTION_PREFIX, actions
from zelos_extension_mqtt.cli import app
from zelos_extension_mqtt.client import node_value
from zelos_extension_mqtt.constants import DEFAULT_PREFIX
from zelos_extension_mqtt.mqtt_map import Node, NodeMap, check_overlaps, subscriptions

from .conftest import ROOT

SCHEMA = json.loads((ROOT / "config.schema.json").read_text())


def test_defaults_match_schema():
    props = SCHEMA["properties"]
    broker = props["brokers"]["items"]
    tls_on = broker["dependencies"]["tls"]["oneOf"][1]["properties"]
    for defaults, properties in (
        (app.ADVANCED_DEFAULTS, props["advanced"]["properties"]),
        (app.BROKER_DEFAULTS, {**tls_on, **broker["properties"]}),
    ):
        assert {k: v["default"] for k, v in properties.items() if "default" in v} == defaults


def test_names_and_the_autoconfig_hook_match_the_manifest():
    manifest = tomllib.loads((ROOT / "extension.toml").read_text())
    assert manifest["name"] == ACTION_PREFIX == DEFAULT_PREFIX
    assert SCHEMA["ui:options"]["autoconfig"] == f"{ACTION_PREFIX}/auto_config"
    assert "auto_config" in get_standalone_actions()


def test_action_names_and_flags():
    """Pins the surface and each action's (read_only, hidden): a change is a deliberate choice."""
    flags = {
        name: (getattr(actions, name)._action.read_only, getattr(actions, name)._action.hidden)
        for name in actions.register_actions(ActionsRegistry())
    }
    assert flags == {
        "auto_config": (True, True),
        "get_status": (True, True),
        "list_topics": (True, True),
        "publish": (False, False),
        "subscribe": (False, False),
    }
    # Only the topic is required; a caller that omits qos / retain gets the defaults.
    required = {
        name: {f.name for f in getattr(actions, name)._action.fields if f.required}
        for name in ("publish", "subscribe")
    }
    assert required == {"publish": {"topic"}, "subscribe": {"topic"}}


def write(tmp_path, name: str, data) -> str:
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(path)


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        (
            {"host": "localhost", "port": 1883, "username": "u", "password": "p", "demo": False},
            "Config format changed: brokers are now listed under brokers[] with shared settings "
            "under advanced, and user name / password login is removed (found top-level host, "
            "password, port, username); reconfigure the extension",
        ),
        ({"demo": True}, "No brokers configured: add one under Brokers"),
        ({"brokers": [{"host": "a", "port": 0}]}, "Invalid configuration: "),
        (
            {"brokers": [{"host": "a", "name": "log"}]},
            "Broker 'log': Invalid broker Name 'log': reserved for the extension's own logs.",
        ),
        (
            {"brokers": [{"host": "a", "name": "plant/1"}]},
            "Broker 'plant/1': Invalid broker Name 'plant/1': '/' is not allowed. "
            "Use letters, digits, space, '_' or '-'.",
        ),
        (
            {"brokers": [{"host": "a"}], "advanced": {"prefix": "MQTT.lab"}},
            "Invalid Prefix 'MQTT.lab': '.' is not allowed.",
        ),
        (
            {"brokers": [{"host": "a", "name": "x"}, {"host": "b", "name": "x"}]},
            "Duplicate broker name 'x'; set Name on one of them.",
        ),
        (
            {"brokers": [{"host": "a", "topics": ["a/#/b"]}]},
            "Broker 'a': Invalid topic filter 'a/#/b': '#' must be a whole level and the last one",
        ),
        (
            {"brokers": [{"host": "a", "topics": ["a/+/c", "a/b/#"]}]},
            "Broker 'a': Topic filters 'a/+/c' and 'a/b/#' both match 'a/b/c' and neither "
            "contains the other; narrow one",
        ),
        (
            {"brokers": [{"host": "a", "tls": True, "cert_file": "c.pem"}]},
            "Broker 'a': set Client Certificate and Client Key together, or neither",
        ),
        (
            {"brokers": [{"host": "a", "tls": True, "ca_file": "/nonexistent/ca.pem"}]},
            "Broker 'a': cannot load TLS files: [Errno 2] No such file or directory",
        ),
        ({"brokers": [{"host": "a", "device_map_file": "/nonexistent/map.json"}]}, "No such file"),
        ({"brokers": [{"host": "a", "device_map_file": "{"}]}, "Expecting property name"),
        (
            {"brokers": [{"host": "a", "device_map_file": {"events": {"e": [{"topic": "t"}]}}}]},
            "An entry in event 'e' has no 'name'",
        ),
        (
            {"brokers": [{"host": "a", "device_map_file": {"events": {"e.1": []}}}]},
            "Invalid event name 'e.1': '.' is not allowed.",
        ),
        (
            {
                "brokers": [
                    {
                        "host": "a",
                        "device_map_file": {
                            "events": {"e": [{"topic": "t", "name": "v", "datatype": "float"}]}
                        },
                    }
                ]
            },
            "Invalid datatype: 'float' (valid: bool, float32, float64,",
        ),
        (
            {
                "brokers": [
                    {
                        "host": "a",
                        "device_map_file": {
                            "events": {
                                "e": [
                                    {"topic": "x/+", "name": "v"},
                                    {"topic": "+/y", "name": "w"},
                                ]
                            }
                        },
                    }
                ]
            },
            "Device map topics '+/y' and 'x/+' both match 'x/y' and neither contains the other",
        ),
        (
            {
                "brokers": [
                    {
                        "host": "a",
                        "device_map_file": {
                            "events": {
                                "e": [{"topic": "x", "name": "v"}, {"topic": "y", "name": "v"}]
                            }
                        },
                    }
                ]
            },
            "Duplicate name 'v' in event 'e'",
        ),
    ],
)
def test_config_problems_stop_with_one_line(tmp_path, monkeypatch, caplog, config, reason):
    """Every startup problem is one ERROR naming the setting, and exit status 1."""
    for broker in config.get("brokers", []):
        if (data := broker.get("device_map_file")) and not str(data).startswith("/"):
            broker["device_map_file"] = write(tmp_path, "map.json", data)
    monkeypatch.setenv("ZELOS_CONFIG_PATH", write(tmp_path, "config.json", config))
    monkeypatch.chdir(ROOT)  # load_config reads config.schema.json from the working directory
    with pytest.raises(SystemExit) as exc:
        app.run_app_mode()
    assert exc.value.code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(errors) == 1 and reason in errors[0], errors


def test_default_names_take_the_port_only_when_they_collide():
    config = {
        "brokers": [
            {"host": "10.0.0.5", "port": 1883},
            {"host": "10.0.0.5", "port": 8883, "tls": True},
            {"host": "plc.local"},
            {"host": "10.0.0.5", "name": "edge"},
        ]
    }
    brokers = app.resolve_brokers(config, app.resolve_advanced(config))
    assert [b["name"] for b in brokers] == ["10_0_0_5_1883", "10_0_0_5_8883", "plc_local", "edge"]


def test_tls_files_inherit_from_advanced_the_key_pair_together():
    advanced = {**app.ADVANCED_DEFAULTS, "ca_file": "ca", "cert_file": "c", "key_file": "k"}
    inherited = app.broker_settings({"host": "a", "tls": True}, advanced)
    own_pair = app.broker_settings(
        {"host": "a", "tls": True, "cert_file": "c2", "key_file": "k2"}, advanced
    )
    assert (inherited["ca_file"], inherited["cert_file"], inherited["key_file"]) == ("ca", "c", "k")
    assert (own_pair["ca_file"], own_pair["cert_file"], own_pair["key_file"]) == ("ca", "c2", "k2")


def test_the_most_specific_map_entry_records_a_topic():
    node_map = NodeMap.from_dict(
        {
            "events": {
                "any": [{"topic": "plant/#", "name": "any"}],
                "line": [{"topic": "plant/+/speed", "name": "speed"}],
                "pump": [
                    {"topic": "plant/pump/speed", "name": "rpm", "json_key": "rpm"},
                    {"topic": "plant/pump/speed", "name": "hz", "json_key": ["drive", "hz"]},
                ],
            }
        }
    )
    assert [n.name for _, n in node_map.match("plant/pump/speed")] == ["rpm", "hz"]
    assert [n.name for _, n in node_map.match("plant/fan/speed")] == ["speed"]
    assert [n.name for _, n in node_map.match("plant/fan/temp")] == ["any"]
    assert node_map.match("other") == []
    # Spec 4.7.2: a first-level wildcard never matches a `$` topic.
    sys_map = NodeMap.from_dict({"events": {"all": [{"topic": "#", "name": "v"}]}})
    assert sys_map.match("$SYS/broker/uptime") == []


def test_covered_filters_subscribe_once_at_the_highest_qos():
    """A broker sends a copy per matching subscription; covered filters would record twice."""
    assert subscriptions({"#": 0, "a/b": 1, "x/+": 0, "x/y": 2}) == {"#": 2}
    assert subscriptions({"a/+": 0, "a/b": 1, "c": 0}) == {"a/+": 1, "c": 0}
    # `#` does not receive `$SYS/...` (spec 4.7.2), so it covers none of it; nor do they overlap.
    assert subscriptions({"#": 0, "$SYS/#": 0, "$SYS/uptime": 1}) == {"#": 0, "$SYS/#": 1}
    check_overlaps(["+/status", "$SYS/#"], "Topic filters")


@pytest.mark.parametrize(
    ("payload", "entry", "value"),
    [
        ("23.5", {}, 23.5),
        ('{"value": 7, "x": 1}', {"datatype": "uint8"}, 7),
        ('{"drive": {"rpm": "1450"}}', {"datatype": "uint16", "json_key": ["drive", "rpm"]}, 1450),
        ("3.9", {"datatype": "int32"}, 3),
        ("ON", {"datatype": "bool"}, True),
        ("0", {"datatype": "bool"}, False),
        ("running", {"datatype": "string"}, "running"),
        ('{"value": true}', {"datatype": "string"}, "true"),
        ('{"other": 1}', {}, ValueError("the JSON has no key 'value'")),
        ("maybe", {"datatype": "bool"}, ValueError("'maybe' is not a boolean")),
    ],
)
def test_payloads_decode_to_the_declared_type(payload, entry, value):
    node = Node(topic="t", name="f", **entry)
    if isinstance(value, ValueError):
        with pytest.raises(ValueError, match=str(value)):
            node_value(payload, node)
    else:
        assert node_value(payload, node) == value
