"""Shared fixtures: free ports, the demo broker, condition waits, log and trace readers."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pytest
import zelos_sdk

from zelos_extension_mqtt import actions
from zelos_extension_mqtt.cli import app
from zelos_extension_mqtt.demo.simulator import DemoServer

ROOT = Path(__file__).parents[1]
DEMO_MAP = str(app.get_demo_device_map_path())


def free_port() -> int:
    """A port nothing listens on (bound and released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"Condition not met within {timeout}s")
        await asyncio.sleep(0.02)


def problems(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The extension's WARNING and ERROR lines."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("zelos_extension_mqtt")
    ]


def read_trace(path: Path) -> dict[str, dict[str, list]]:
    """{"<source>/<event>": {field: [values in time order], "time_ns": [...]}} as recorded."""
    out: dict[str, dict[str, list]] = {}
    with zelos_sdk.TraceReader(str(path)) as reader:
        segments = [s.id for s in reader.list_data_segments()]
        span = reader.time_range()
        for source in reader.list_fields():
            for event in source.events:
                key = f"{source.name}/{event.name}"
                fields = [f.path for f in event.fields]
                data = reader.query(
                    data_segment_ids=segments, fields=fields, start=span.start, end=span.end
                ).to_arrow()
                table = pa.ipc.open_stream(data).read_all() if data else None
                columns = {f.name: f"{key}.{f.name}" for f in event.fields} | {"time_ns": "time_ns"}
                out[key] = {
                    name: table.column(column).to_pylist() if table else []
                    for name, column in columns.items()
                }
    return out


@pytest.fixture(scope="module")
def demo_broker() -> DemoServer:
    """The built-in broker and simulator on a free port, publishing every 0.1 s."""
    server = DemoServer(port=free_port(), publish_interval=0.1)
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def no_runner():
    """Actions see no runner unless a test binds one."""
    yield
    actions.set_runner(None)


def make_pki(directory: Path) -> dict[str, str]:
    """A test CA, a broker certificate for localhost and a client certificate, as PEM files."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.UTC)

    def issue(name, key, issuer=None, issuer_key=None, ca=False, san=None, usage=None):
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer.subject if issuer else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if ca:
            builder = builder.add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False), True
            )
        if san:
            builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
        if usage:
            builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        return builder.sign(issuer_key or key, hashes.SHA256())

    def save(stem, cert, key):
        directory.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = directory / f"{stem}.pem", directory / f"{stem}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        return str(cert_path), str(key_path)

    ca_key, server_key, client_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca = issue("Test CA", ca_key, ca=True)
    server = issue(
        "localhost",
        server_key,
        ca,
        ca_key,
        san=[x509.DNSName("localhost")],
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
    )
    client = issue("zelos", client_key, ca, ca_key, usage=ExtendedKeyUsageOID.CLIENT_AUTH)
    ca_file, _ = save("ca", ca, ca_key)
    server_cert, server_key_file = save("server", server, server_key)
    client_cert, client_key_file = save("client", client, client_key)
    return {
        "ca": ca_file,
        "server_cert": server_cert,
        "server_key": server_key_file,
        "client_cert": client_cert,
        "client_key": client_key_file,
    }
