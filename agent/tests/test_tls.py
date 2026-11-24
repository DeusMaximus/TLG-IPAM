"""TLS verification is always on (spec §5.6), tested for real against a TLS server on
127.0.0.1: an untrusted certificate and a certificate for another name are refused
before any request is sent, and a private CA is trusted only once it is mounted and
named by `SSL_CERT_FILE`."""

import asyncio
import datetime
import ipaddress
import json
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from conftest import TOKEN
from tlg_ipam_agent.client import AgentClient, Kind, make_http


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """A private CA, and server certificates it signed: one for 127.0.0.1, one for
    another name."""
    out = tmp_path_factory.mktemp("pki")
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("test CA"))
        .issuer_name(_name("test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (out / "ca.pem").write_bytes(_pem(ca))

    def server(label: str, san: x509.GeneralName):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(_name(label))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([san]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        (out / f"{label}.pem").write_bytes(_pem(cert))
        (out / f"{label}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        return out / f"{label}.pem", out / f"{label}.key"

    return {
        "ca": out / "ca.pem",
        "loopback": server("loopback", x509.IPAddress(ipaddress.ip_address("127.0.0.1"))),
        "other": server("other", x509.DNSName("other.example.com")),
    }


async def _serve(cert_and_key):
    """A one-route HTTPS server on 127.0.0.1 that answers every request like an
    accepted check-in, and counts the requests it read."""
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*cert_and_key)
    seen = []

    async def handle(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(
                (int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")),
                0,
            )
            await reader.readexactly(length)
            seen.append(head.split(b"\r\n")[0])
            body = json.dumps({"data": {"checkin_interval_s": 60, "jobs": []}}).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (ssl.SSLError, asyncio.IncompleteReadError, ConnectionError):
            pass  # the client refused the handshake
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    return server, server.sockets[0].getsockname()[1], seen


async def _checkin(port: int):
    async with make_http(f"https://127.0.0.1:{port}", TOKEN) as http:
        return await AgentClient(http).checkin({})


async def test_an_untrusted_certificate_is_refused_before_any_request(pki, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server, port, seen = await _serve(pki["loopback"])
    async with server:
        answer = await _checkin(port)
    assert answer.kind is Kind.RETRY and answer.status is None
    assert "CERTIFICATE_VERIFY_FAILED" in answer.message
    assert seen == []


async def test_a_private_ca_is_trusted_once_named_by_ssl_cert_file(pki, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki["ca"]))
    server, port, seen = await _serve(pki["loopback"])
    async with server:
        answer = await _checkin(port)
    assert answer.kind is Kind.OK, answer.message
    assert seen == [b"POST /api/v1/agent/checkin HTTP/1.1"]


async def test_a_certificate_for_another_name_is_refused_even_from_a_trusted_ca(pki, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki["ca"]))
    server, port, seen = await _serve(pki["other"])
    async with server:
        answer = await _checkin(port)
    assert answer.kind is Kind.RETRY and answer.status is None
    assert "CERTIFICATE_VERIFY_FAILED" in answer.message
    assert seen == []
