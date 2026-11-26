"""The link window and the consent page (v0.2 spec §6.3, #27): linking a client end to
end, through the owner's routes and the page, with no gate opened in the window's place.
"""

import asyncio
import html
import re
import sqlite3
import time

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import consent
from app.database import SessionLocal
from app.errors import ApiError
from app.models import Change, OAuthClient, OAuthGrant, OAuthToken
from app.services import link_window, oauth, scans

from .oauth_flow import (  # noqa: F401 (web and no_ambient_token are fixtures)
    BASE,
    database_dump,
    CALLBACK,
    STATE,
    all_rows,
    authorise,
    exchange,
    mcp_client,
    no_ambient_token,
    refresh,
    register,
    running,
    transaction,
    web,
)

OWNER = {"X-API-Key": "test-key"}
LOOKALIKE = "https://client-example.attacker.example/cb"


# --- Steps ---


async def open_window(web, **body) -> dict:
    res = await web.post("/api/v1/link-window", json=body, headers=OWNER)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def window_state(web) -> dict:
    res = await web.get("/api/v1/link-window", headers=OWNER)
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def consent_page(web, txn) -> httpx.Response:
    return await web.get("/mcp/consent", params={"txn": txn})


HIDDEN = re.compile(r'<input type="hidden" name="(\w+)" value="([^"]*)">')


def hidden_fields(page: str) -> dict:
    return {name: html.unescape(value) for name, value in HIDDEN.findall(page)}


async def post_code(web, txn, code, *, action="link", headers=None, **overrides) -> httpx.Response:
    """The owner types `code` into the consent page of `txn` and submits it."""
    page = await consent_page(web, txn)
    assert page.status_code == 200, page.text
    form = {**hidden_fields(page.text), "code": code, "action": action, **overrides}
    form = {name: value for name, value in form.items() if value is not None}
    return await web.post("/mcp/consent", data=form, headers=headers or {})


async def consent_with_code(web, client, code, **overrides) -> httpx.URL:
    """Authorise and give the code on the consent page: where the browser is sent."""
    txn = transaction(await authorise(web, client, **overrides))
    res = await post_code(web, txn, code)
    assert res.status_code == 303, res.text
    return httpx.URL(res.headers["location"])


def element(page: str, element_id: str) -> str:
    """The text of the element with `element_id`, as a browser shows it."""
    match = re.search(rf'id="{element_id}"[^>]*>(.*?)</', page, re.S)
    assert match, element_id
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))


def wrong_code_for(code: str) -> str:
    return "".join("2" if c != "2" else "3" for c in code)


async def changes() -> list[Change]:
    async with SessionLocal() as session:
        return list((await session.scalars(select(Change).order_by(Change.ts))).all())


# --- The owner's routes ---


async def test_opening_a_window_gives_the_code_once(web):
    opened = await open_window(web)
    code = opened.pop("code")
    assert len(code) == link_window.CODE_LENGTH == 10
    assert set(code) <= set(link_window.CODE_ALPHABET)
    assert not set("01ILOU") & set(link_window.CODE_ALPHABET)
    assert opened["configured"] is True and opened["open"] is True
    assert opened["can_write"] is False and opened["wrong_codes"] == 0
    assert 598 <= opened["expires_in_s"] <= 600
    assert opened["opened_at"].endswith("+00:00") and opened["expires_at"].endswith("+00:00")
    assert opened["ended"] is None
    state = await window_state(web)
    assert "code" not in state and code not in str(state)
    assert {k: v for k, v in state.items() if k != "expires_in_s"} == {
        k: v for k, v in opened.items() if k != "expires_in_s"
    }


async def test_codes_are_drawn_afresh(web):
    codes = {(await open_window(web))["code"] for _ in range(20)}
    assert len(codes) == 20


async def test_the_window_opened_is_audit_logged_without_the_code(web):
    opened = await open_window(web)
    await web.post("/api/v1/link-window", json={}, headers={**OWNER, "X-TLG-Client": "ui"})
    first, second = await changes()
    assert (first.entity_type, first.action, first.source, first.actor) == (
        "link_window", "open", "api", None,
    )
    assert second.source == "ui"
    assert first.summary == "link window opened for a read-only link"
    assert set(first.after) == {"can_write", "opened_at", "expires_at"}
    assert opened["code"] not in repr((first.summary, first.after, second.after))


async def test_the_countdown_is_the_servers_monotonic_clock(web, monkeypatch):
    await open_window(web)
    start = time.monotonic()
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 400)
    assert 199 <= (await window_state(web))["expires_in_s"] <= 200
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 601)
    state = await window_state(web)
    assert state["open"] is False and state["ended"] == "expired"
    assert state["expires_in_s"] is None and state["ended_at"].endswith("+00:00")


async def test_a_window_allowing_writes_is_refused_while_remote_writes_are_off(web):
    """`web` is the server with `MCP_REMOTE_WRITES` off (`test_remote_writes.py` has it on)."""
    res = await web.post("/api/v1/link-window", json={"can_write": True}, headers=OWNER)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "REMOTE_WRITES_OFF"
    assert (await window_state(web))["open"] is False
    assert await changes() == []


async def test_a_request_that_is_no_window_request_is_refused(web):
    for body in ({"can_write": "yes"}, {"code": "ABCDEFGHJK"}, []):
        res = await web.post("/api/v1/link-window", json=body, headers=OWNER)
        assert res.status_code == 422, res.text
    assert (await window_state(web))["open"] is False


async def test_cancelling_ends_the_window_and_its_requests(web):
    opened = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    res = await web.delete("/api/v1/link-window", headers=OWNER)
    assert res.status_code == 200
    assert res.json()["data"]["open"] is False and res.json()["data"]["ended"] == "cancelled"
    assert len(oauth.pending) == 0
    assert (await consent_page(web, txn)).status_code == 403
    assert (await web.post("/mcp/register", json={"redirect_uris": [CALLBACK]})).status_code == 403
    # nothing to cancel is no error
    assert (await web.delete("/api/v1/link-window", headers=OWNER)).status_code == 200
    assert [c.action for c in await changes()] == ["open"] and opened


async def test_the_state_names_the_window_by_its_own_id(web):
    """`window_id` is the window's `id`, the `entity_id` of its audit entry (#30): in the
    answer that opens it and in every state while it is open; null when none is."""
    assert (await window_state(web))["window_id"] is None
    opened = await open_window(web)
    [entry] = await changes()
    assert opened["window_id"] == entry.entity_id
    assert (await window_state(web))["window_id"] == opened["window_id"]
    res = await web.delete("/api/v1/link-window", headers=OWNER)
    assert res.json()["data"]["window_id"] is None


