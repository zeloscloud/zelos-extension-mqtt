"""Find MQTT brokers for the config form's Auto-configure button: check the form's
brokers, or with none, look on this machine.

Read-only: an MQTT CONNECT / CONNACK then DISCONNECT, each bounded by PROBE_TIMEOUT;
nothing is subscribed or published. Localhost 1883 and 8883 plus a passive mDNS
browse; no subnet sweep.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from typing import Any

import aiomqtt
import zelos_sdk

from zelos_extension_mqtt.client import describe_error, paho_logger, tls_context

logger = logging.getLogger(__name__)

#: (host, port, tls) probed with no brokers in the form: the IANA MQTT ports.
LOCAL = (("localhost", 1883, False), ("localhost", 8883, True))
MDNS_SERVICE = "_mqtt._tcp.local."
# A refused port answers at once; this bounds one that accepts and stalls.
PROBE_TIMEOUT = 2.0
MDNS_SECONDS = 2.0


async def check(host: str, port: int, tls: ssl.SSLContext | None) -> tuple[str, str] | None:
    """None when the broker accepts an MQTT connect, else (reason, fix)."""
    log, errors = paho_logger(f"{host}:{port}")
    client = aiomqtt.Client(
        host,
        port,
        tls_context=tls,
        timeout=PROBE_TIMEOUT,
        keepalive=int(PROBE_TIMEOUT),
        logger=log,
    )
    # paho connects (TCP, then the TLS handshake bounded by keepalive) in a blocking thread;
    # aiomqtt has no option for its 5 s connect timeout.
    client._client.connect_timeout = PROBE_TIMEOUT
    try:
        async with asyncio.timeout(3 * PROBE_TIMEOUT), client:
            pass
    except TimeoutError:
        return describe_error(aiomqtt.MqttError("Operation timed out"), errors.last)
    except aiomqtt.MqttError as e:
        return describe_error(e, errors.last)
    return None


def _label(host: str, port: int, tls: bool) -> str:
    return f"{host}:{port} ({'TLS' if tls else 'plain'})"


async def check_brokers(brokers: list[dict[str, Any]], advanced: dict[str, Any]) -> dict[str, Any]:
    """The form's brokers, each checked with its own settings and kept as entered."""
    from zelos_extension_mqtt.cli.app import ADVANCED_DEFAULTS, broker_settings

    advanced = {**ADVANCED_DEFAULTS, **advanced}
    jobs, found, problems = [], [], []
    for entry in brokers:
        label = str(entry.get("name") or entry.get("host") or "broker").strip()
        try:
            s = broker_settings(entry, advanced)
            tls = tls_context(s["ca_file"], s["cert_file"], s["key_file"]) if s["tls"] else None
        except ValueError as e:
            problems.append(f"{label}: {e}.")
            continue
        jobs.append((label, s, check(s["host"], s["port"], tls)))
    results = await asyncio.gather(*(job for *_, job in jobs))
    for (label, s, _), result in zip(jobs, results, strict=True):
        where = _label(s["host"], s["port"], s["tls"])
        if result is None:
            found.append(f"{label} at {where}")
        else:
            reason, fix = result
            problems.append(
                f"Couldn't connect to {label} at {where}: {reason}" + (f"; {fix}." if fix else ".")
            )
    if not found:
        return {"status": "error", "message": " ".join(problems)}
    message = " ".join([f"Connected to {'; '.join(found)}.", *problems])
    return {"status": "success", "message": message, "config": {"brokers": [*map(dict, brokers)]}}


async def _mdns_brokers() -> list[tuple[str, str, int]]:
    """(instance name, address, port) of brokers announced over mDNS within MDNS_SECONDS."""
    from zeroconf import InterfaceChoice, ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    names: set[str] = set()

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change is ServiceStateChange.Added:
            names.add(name)

    found = []
    try:
        azc = AsyncZeroconf()
    except OSError:  # macOS: the system responder owns 127.0.0.1:5353, so not every interface
        try:
            azc = AsyncZeroconf(interfaces=InterfaceChoice.Default)
        except Exception as e:  # no usable interface, or multicast refused
            logger.debug("mDNS unavailable: %s", e)
            return []
    try:
        browser = AsyncServiceBrowser(azc.zeroconf, MDNS_SERVICE, handlers=[on_change])
        await asyncio.sleep(MDNS_SECONDS)
        await browser.async_cancel()
        for name in sorted(names):
            info = AsyncServiceInfo(MDNS_SERVICE, name)
            if not await info.async_request(azc.zeroconf, 1000) or not info.port:
                continue
            for host in info.parsed_addresses()[:1]:
                found.append((name.removesuffix(f".{MDNS_SERVICE}"), host, info.port))
    finally:
        with contextlib.suppress(Exception):
            await azc.async_close()
    return found


async def probe(advanced: dict[str, Any]) -> dict[str, Any]:
    """Brokers on this machine's MQTT ports and announced over mDNS, each added if it
    accepts a connect. 8883 verifies with Advanced's TLS files, else the system trust store."""
    advanced = advanced or {}
    problems: list[str] = []
    try:
        secure = tls_context(
            advanced.get("ca_file", ""), advanced.get("cert_file", ""), advanced.get("key_file", "")
        )
    except ValueError as e:
        secure, problems = None, [f"Advanced: {e}."]
    local = [(host, host, port, tls) for host, port, tls in LOCAL if secure or not tls]
    local_results, mdns = await asyncio.gather(
        asyncio.gather(*(check(h, p, secure if t else None) for _, h, p, t in local)),
        _mdns_brokers(),
    )
    remote = [(label, h, p, False) for label, h, p in mdns]
    remote_results = await asyncio.gather(*(check(h, p, None) for _, h, p, _ in remote))

    brokers: list[dict[str, Any]] = []
    found, seen, taken = [], set(), set()
    for (label, host, port, tls), result in zip(
        [*local, *remote], [*local_results, *remote_results], strict=True
    ):
        if (host, port) in seen:
            continue
        if result is not None:
            reason, fix = result
            if reason != "connection refused":  # nothing listening: not worth a note
                problems.append(
                    f"{_label(host, port, tls)}: {reason}" + (f"; {fix}." if fix else ".")
                )
            continue
        seen.add((host, port))
        name = base = zelos_sdk.sanitize_name(label, kind="source")
        k = 2
        while name in taken:
            name, k = f"{base}_{k}", k + 1
        taken.add(name)
        brokers.append({"name": name, "host": host, "port": port, "tls": tls})
        found.append(f"{name} at {_label(host, port, tls)}")
    if not found:
        return {
            "status": "error",
            "message": " ".join(
                [
                    *problems,
                    "No MQTT broker answered on this machine (ports 1883 and 8883) or over "
                    "mDNS. Add a broker with its host.",
                ]
            ),
        }
    message = " ".join([f"Found {'; '.join(found)}.", *problems])
    return {"status": "success", "message": message, "config": {"brokers": brokers}}
