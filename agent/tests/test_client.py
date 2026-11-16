"""The HTTP client: TLS always verified, answers sorted into three kinds (spec §5.6, §5.7)."""

import ssl

import httpx
import pytest

from conftest import TOKEN, make_config
from tlg_ipam_agent.client import AgentClient, Kind, make_http, tls_context


def test_tls_is_always_verified():
    ctx = tls_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


async def test_the_http_client_verifies_and_does_not_follow_redirects():
    async with make_http(make_config()) as http:
        assert http.follow_redirects is False
        assert http.headers["authorization"] == f"Bearer {TOKEN}"
        assert http.headers["user-agent"].startswith("tlg-ipam-agent/")
        assert http.trust_env is False
        pool = http._transport._pool  # httpx's own connection pool
        assert pool._ssl_context.verify_mode is ssl.CERT_REQUIRED
        assert pool._ssl_context.check_hostname is True


def test_there_is_no_setting_that_turns_verification_off():
    import inspect

    from tlg_ipam_agent import client, config

    for module in (client, config):
        source = inspect.getsource(module)
        assert "CERT_NONE" not in source
        assert "verify=False" not in source
        assert "check_hostname = False" not in source


def _client(status, body=None, *, content=None):
    def handler(request):
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return AgentClient(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))


@pytest.mark.parametrize(
    "status,kind",
    [(200, Kind.OK), (429, Kind.RETRY), (500, Kind.RETRY), (502, Kind.RETRY), (503, Kind.RETRY),
     (401, Kind.REFUSED), (403, Kind.REFUSED), (404, Kind.REFUSED), (409, Kind.REFUSED),
     (413, Kind.REFUSED), (422, Kind.REFUSED), (301, Kind.REFUSED)],
)
async def test_answers_are_sorted(status, kind):
    body = {"data": {"checkin_interval_s": 60, "jobs": []}} if status == 200 else {"error": {"code": "X", "message": "m"}}
    answer = await _client(status, body).checkin({})
    assert answer.kind is kind
    assert answer.status == status


async def test_the_servers_message_is_made_printable_and_short():
    answer = await _client(409, {"error": {"code": "PROTOCOL_MISMATCH", "message": "bad\x1b[31m\n" + "x" * 900}}).checkin({})
    assert answer.code == "PROTOCOL_MISMATCH"
    assert "\x1b" not in answer.message and "\n" not in answer.message
    assert len(answer.message) < 400


@pytest.mark.parametrize("content", [b"<html>bad gateway</html>", b"", b"[1, 2]", b'{"error": "x"}'])
async def test_an_answer_that_is_not_the_envelope_is_still_sorted(content):
    answer = await _client(503, content=content).checkin({})
    assert answer.kind is Kind.RETRY and answer.code is None


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"data": null}', b'{"data": []}'])
async def test_a_200_without_a_data_object_is_retried(content):
    answer = await _client(200, content=content).checkin({})
    assert answer.kind is Kind.RETRY


async def test_no_answer_is_a_retry():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    client = AgentClient(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    answer = await client.results({})
    assert answer.kind is Kind.RETRY and answer.status is None