async def test_each_end_names_the_window_it_ended(web):
    """`ended_window_id` (#30, round 1 of #195's briefed review): the end the state
    reports is the latest of any window's, so it names which window it was. Window A
    links; B opens and is cancelled before A's page asks: the state's end is B's."""
    a = await open_window(web)
    client = await register(web)
    back = await consent_with_code(web, client, a["code"])
    assert back.params["code"]
    state = await window_state(web)
    assert (state["ended"], state["ended_window_id"]) == ("linked", a["window_id"])
    b = await open_window(web)
    await web.delete("/api/v1/link-window", params={"window_id": b["window_id"]}, headers=OWNER)
    state = await window_state(web)
    assert (state["ended"], state["ended_window_id"]) == ("cancelled", b["window_id"])
    # a window replaced: its end names it, and the open one is the other
    c = await open_window(web)
    d = await open_window(web)
    state = await window_state(web)
    assert state["open"] and state["window_id"] == d["window_id"]
    assert state["ended_window_id"] is None
    assert c["window_id"] != d["window_id"]
    # after a restart nothing is known of any end
    link_window.reset()
    state = await window_state(web)
    assert (state["ended"], state["ended_window_id"]) == (None, None)


async def test_a_cancel_naming_its_window_ends_that_window(web):
    opened = await open_window(web)
    res = await web.delete(
        "/api/v1/link-window", params={"window_id": opened["window_id"]}, headers=OWNER
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["cancelled"] is True and data["open"] is False and data["ended"] == "cancelled"


async def test_a_cancel_naming_a_replaced_window_leaves_the_replacement_and_its_requests(web):
    """The race Codex named on PR #195: a page reads window A, another tab opens window
    B in its place, and the page then cancels A. B stays open, with its pending request,
    and nothing is written."""
    first = await open_window(web)
    second = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    before = database_dump()
    res = await web.delete(
        "/api/v1/link-window", params={"window_id": first["window_id"]}, headers=OWNER
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["cancelled"] is False
    assert data["open"] is True and data["window_id"] == second["window_id"]
    assert len(oauth.pending) == 1
    assert (await consent_page(web, txn)).status_code == 200
    assert database_dump() == before
    # the replacement still links with its own code
    assert (await post_code(web, txn, second["code"])).status_code == 303


async def test_the_service_compares_the_window_under_the_transition_lock(web):
    """At the service: the cancel's comparison is made once it holds `_transitions`, so
    a replacement that lands while the cancel waits for the lock is what it compares
    with, and is left open."""
    first = await open_window(web)
    await link_window._transitions.acquire()
    try:
        cancelling = asyncio.create_task(
            link_window.cancel(configured=True, remote_writes=False, window_id=first["window_id"])
        )
        await asyncio.sleep(0)
        # the replacement lands while the cancel waits: opened as `open_window` does
        replacement = link_window._Window(
            id="replacement",
            code_hash="",
            can_write=False,
            opened_at=link_window._now(),
            deadline=time.monotonic() + link_window.LIFETIME_S,
            csrf_key=b"k" * 32,
        )
        link_window._end(link_window.REPLACED)
        link_window._current = replacement
    finally:
        link_window._transitions.release()
    state, cancelled = await cancelling
    assert cancelled is False
    assert state["open"] is True and state["window_id"] == "replacement"


async def test_a_cancel_naming_no_window_still_ends_whatever_is_open(web):
    """The owner's own scripts may still ask to cancel whatever is open; the page never
    does (`LinkClientModal.cancelOwn`)."""
    await open_window(web)
    res = await web.delete("/api/v1/link-window", headers=OWNER)
    assert res.json()["data"]["cancelled"] is True
    res = await web.delete("/api/v1/link-window", headers=OWNER)
    assert res.json()["data"]["cancelled"] is False
    res = await web.delete("/api/v1/link-window", params={"window_id": "x" * 65}, headers=OWNER)
    assert res.status_code == 422


async def test_opening_another_window_replaces_the_first(web):
    first = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    second = await open_window(web)
    assert first["code"] != second["code"]
    # the first window's request went with it
    assert (await consent_page(web, txn)).status_code == 410
    txn = transaction(await authorise(web, client))
    res = await post_code(web, txn, first["code"])
    assert res.status_code == 403 and consent.WRONG_CODE in res.text
    assert (await window_state(web))["wrong_codes"] == 1
    assert (await post_code(web, txn, second["code"])).status_code == 303


async def test_the_owners_routes_take_the_owner_key_alone(web):
    await open_window(web)
    for headers, status in (({}, 401), ({"X-API-Key": "wrong"}, 403)):
        for method in ("GET", "POST", "DELETE"):
            res = await web.request(method, "/api/v1/link-window", json={}, headers=headers)
            assert res.status_code == status
    client = await register(web)
    url = await consent_with_code(web, client, (await open_window(web))["code"])
    tokens = (await exchange(web, client, url.params["code"])).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await web.get("/api/v1/link-window", headers=bearer)).status_code == 401
    assert (await web.post("/api/v1/link-window", json={}, headers=bearer)).status_code == 401


async def test_without_oauth_no_window_opens(client):
    """The session's app is a server without `PUBLIC_BASE_URL`."""
    state = (await client.get("/api/v1/link-window")).json()["data"]
    assert state["configured"] is False and state["open"] is False
    res = await client.post("/api/v1/link-window", json={})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "OAUTH_NOT_CONFIGURED"
    assert (await client.delete("/api/v1/link-window")).json()["data"]["configured"] is False
    assert await changes() == []


# --- Acceptance: no window ---


async def test_with_no_window_open_registration_and_authorisation_are_refused(
    web, no_ambient_token
):
    window = await open_window(web)
    client = await register(web)
    url = await consent_with_code(web, client, window["code"])
    tokens = (await exchange(web, client, url.params["code"])).json()
    # the link closed the window
    assert (await window_state(web))["ended"] == "linked"
    res = await web.post("/mcp/register", json={"redirect_uris": [CALLBACK]})
    assert res.status_code == 403 and res.json()["error"] == "access_denied"
    res = await authorise(web, client)
    assert res.status_code == 403 and "location" not in res.headers
    assert len(oauth.pending) == 0
    # and the grant keeps working: a refresh, and a call on /mcp
    assert (await refresh(web, client, tokens["refresh_token"])).status_code == 200
    async with running(), mcp_client(tokens["access_token"]) as mcp:
        assert not (await mcp.call_tool("list_subnets", {})).is_error


async def test_the_consent_page_with_no_window_is_a_page_and_no_redirect(web):
    res = await consent_page(web, "anything")
    assert res.status_code == 403
    assert "location" not in res.headers
    assert res.headers["content-type"].startswith("text/html")
    assert "No link window is open" in res.text


# --- Wrong codes end a request, never the window ---


async def test_the_fifth_wrong_code_ends_the_request_and_not_the_window(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    wrong = wrong_code_for(window["code"])
    for count in (1, 2, 3, 4):
        res = await post_code(web, txn, wrong)
        assert res.status_code == 403 and consent.WRONG_CODE in res.text
        assert "location" not in res.headers
        assert (await window_state(web))["wrong_codes"] == count
    page = (await consent_page(web, txn)).text
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page), "code": wrong, "action": "link"}
    )
    assert res.status_code == 403 and "This request was ended" in res.text
    assert "location" not in res.headers
    # a sixth guess on that request, the right code included, is no guess at all
    assert oauth.pending.get(txn) is None
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page), "code": window["code"], "action": "link"}
    )
    assert res.status_code == 410
    assert await all_rows(OAuthGrant) == []
    # the window is open, and the client links in it with the right code
    state = await window_state(web)
    assert state["open"] is True and state["wrong_codes"] == 5
    url = await consent_with_code(web, client, window["code"])
    assert (await exchange(web, client, url.params["code"])).status_code == 200


