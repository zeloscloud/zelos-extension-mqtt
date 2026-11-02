"""auto_config through the standalone action: the form's brokers, or this machine's."""

from __future__ import annotations

from zelos_extension_mqtt import actions, autoconfig

from .conftest import free_port


def test_auto_config_checks_the_forms_brokers_and_keeps_them(demo_broker):
    gone = free_port()
    form = [
        {"name": "plant", "host": "127.0.0.1", "port": demo_broker.port},
        {"name": "gone", "host": "127.0.0.1", "port": gone, "topics": ["a/#"]},
        {"name": "half", "host": "127.0.0.1", "tls": True, "cert_file": "c.pem"},
    ]
    result = actions.auto_config({"brokers": form, "advanced": {"prefix": "Kept"}})
    assert result == {
        "status": "success",
        "message": f"Connected to plant at 127.0.0.1:{demo_broker.port} (plain). "
        "half: set Client Certificate and Client Key together, or neither. "
        f"Couldn't connect to gone at 127.0.0.1:{gone} (plain): connection refused; "
        "check Host and Port.",
        "config": {"brokers": form},
    }


def test_auto_config_without_brokers_probes_this_machine(demo_broker, monkeypatch):
    """Local ports and mDNS, deduped by host:port; refused ports are not worth a note."""
    port = demo_broker.port

    async def mdns() -> list[tuple[str, str, int]]:
        return [("Lab Broker", "127.0.0.1", port), ("same as local", "localhost", port)]

    monkeypatch.setattr(autoconfig, "LOCAL", (("localhost", port, False), ("localhost", 1, True)))
    monkeypatch.setattr(autoconfig, "_mdns_brokers", mdns)
    result = actions.auto_config({})
    assert result == {
        "status": "success",
        "message": f"Found localhost at localhost:{port} (plain); "
        f"Lab Broker at 127.0.0.1:{port} (plain).",
        "config": {
            "brokers": [
                {"name": "localhost", "host": "localhost", "port": port, "tls": False},
                {"name": "Lab Broker", "host": "127.0.0.1", "port": port, "tls": False},
            ]
        },
    }

    monkeypatch.setattr(autoconfig, "LOCAL", (("localhost", 1, False),))
    monkeypatch.setattr(autoconfig, "_mdns_brokers", mdns_none)
    assert actions.auto_config({}) == {
        "status": "error",
        "message": "No MQTT broker answered on this machine (ports 1883 and 8883) or over mDNS. "
        "Add a broker with its host.",
    }


async def mdns_none() -> list[tuple[str, str, int]]:
    return []