async def test_the_change_log_has_one_entry_a_window_for_its_wrong_codes(web):
    window = await open_window(web)
    client = await register(web)
    for _ in range(3):
        txn = transaction(await authorise(web, client))
        for _ in range(link_window.WRONG_CODES_MAX):
            await post_code(web, txn, wrong_code_for(window["code"]))
    opened, wrong = await changes()
    assert (wrong.entity_type, wrong.entity_id, wrong.action) == (
        "link_window", opened.entity_id, "wrong_code",
    )
    assert (wrong.source, wrong.actor) == ("oauth", None)
    assert wrong.summary == "a wrong link code was typed on a consent page of this link window"
    assert set(wrong.after) == {"can_write", "opened_at", "expires_at"}
    assert window["code"] not in repr((wrong.summary, wrong.after))
    assert (await window_state(web))["wrong_codes"] == 15
    # the next window has an entry of its own
    second = await open_window(web)
    txn = transaction(await authorise(web, client))
    await post_code(web, txn, wrong_code_for(second["code"]))
    await post_code(web, txn, wrong_code_for(second["code"]))
    assert [c.action for c in await changes()] == ["open", "wrong_code", "open", "wrong_code"]


async def test_an_outsider_guessing_on_its_own_requests_never_closes_the_owners_window(web):
    """As round 1 of the briefed review broke it: one registration an outsider made in
    an earlier window, kept, tells it when a window is open (its authorise is a redirect
    and no longer a 403) and gets it consent forms of its own. It can type any number of
    wrong codes on them; the owner still links in every window with the right code."""
    await open_window(web)
    outsider = await register(web, client_name="Outsider")
    await web.delete("/api/v1/link-window", headers=OWNER)
    assert (await authorise(web, outsider)).status_code == 403
    for _ in range(3):
        window = await open_window(web)
        for _ in range(4):
            txn = transaction(await authorise(web, outsider))
            for _ in range(link_window.WRONG_CODES_MAX):
                await post_code(web, txn, wrong_code_for(window["code"]))
        state = await window_state(web)
        assert state["open"] is True and state["wrong_codes"] == 20
        owners = await register(web, client_name="Example Assistant")
        url = await consent_with_code(web, owners, window["code"])
        assert (await exchange(web, owners, url.params["code"])).status_code == 200
        assert (await window_state(web))["ended"] == "linked"
    grants = await all_rows(OAuthGrant)
    assert len(grants) == 3 and outsider["client_id"] not in {g.client_id for g in grants}


async def test_wrong_codes_on_other_requests_do_not_count_against_this_one(web):
    window = await open_window(web)
    client = await register(web)
    mine = transaction(await authorise(web, client))
    for _ in range(4):
        other = transaction(await authorise(web, client))
        for _ in range(4):
            await post_code(web, other, wrong_code_for(window["code"]))
    page = (await consent_page(web, mine)).text
    assert "5 more wrong codes end this request" in page
    assert (await post_code(web, mine, window["code"])).status_code == 303


async def test_what_cannot_be_a_code_is_not_counted(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    for typed in ("", "ABC", window["code"] + "2", "0" * 10, "I" * 10, "x" * 5000):
        res = await post_code(web, txn, typed)
        assert res.status_code == 400 and consent.NOT_A_CODE in res.text
    assert (await window_state(web))["wrong_codes"] == 0
    assert [c.action for c in await changes()] == ["open"]


@pytest.mark.parametrize(
    "typed",
    [
        "ſ23456789A",  # long s: upper-cases to S
        "ß2345678A",  # sharp s: upper-cases to SS
        "２３４５６７８９AB",  # fullwidth digits
        "23456\t789AB",
        "23456\n789AB",
        "23456\u00a0789AB",  # no-break space
        "23456_789AB",
    ],
)
def test_only_ascii_code_characters_spaces_and_hyphens_make_a_code(typed):
    assert link_window.typed_code(typed) is None


def test_a_code_may_be_typed_with_spaces_hyphens_and_in_either_case():
    assert link_window.typed_code(" 23456-789ab ") == "23456789AB"
    assert link_window.typed_code("2 3 4 5 6 7 8 9 a b") == "23456789AB"


async def test_a_code_is_taken_in_either_case_with_spaces_or_hyphens(web):
    window = await open_window(web)
    code = window["code"]
    client = await register(web)
    txn = transaction(await authorise(web, client))
    typed = f" {code[:5].lower()}-{code[5:]} "
    assert (await post_code(web, txn, typed)).status_code == 303


# --- Acceptance: one link per window ---


async def test_a_second_client_cannot_link_on_a_window_that_has_been_used(web):
    window = await open_window(web)
    first, second = await register(web), await register(web, redirect_uris=[LOOKALIKE])
    first_txn = transaction(await authorise(web, first))
    second_txn = transaction(await authorise(web, second, redirect_uri=LOOKALIKE))
    second_page = await consent_page(web, second_txn)
    assert (await post_code(web, first_txn, window["code"])).status_code == 303
    # the second's request went with the window, whatever form it holds
    res = await web.post(
        "/mcp/consent",
        data={**hidden_fields(second_page.text), "code": window["code"], "action": "link"},
    )
    assert res.status_code == 403 and "No link window is open" in res.text
    assert (await authorise(web, second, redirect_uri=LOOKALIKE)).status_code == 403
    assert (await web.post("/mcp/register", json={"redirect_uris": [CALLBACK]})).status_code == 403
    [grant] = await all_rows(OAuthGrant)
    assert grant.client_id == first["client_id"]


async def test_two_posts_of_the_right_code_at_once_link_once(web):
    window = await open_window(web)
    a, b = await register(web), await register(web)
    pages = []
    for client in (a, b):
        txn = transaction(await authorise(web, client))
        pages.append(hidden_fields((await consent_page(web, txn)).text))
    answers = await asyncio.gather(
        *(
            web.post("/mcp/consent", data={**form, "code": window["code"], "action": "link"})
            for form in pages
        )
    )
    assert sorted(res.status_code for res in answers) in ([303, 403], [303, 409])
    assert len(await all_rows(OAuthGrant)) == 1


# --- Acceptance: what the page shows ---


async def test_the_page_shows_the_redirect_host_the_name_unverified_and_the_access(web):
    await open_window(web)
    client = await register(web, client_name="Example Assistant")
    res = await consent_page(web, transaction(await authorise(web, client)))
    assert res.status_code == 200
    assert element(res.text, "redirect-host") == "client.example.com"
    assert element(res.text, "client-name") == "Example Assistant"
    assert "unverified" in res.text
    assert element(res.text, "access").startswith("Read-only.")
    assert consent.HTTPS_NOTE in res.text


async def test_a_lookalike_name_with_another_redirect_host_is_told_apart(web):
    await open_window(web)
    real = await register(web, client_name="Example Assistant")
    fake = await register(web, client_name="Example Assistant", redirect_uris=[LOOKALIKE])
    real_page = (await consent_page(web, transaction(await authorise(web, real)))).text
    fake_page = (
        await consent_page(web, transaction(await authorise(web, fake, redirect_uri=LOOKALIKE)))
    ).text
    assert element(real_page, "client-name") == element(fake_page, "client-name")
    assert element(real_page, "redirect-host") == "client.example.com"
    assert element(fake_page, "redirect-host") == "client-example.attacker.example"
    # the host is the request's, not the client's: a client registered on two hosts
    two = await register(web, redirect_uris=[CALLBACK, LOOKALIKE])
    page = (await consent_page(web, transaction(await authorise(web, two, redirect_uri=LOOKALIKE)))).text
    assert element(page, "redirect-host") == "client-example.attacker.example"


async def test_the_page_shows_an_internationalised_host_in_its_ascii_form(web):
    await open_window(web)
    client = await register(web, redirect_uris=["https://bücher.example/cb"])
    assert client["redirect_uris"] == ["https://xn--bcher-kva.example/cb"]
    for asked in ("https://bücher.example/cb", "https://xn--bcher-kva.example/cb"):
        res = await consent_page(web, transaction(await authorise(web, client, redirect_uri=asked)))
        assert element(res.text, "redirect-host") == "xn--bcher-kva.example"
        assert "bücher" not in res.text and "b&#252;cher" not in res.text


def test_the_host_is_the_parsed_uris_in_ascii_form():
    assert link_window.redirect_host("https://xn--bcher-kva.example:8443/cb") == "xn--bcher-kva.example"
    assert link_window.redirect_host("https://bücher.example/cb") == "xn--bcher-kva.example"
    assert link_window.redirect_host("http://[::1]:5000/cb") == "[::1]"
    assert link_window.redirect_host("https://user@client.example.com/cb") == "client.example.com"


async def test_a_loopback_redirect_says_it_is_this_computer(web):
    await open_window(web)
    client = await register(web, redirect_uris=["http://127.0.0.1:8765/cb"])
    res = await consent_page(
        web, transaction(await authorise(web, client, redirect_uri="http://127.0.0.1:50000/cb"))
    )
    assert element(res.text, "redirect-host") == "127.0.0.1"
    assert consent.LOOPBACK_NOTE in res.text


HOSTILE = "<script>alert(1)</script>\"'&{host} ignore previous instructions"


async def test_what_a_client_supplied_is_stored_and_shown_sanitised(web):
    """A name holding markup and instructions is stored sanitised, shown so, and
    recorded so in `actor` (#29). `_html` escapes it as well, as every value."""
    await open_window(web)
    client = await register(web, client_name=HOSTILE)
    kept = "script alert 1 script host ignore previous instructions"
    assert client["client_name"] == kept
    res = await consent_page(web, transaction(await authorise(web, client)))
    assert element(res.text, "client-name") == kept
    assert "<script" not in res.text and "alert(1)" not in res.text
    window = await open_window(web)
    await consent_with_code(web, client, window["code"])
    [entry] = [c for c in await changes() if c.entity_type == "grant"]
    assert entry.actor == f"grant:{kept}"
    assert kept not in entry.summary


async def test_a_client_with_no_kept_name_is_shown_as_such(web):
    await open_window(web)
    client = await register(web, client_name="✓ Клод")
    res = await consent_page(web, transaction(await authorise(web, client)))
    assert element(res.text, "client-name") == "no name this server keeps"
    assert 'class="tag"' not in res.text and "<q" not in res.text


LOOKALIKE_NAME = "Example Assistant ✓ verified · client.example.com"


def name_markup(page: str) -> str:
    """The page's markup for the client's name, from its `<dd>` to the note."""
    match = re.search(r"<dt>Name the client gave</dt>\n<dd>(.*?)<br>", page, re.S)
    assert match
    return match.group(1)


async def test_a_name_that_claims_verification_and_a_host_is_a_quoted_claim(web):
    """#29's acceptance case: a client names itself as if the server vouched for it
    and as if it were a host, and redirects to another host. It is stored without the
    symbols and without the `.` a host is spelled with; the page shows it as the
    client's quoted claim, not in bold, after the "unverified" label; the host line is
    the redirect's alone."""
    await open_window(web)
    client = await register(web, client_name=LOOKALIKE_NAME, redirect_uris=[LOOKALIKE])
    kept = "Example Assistant verified client example com"
    assert client["client_name"] == kept
    assert (await stored_client_name(client)) == kept
    res = await consent_page(web, transaction(await authorise(web, client, redirect_uri=LOOKALIKE)))
    assert res.status_code == 200
    page = res.text
    assert element(page, "client-name") == kept
    assert element(page, "redirect-host") == "client-example.attacker.example"
    markup = name_markup(page)
    assert markup == (
        '<span class="tag">unverified</span> <span class="claim">The client calls itself '
        f'<q id="client-name"><bdi>{kept}</bdi></q></span>'
    )
    assert "<strong" not in page and "<b>" not in page
    # one host line, and the name is not in it
    assert page.count('class="hostname"') == 1 and "client.example.com" not in page
    # the quotation's marks are the stylesheet's, and the name holds none to close it
    assert 'q { quotes: "\\201C" "\\201D"; font-weight: 400; }' in consent.STYLE
    assert not set(kept) & set("\"'\u201c\u201d.:")


async def stored_client_name(client: dict) -> str | None:
    async with SessionLocal() as session:
        return (await session.get(OAuthClient, client["client_id"])).client_name


@pytest.mark.parametrize("name", ["ChatGPT", "Claude", "Google", "mistral-mcp-client"])
async def test_the_four_web_clients_names_are_stored_and_shown_unchanged(web, name):
    await open_window(web)
    client = await register(web, client_name=name)
    assert client["client_name"] == name and await stored_client_name(client) == name
    res = await consent_page(web, transaction(await authorise(web, client)))
    assert element(res.text, "client-name") == name
    window = await open_window(web)
    await consent_with_code(web, client, window["code"])
    [entry] = [c for c in await changes() if c.entity_type == "grant"]
    assert entry.actor == f"grant:{name}"


async def test_a_registration_past_its_24_hours_is_sent_to_start_again(web):
    """A request pending for a registration that turns 24 hours old unlinked: its page,
    and a right code on it, say to start again from the client. The window stays open."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    page = await consent_page(web, txn)
    async with SessionLocal() as session:
        row = await session.get(OAuthClient, client["client_id"])
        row.created_at = row.created_at - oauth.UNLINKED_LIFETIME
        await session.commit()
    form = {**hidden_fields(page.text), "code": window["code"], "action": "link"}
    res = await web.post("/mcp/consent", data=form)
    assert res.status_code == 409 and "Start again from the client" in res.text
    assert "24 hours" in res.text
    assert await all_rows(OAuthGrant) == []
    assert (await window_state(web))["open"] is True


def test_every_value_put_into_a_page_is_escaped():
    page = consent._html("<p title=\"{a}\">{b}</p>", a='"><x>', b=consent.Markup("<i>ok</i>"))
    assert page == '<p title="&quot;&gt;&lt;x&gt;"><i>ok</i></p>'
    # braces in a value are not read as a template
    assert consent._html("{a}", a="{a}{0}") == "{a}{0}"


async def test_the_page_loads_nothing_and_cannot_be_framed(web):
    await open_window(web)
    client = await register(web)
    res = await consent_page(web, transaction(await authorise(web, client)))
    policy = res.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy and "default-src 'none'" in policy
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "same-origin"
    # no asset of any kind: nothing a browser would fetch
    assert not re.search(r"<(script|link|img|iframe|object|embed)\b|\bsrc=|\bhref=|url\(", res.text)
    # the one stylesheet is allowed by its hash
    [style] = re.findall(r"<style>(.*?)</style>", res.text, re.S)
    assert f"'sha256-{consent._STYLE_HASH}'" in policy and style == consent.STYLE


# --- Acceptance: when clients register ---


async def test_a_client_registered_when_its_url_is_added_links_in_the_one_window(web, monkeypatch):
    window = await open_window(web)
    client = await register(web)
    # half a minute passes before it authorises (ChatGPT and Gemini, spec §6.8)
    start = time.monotonic()
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 34)
    url = await consent_with_code(web, client, window["code"])
    assert url.params["state"] == STATE
    assert (await exchange(web, client, url.params["code"])).status_code == 200


async def test_a_client_that_registers_twice_links_with_its_second_registration(web):
    window = await open_window(web)
    unused, used = await register(web), await register(web)
    # neither registration closed the window or used it up
    assert (await window_state(web))["open"] is True
    assert (await window_state(web))["wrong_codes"] == 0
    url = await consent_with_code(web, used, window["code"])
    assert (await exchange(web, used, url.params["code"])).status_code == 200
    [grant] = await all_rows(OAuthGrant)
    assert grant.client_id == used["client_id"]
    assert len(await all_rows(OAuthClient)) == 2


async def test_a_client_registered_in_a_window_that_ran_out_links_in_a_later_one(web, monkeypatch):
    await open_window(web)
    client = await register(web)
    start = time.monotonic()
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 601)
    res = await authorise(web, client)
    assert res.status_code == 403 and "location" not in res.headers
    assert len(oauth.pending) == 0
    monkeypatch.undo()
    window = await open_window(web)
    url = await consent_with_code(web, client, window["code"])
    assert (await exchange(web, client, url.params["code"])).status_code == 200


async def test_a_request_from_an_earlier_window_cannot_be_approved_in_a_later_one(web, monkeypatch):
    await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    start = time.monotonic()
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 601)
    assert (await consent_page(web, txn)).status_code == 403
    monkeypatch.undo()
    await open_window(web)
    assert (await consent_page(web, txn)).status_code == 410


# --- Acceptance: the start-again page ---


async def test_a_request_whose_registration_was_reclaimed_shows_the_start_again_page(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    page = await consent_page(web, txn)
    # the registration goes while the request waits (a flood reclaims it, spec §6.6)
    async with SessionLocal() as session:
        await session.delete(await session.get(OAuthClient, client["client_id"]))
        await session.commit()
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page.text), "code": window["code"], "action": "link"}
    )
    assert res.status_code == 409 and "Start again from the client" in res.text
    assert "location" not in res.headers
    assert await all_rows(OAuthGrant) == [] and await all_rows(OAuthToken) == []
    # the window stays open, its count unchanged: the client registers again and links
    state = await window_state(web)
    assert state["open"] is True and state["wrong_codes"] == 0
    again = await register(web)
    url = await consent_with_code(web, again, window["code"])
    assert (await exchange(web, again, url.params["code"])).status_code == 200


async def registered_at(web, when) -> dict:
    client = await register(web)
    async with SessionLocal() as session:
        (await session.get(OAuthClient, client["client_id"])).created_at = when
        await session.commit()
    return client


async def held_ids() -> set[str]:
    return {c.client_id for c in await all_rows(OAuthClient)}


@pytest.mark.parametrize("oldest", [True, False])
async def test_at_the_cap_a_pending_request_does_not_change_what_is_reclaimed(web, oldest):
    """#190, part 2: the registry at the cap, one registration holding the owner's
    pending request. A new registration reclaims the oldest registration with no
    unrevoked grant, by `created_at` alone, whether or not it has a request pending.
    When that is the owner's client, the right code shows the start-again page, nothing
    is written, the window stays open, and the client registers again and links with
    the same code. When it is another, the owner's request links."""
    from datetime import datetime, timedelta, timezone

    window = await open_window(web)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    owners = await registered_at(web, start + timedelta(seconds=0 if oldest else 30))
    txn = transaction(await authorise(web, owners))
    page = await consent_page(web, txn)
    assert page.status_code == 200
    others = [
        await registered_at(web, start + timedelta(seconds=1 + i))
        for i in range(oauth.CLIENTS_MAX - 1)
    ]
    assert len(await held_ids()) == oauth.CLIENTS_MAX
    newcomer = await register(web)
    held = await held_ids()
    assert len(held) == oauth.CLIENTS_MAX and newcomer["client_id"] in held
    reclaimed = owners if oldest else others[0]
    assert reclaimed["client_id"] not in held
    assert oauth.pending.get(txn) is not None
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page.text), "code": window["code"], "action": "link"}
    )
    if not oldest:
        assert res.status_code == 303
        assert [g.client_id for g in await all_rows(OAuthGrant)] == [owners["client_id"]]
        return
    assert res.status_code == 409 and "Start again from the client" in res.text
    assert await all_rows(OAuthGrant) == [] and await all_rows(OAuthToken) == []
    # the change log holds the window's opening and nothing about a grant
    assert [c.entity_type for c in await changes()] == ["link_window"]
    state = await window_state(web)
    assert state["open"] is True and state["wrong_codes"] == 0
    again = await register(web)
    url = await consent_with_code(web, again, window["code"])
    assert (await exchange(web, again, url.params["code"])).status_code == 200


async def test_an_outsider_ends_a_pending_request_without_touching_the_registry(web):
    """Why #190 spares no registration for its pending request: the owner's request is
    ended as surely, and more cheaply, through the pending store alone. One registration
    of the outsider's and `PENDING_MAX` authorise requests push the owner's out of the
    32 held, with no registration reclaimed; the right code then finds nothing pending,
    and the window stays open for the owner to start again."""
    window = await open_window(web)
    owners = await register(web)
    txn = transaction(await authorise(web, owners))
    page = await consent_page(web, txn)
    outsider = await register(web)
    held = await held_ids()
    for _ in range(oauth.PENDING_MAX):
        transaction(await authorise(web, outsider))
    assert await held_ids() == held and oauth.pending.get(txn) is None
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page.text), "code": window["code"], "action": "link"}
    )
    assert res.status_code == 410 and "no longer pending" in res.text
    assert await all_rows(OAuthGrant) == []
    state = await window_state(web)
    assert state["open"] is True and state["wrong_codes"] == 0
    url = await consent_with_code(web, owners, window["code"])
    assert (await exchange(web, owners, url.params["code"])).status_code == 200


async def test_the_page_of_a_request_whose_registration_was_reclaimed_says_start_again(web):
    await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    async with SessionLocal() as session:
        await session.delete(await session.get(OAuthClient, client["client_id"]))
        await session.commit()
    res = await consent_page(web, txn)
    assert res.status_code == 409 and "Start again from the client" in res.text
    assert len(oauth.pending) == 0


async def test_a_grant_the_database_refuses_leaves_the_window_open(web, monkeypatch):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    page = await consent_page(web, txn)

    async def refused(session, txn_id, *, can_write):
        oauth.pending.take(txn_id)
        raise ApiError("UNAVAILABLE", "Database busy; retry the request", 503)

    monkeypatch.setattr(oauth, "approve", refused)
    res = await web.post(
        "/mcp/consent", data={**hidden_fields(page.text), "code": window["code"], "action": "link"}
    )
    assert res.status_code == 503 and "could not be recorded" in res.text
    state = await window_state(web)
    assert state["open"] is True and state["wrong_codes"] == 0


# --- The form ---


async def test_a_link_sends_the_browser_to_the_client_with_the_code(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    res = await post_code(web, txn, window["code"])
    assert res.status_code == 303
    back = httpx.URL(res.headers["location"])
    assert str(back.copy_with(query=None)) == CALLBACK
    assert set(back.params) == {"code", "state"} and back.params["state"] == STATE
    [entry] = [c for c in await changes() if c.entity_type == "grant"]
    assert entry.summary == "read-only grant issued to a linked client"
    [grant] = await all_rows(OAuthGrant)
    assert grant.can_write is False


async def test_a_form_for_another_request_or_without_its_token_is_refused_uncounted(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    other = transaction(await authorise(web, client))
    other_form = hidden_fields((await consent_page(web, other)).text)
    for csrf in (other_form["csrf"], None, "", "x" * 43):
        res = await post_code(web, txn, window["code"], csrf=csrf)
        assert res.status_code == 403 and "was not accepted" in res.text
    assert (await window_state(web))["wrong_codes"] == 0
    assert await all_rows(OAuthGrant) == []


async def test_a_post_from_another_origin_is_refused_uncounted(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    for origin in ("https://attacker.example", "null", "http://ipam.example.com"):
        res = await post_code(web, txn, window["code"], headers={"Origin": origin})
        assert res.status_code == 403 and "was not accepted" in res.text
    assert (await window_state(web))["wrong_codes"] == 0
    res = await post_code(web, txn, window["code"], headers={"Origin": BASE})
    assert res.status_code == 303


async def test_an_unknown_request_is_no_form(web):
    await open_window(web)
    for txn in ("no-such-request", ""):
        res = await consent_page(web, txn)
        assert res.status_code == 410 and "no longer pending" in res.text
    assert (await web.get("/mcp/consent")).status_code == 410
    res = await web.post("/mcp/consent", data={"txn": "x", "code": "ABCDEFGHJK"})
    assert res.status_code == 410
    assert (await window_state(web))["wrong_codes"] == 0


async def test_do_not_link_drops_the_request_and_keeps_the_window(web):
    await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    res = await post_code(web, txn, "", action="deny")
    assert res.status_code == 200 and "Not linked" in res.text
    assert oauth.pending.get(txn) is None
    assert (await window_state(web))["open"] is True
    assert await all_rows(OAuthGrant) == []


async def test_the_page_says_how_many_codes_and_minutes_are_left(web):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    page = (await consent_page(web, txn)).text
    assert "5 more wrong codes end this request; the window stays open." in page
    assert "closes in 10 minutes" in page
    for _ in range(4):
        await post_code(web, txn, wrong_code_for(window["code"]))
    assert "1 more wrong code ends this request" in (await consent_page(web, txn)).text


# --- Ending a window and an approval in flight ---


async def _post_right_code(web, txn, code):
    form = hidden_fields((await consent_page(web, txn)).text)
    return await web.post("/mcp/consent", data={**form, "code": code, "action": "link"})


@pytest.mark.parametrize("owner_action", ["cancel", "replace"])
async def test_an_approval_in_flight_finishes_before_the_owner_ends_its_window(
    web, monkeypatch, owner_action
):
    """As round 1 of the briefed review broke it: the approval is paused at the write
    lock, having taken its transaction, and the owner cancels or replaces the window.
    The owner's answer waits for the approval, and says what happened."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    paused, go = asyncio.Event(), asyncio.Event()
    take = scans.take_write_lock

    async def held(session):
        paused.set()
        await go.wait()
        await take(session)

    monkeypatch.setattr(scans, "take_write_lock", held)
    linking = asyncio.create_task(_post_right_code(web, txn, window["code"]))
    await paused.wait()
    if owner_action == "cancel":
        owner = asyncio.create_task(web.delete("/api/v1/link-window", headers=OWNER))
    else:
        owner = asyncio.create_task(web.post("/api/v1/link-window", json={}, headers=OWNER))
    await asyncio.sleep(0.05)
    assert not owner.done()  # the owner is not answered while the approval is out
    monkeypatch.setattr(scans, "take_write_lock", take)
    go.set()
    res, answer = await linking, await owner
    assert res.status_code == 303
    assert len(await all_rows(OAuthGrant)) == 1
    if owner_action == "cancel":
        # nothing left to cancel: the answer says the window linked
        assert answer.json()["data"]["ended"] == "linked"
    else:
        assert answer.status_code == 201 and answer.json()["data"]["open"] is True
        assert [c.action for c in await changes()] == ["open", "create", "open"]


@pytest.mark.parametrize("owner_action", ["cancel", "replace"])
async def test_an_approval_that_waits_behind_the_owner_ending_its_window_links_nothing(
    web, owner_action
):
    """The other order: the owner's cancel or replace takes the lock first, and the
    approval of the right code, waiting behind it, finds its window gone."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    form = hidden_fields((await consent_page(web, txn)).text)
    lock = link_window._transitions
    await lock.acquire()  # only to order the two requests
    if owner_action == "cancel":
        owner = asyncio.create_task(web.delete("/api/v1/link-window", headers=OWNER))
    else:
        owner = asyncio.create_task(web.post("/api/v1/link-window", json={}, headers=OWNER))
    await asyncio.sleep(0.05)
    linking = asyncio.create_task(
        web.post("/mcp/consent", data={**form, "code": window["code"], "action": "link"})
    )
    await asyncio.sleep(0.05)
    lock.release()
    answer, res = await owner, await linking
    if owner_action == "cancel":
        assert answer.json()["data"]["ended"] == "cancelled"
    else:
        assert answer.status_code == 201
    assert res.status_code == 403 and "No link window is open" in res.text
    assert await all_rows(OAuthGrant) == [] and await all_rows(OAuthToken) == []


async def test_a_window_does_not_expire_under_an_approval_in_flight(web, monkeypatch):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    paused, go = asyncio.Event(), asyncio.Event()
    take = scans.take_write_lock

    async def held(session):
        paused.set()
        await go.wait()
        await take(session)

    monkeypatch.setattr(scans, "take_write_lock", held)
    linking = asyncio.create_task(_post_right_code(web, txn, window["code"]))
    await paused.wait()
    start = time.monotonic()
    monkeypatch.setattr(link_window.time, "monotonic", lambda: start + 601)
    assert (await window_state(web))["open"] is True
    monkeypatch.setattr(scans, "take_write_lock", take)
    go.set()
    assert (await linking).status_code == 303
    assert (await window_state(web))["ended"] == "linked"


async def test_an_unexpected_failure_of_the_approval_ends_the_window(web, monkeypatch):
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    form = hidden_fields((await consent_page(web, txn)).text)
    approve = oauth.approve

    async def broken(session, txn_id, *, can_write):
        raise RuntimeError("injected")

    monkeypatch.setattr(oauth, "approve", broken)
    res = await web.post("/mcp/consent", data={**form, "code": window["code"], "action": "link"})
    assert res.status_code == 500 and "The link failed" in res.text
    state = await window_state(web)
    assert state["open"] is False and state["ended"] == "failed"
    # not left claimed: the retry is told there is no window, and a new one links
    res = await web.post("/mcp/consent", data={**form, "code": window["code"], "action": "link"})
    assert res.status_code == 403 and "No link window is open" in res.text
    monkeypatch.setattr(oauth, "approve", approve)
    url = await consent_with_code(web, client, (await open_window(web))["code"])
    assert (await exchange(web, client, url.params["code"])).status_code == 200


async def test_a_failure_after_the_grant_is_committed_still_links_once(web, monkeypatch):
    window = await open_window(web)
    first, second = await register(web), await register(web)
    txn = transaction(await authorise(web, first))
    other = transaction(await authorise(web, second))
    other_form = hidden_fields((await consent_page(web, other)).text)
    approve = oauth.approve

    async def fails_after_commit(session, txn_id, *, can_write):
        await approve(session, txn_id, can_write=can_write)
        raise RuntimeError("injected after the commit")

    monkeypatch.setattr(oauth, "approve", fails_after_commit)
    assert (await _post_right_code(web, txn, window["code"])).status_code == 500
    monkeypatch.setattr(oauth, "approve", approve)
    res = await web.post(
        "/mcp/consent", data={**other_form, "code": window["code"], "action": "link"}
    )
    assert res.status_code == 403
    assert len(await all_rows(OAuthGrant)) == 1


# --- The wrong-code entry and the window's ends (round 2 of the briefed review) ---


def pause_wrong_code_commits(monkeypatch, *, fail_first: int = 0):
    """Commits that carry the wrong-code entry: the first `fail_first` refused as
    SQLite refuses a busy write, the next paused until `go` is set."""
    paused, go = asyncio.Event(), asyncio.Event()
    commit = AsyncSession.commit
    refused = []

    async def held(self):
        if any(isinstance(row, Change) and row.action == "wrong_code" for row in self.new):
            if len(refused) < fail_first:
                refused.append(1)
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
            paused.set()
            await go.wait()
        return await commit(self)

    monkeypatch.setattr(AsyncSession, "commit", held)
    return paused, go


async def _owner_ends(web, action, other_txn=None, code=None):
    if action == "cancel":
        return await web.delete("/api/v1/link-window", headers=OWNER)
    if action == "replace":
        return await web.post("/api/v1/link-window", json={}, headers=OWNER)
    return await _post_right_code(web, other_txn, code)


ENDS = {"cancel": [], "replace": ["open"], "link": ["create"]}


@pytest.mark.parametrize("action", ENDS)
async def test_a_wrong_code_entry_in_progress_is_logged_before_the_window_ends(
    web, monkeypatch, action
):
    """As round 2 broke it: the first wrong code's entry is paused in its commit, and the
    owner cancels, replaces or links. The owner's step waits for the one write, so the
    entry is in the log before the end, as the wrong code was."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    other = transaction(await authorise(web, client))
    form = hidden_fields((await consent_page(web, txn)).text)
    await consent_page(web, other)
    paused, go = pause_wrong_code_commits(monkeypatch)
    wrong = asyncio.create_task(
        web.post("/mcp/consent", data={**form, "code": wrong_code_for(window["code"]), "action": "link"})
    )
    await paused.wait()
    owner = asyncio.create_task(_owner_ends(web, action, other, window["code"]))
    await asyncio.sleep(0.05)
    assert not owner.done()  # behind one database write, no more
    go.set()
    await wrong, await owner
    assert [c.action for c in await changes()] == ["open", "wrong_code", *ENDS[action]]


@pytest.mark.parametrize("action", ENDS)
async def test_a_wrong_code_whose_window_ended_first_is_not_logged_after_the_end(web, action):
    """The other order: the owner's step takes the lock first, and the first wrong code's
    entry, waiting behind it, finds the window gone and is not written."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    other = transaction(await authorise(web, client))
    form = hidden_fields((await consent_page(web, txn)).text)
    other_form = hidden_fields((await consent_page(web, other)).text)
    lock = link_window._transitions
    await lock.acquire()  # only to order the two requests
    if action == "link":
        owner = asyncio.create_task(
            web.post("/mcp/consent", data={**other_form, "code": window["code"], "action": "link"})
        )
    else:
        owner = asyncio.create_task(_owner_ends(web, action))
    await asyncio.sleep(0.05)
    wrong = asyncio.create_task(
        web.post("/mcp/consent", data={**form, "code": wrong_code_for(window["code"]), "action": "link"})
    )
    await asyncio.sleep(0.05)
    lock.release()
    await owner
    res = await wrong
    # a link in flight refuses the wrong code uncounted; an end leaves it no window
    assert res.status_code == (409 if action == "link" else 403)
    assert [c.action for c in await changes()] == ["open", *ENDS[action]]


async def test_a_request_gets_five_guesses_however_the_database_takes_the_entry(
    web, monkeypatch
):
    """As round 2 broke it: the first four entries refused (each leaves the entry to the
    next wrong code), the fifth paused, and a sixth posted meanwhile. The fifth ended the
    request before it waited, so the sixth compares nothing."""
    window = await open_window(web)
    client = await register(web)
    txn = transaction(await authorise(web, client))
    form = hidden_fields((await consent_page(web, txn)).text)
    wrong = {**form, "code": wrong_code_for(window["code"]), "action": "link"}
    paused, go = pause_wrong_code_commits(monkeypatch, fail_first=4)
    for _ in range(4):
        assert (await web.post("/mcp/consent", data=wrong)).status_code == 403
    fifth = asyncio.create_task(web.post("/mcp/consent", data=wrong))
    await paused.wait()
    sixth = await asyncio.wait_for(web.post("/mcp/consent", data=wrong), 2)
    go.set()
    fifth = await fifth
    assert fifth.status_code == 403 and "This request was ended" in fifth.text
    assert sixth.status_code == 410
    assert (await window_state(web))["wrong_codes"] == 5
    # nor does the right code, after
    assert (await web.post("/mcp/consent", data={**wrong, "code": window["code"]})).status_code == 410
    assert await all_rows(OAuthGrant) == []
    assert [c.action for c in await changes()] == ["open", "wrong_code"]


async def test_while_a_link_is_approved_other_posts_are_refused_and_not_counted(
    web, monkeypatch
):
    """What the claim does besides keeping the window from expiring: while the right code
    of one request is being approved, a post on another request is answered `409` at
    once, its code neither compared nor counted. (One grant a window rests on the lock
    and the recheck after it, `_link`.)"""
    window = await open_window(web)
    client = await register(web)
    first = transaction(await authorise(web, client))
    second = transaction(await authorise(web, client))
    second_form = hidden_fields((await consent_page(web, second)).text)
    paused, go = asyncio.Event(), asyncio.Event()
    take = scans.take_write_lock

    async def held(session):
        paused.set()
        await go.wait()
        await take(session)

    monkeypatch.setattr(scans, "take_write_lock", held)
    linking = asyncio.create_task(_post_right_code(web, first, window["code"]))
    await paused.wait()
    for code in (wrong_code_for(window["code"]), window["code"]):
        res = await asyncio.wait_for(
            web.post("/mcp/consent", data={**second_form, "code": code, "action": "link"}), 2
        )
        assert res.status_code == 409 and "A link is being made" in res.text
    assert (await window_state(web))["wrong_codes"] == 0
    monkeypatch.setattr(scans, "take_write_lock", take)
    go.set()
    assert (await linking).status_code == 303
    assert len(await all_rows(OAuthGrant)) == 1
