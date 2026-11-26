"""The consent page, `/mcp/consent` (v0.2 spec §6.3, #27): the one HTML page the API
serves, and the one with a form.

The owner's browser is sent here by the authorise step with `?txn=<id>`. The page shows
the host the code is sent to, prominently; the client's declared name, marked
unverified; the access the link gives; and a field for the link code. What it decides,
`services/link_window` decides: this module reads the request and renders the answer.

**How the page is built.** With no template library: each page is a literal template
of this module, and `_html` fills it. Every value `_html` inserts is escaped with
`html.escape(..., quote=True)` unless it is a `Markup`, which only `_html` returns, so
text that reached the page from a client (its name, the redirect host) or from the
request (the transaction id) is always text, inside an element or an attribute value
alike. The templates are filled with `str.format`, which reads braces only in the
template; a value holding braces is inserted as it is. The stylesheet is a constant
that is not filled.

**What it is served with.** A Content-Security-Policy that loads nothing (`default-src
'none'`) but the page's one stylesheet, by its hash; `frame-ancestors 'none'` (and
`X-Frame-Options: DENY` for browsers without it), so no site can frame it; `base-uri
'none'`; no external asset of any kind; `Cache-Control: no-store`; and `Referrer-Policy:
same-origin`, so the client is sent no referrer when the browser goes back to it, while
the form's own post still carries its `Origin`. `form-action` is not set: Chrome
applies it to the redirect after the post as well, and that redirect goes to the client.

No answer here is a redirect to the client but the one after a link.
"""

import base64
import hashlib
import html

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .database import SessionLocal
from .services import link_window as window

TITLE = "Link a client · TLG-IPAM"

STYLE = """
:root { color-scheme: light dark; --fg: #111827; --bg: #f8fafc; --card: #ffffff;
  --muted: #4b5563; --line: #d1d5db; --accent: #1d4ed8; --warn: #92400e; --warnbg: #fef3c7; }
@media (prefers-color-scheme: dark) { :root { --fg: #e5e7eb; --bg: #0f172a; --card: #1e293b;
  --muted: #9ca3af; --line: #334155; --accent: #60a5fa; --warn: #fcd34d; --warnbg: #422006; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 34rem; margin: 0 auto; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 24px; }
h1 { font-size: 1.25rem; margin: 0 0 12px; }
p { margin: 0 0 12px; }
.host { border: 2px solid var(--accent); border-radius: 8px; padding: 12px 14px; margin: 16px 0; }
.label { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted);
  margin: 0 0 4px; }
.hostname { font: 700 1.6rem/1.25 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow-wrap: anywhere; margin: 0 0 6px; }
.note, .hint { font-size: 0.85rem; color: var(--muted); }
dl { margin: 0 0 16px; }
dt { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
dd { margin: 0 0 10px; overflow-wrap: anywhere; }
.tag { display: inline-block; font-size: 0.75rem; padding: 0 6px; border-radius: 4px;
  background: var(--warnbg); color: var(--warn); margin-left: 4px; }
.message { background: var(--warnbg); color: var(--warn); border-radius: 6px; padding: 8px 12px; }
label { display: block; font-weight: 600; margin-bottom: 4px; }
input[type=text] { width: 100%; font: 1.2rem ui-monospace, Menlo, Consolas, monospace;
  letter-spacing: 0.15em; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--fg); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
button { font: inherit; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--bg); color: var(--fg); cursor: pointer; }
button.primary { background: var(--accent); border-color: var(--accent); color: #ffffff; }
"""

_STYLE_HASH = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()

HEADERS = {
    "Content-Security-Policy": (
        f"default-src 'none'; style-src 'sha256-{_STYLE_HASH}'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class Markup(str):
    """HTML this module wrote: what `_html` returns, and inserts unescaped."""


def _html(template: str, **values) -> Markup:
    """`template`, a literal of this module, with each value inserted as escaped text,
    unless it is `Markup` (a fragment `_html` built)."""
    return Markup(
        template.format(
            **{
                name: value if isinstance(value, Markup) else html.escape(str(value), quote=True)
                for name, value in values.items()
            }
        )
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""

_CONSENT = """<h1>Link a client to this IPAM?</h1>
<p>A client asks for access to this IPAM. Link it only if you started this link from that client just now, and the host below is the one you expect.</p>
<section class="host">
<p class="label">The link is sent to</p>
<p class="hostname" id="redirect-host"><bdi>{host}</bdi></p>
<p class="note">{host_note}</p>
</section>
<dl>
<dt>Name the client gave</dt>
<dd>{name} <span class="tag">unverified</span><br><span class="note">Any client can give itself any name. The host above is where the link goes.</span></dd>
<dt>Access</dt>
<dd id="access">{access}</dd>
</dl>
{message}<form method="post" action="consent">
<input type="hidden" name="txn" value="{txn}">
<input type="hidden" name="csrf" value="{token}">
<label for="code">Link code</label>
<input type="text" id="code" name="code" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="32" required autofocus>
<p class="hint">The code the IPAM's web interface showed when you opened the link window. {attempts} The window closes in {minutes}.</p>
<div class="actions">
<button class="primary" type="submit" name="action" value="link">Link this client</button>
<button type="submit" name="action" value="deny" formnovalidate>Do not link</button>
</div>
</form>
"""

_NOTICE = """<h1>{heading}</h1>
<p>{text}</p>
"""

_NAMED = """<strong id="client-name"><bdi>{name}</bdi></strong>"""
_UNNAMED = """<em id="client-name">no name this server keeps</em>"""
_MESSAGE = """<p class="message" role="alert">{text}</p>
"""

HTTPS_NOTE = "Over HTTPS, to this host."
LOOPBACK_NOTE = (
    "Over plain http to a loopback address: a program on the computer this browser "
    "runs on, not a website."
)
READ_ONLY = "Read-only. It can read the IPAM's sections, subnets, hosts, findings and change log, and cannot change anything."
READ_WRITE = (
    "Read and write. As well as reading everything, it can create, change and delete "
    "hosts and reserved ranges, and ask for scans."
)
WRONG_CODE = "That is not the link code."
NOT_A_CODE = "A link code is 10 letters and digits, as the web interface shows it. Nothing was counted."

# What the browser is shown when there is no form: (status, heading, text)
NOTICES = {
    window.NO_WINDOW: (
        403,
        "No link window is open",
        "Linking a client is started from the IPAM's own web interface: open a link "
        "window there, then start again from the client.",
    ),
    window.REQUEST_ENDED: (
        403,
        "This request was ended",
        "Five wrong link codes were typed for it, and nothing was linked. The link "
        "window is still open and its code has not changed: start again from the "
        "client.",
    ),
    window.NOT_PENDING: (
        410,
        "This request is no longer pending",
        "It was approved, refused or ran out, or its link window closed. Start again "
        "from the client.",
    ),
    window.START_AGAIN: (
        409,
        "Start again from the client",
        "The client's registration was removed while this request waited (this server "
        "keeps a limited number), so nothing was linked. Remove the server from the "
        "client and add it again: the client registers again. The link window is still "
        "open and its link code has not changed.",
    ),
    window.FORGED: (
        403,
        "This form was not accepted",
        "It was not sent from the consent page this server gave for this request, so "
        "no code was checked. Start again from the client.",
    ),
    window.BUSY: (
        409,
        "A link is being made",
        "A client is being linked with this link window now. Check the IPAM's web "
        "interface.",
    ),
    window.DENIED: (
        200,
        "Not linked",
        "Nothing was linked. The link window is still open: cancel it in the IPAM's "
        "web interface if you are not linking another client now.",
    ),
    window.FAILED_NOW: (
        500,
        "The link failed",
        "Something went wrong while the link was recorded, and the link window was "
        "closed. Check the IPAM's web interface for a linked client; to link one, open "
        "a new link window there and start again from the client.",
    ),
    window.UNAVAILABLE: (
        503,
        "The link could not be recorded",
        "The database did not take the link, so nothing was linked. Start again from "
        "the client; the link window is still open and its link code has not changed.",
    ),
}


def _respond(body: Markup, status: int) -> HTMLResponse:
    page = _html(_PAGE, title=TITLE, style=Markup(STYLE), body=body)
    return HTMLResponse(page, status_code=status, headers=HEADERS)


def notice(kind: str) -> HTMLResponse:
    status, heading, text = NOTICES[kind]
    return _respond(_html(_NOTICE, heading=heading, text=text), status)


def _minutes(seconds: int) -> str:
    minutes = max(1, (seconds + 59) // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _attempts(left: int) -> str:
    codes = f"{left} more wrong code{'s' if left != 1 else ''}"
    return f"{codes} end{'s' if left == 1 else ''} this request; the window stays open."


def consent_page(consent: window.Consent, message: str | None = None, status: int = 200) -> HTMLResponse:
    name = (
        _html(_NAMED, name=consent.client_name)
        if consent.client_name
        else Markup(_UNNAMED)
    )
    body = _html(
        _CONSENT,
        host=consent.host,
        host_note=LOOPBACK_NOTE if consent.loopback else HTTPS_NOTE,
        name=name,
        access=READ_WRITE if consent.can_write else READ_ONLY,
        message=_html(_MESSAGE, text=message) if message else Markup(""),
        txn=consent.txn_id,
        token=consent.token,
        attempts=_attempts(consent.attempts_left),
        minutes=_minutes(consent.seconds_left),
    )
    return _respond(body, status)


def _answer(outcome: window.Outcome) -> Response:
    if outcome.kind == window.LINKED_NOW:
        return RedirectResponse(outcome.redirect, status_code=303, headers=HEADERS)
    if outcome.kind == "consent":
        return consent_page(outcome.consent)
    if outcome.kind == window.WRONG:
        return consent_page(outcome.consent, WRONG_CODE, 403)
    if outcome.kind == window.MALFORMED:
        return consent_page(outcome.consent, NOT_A_CODE, 400)
    return notice(outcome.kind)


def _field(form, name: str) -> str | None:
    value = form.get(name)
    return value if isinstance(value, str) else None


def endpoint(public_base_url: str):
    """The handler of `GET` and `POST /mcp/consent`, for a server published at
    `public_base_url`."""

    async def consent(request: Request) -> Response:
        if request.method != "POST":
            async with SessionLocal() as session:
                return _answer(await window.show(session, request.query_params.get("txn")))
        if not window.origin_allowed(request.headers.get("origin"), public_base_url):
            return notice(window.FORGED)
        form = await request.form()
        async with SessionLocal() as session:
            outcome = await window.submit(
                session,
                txn_id=_field(form, "txn"),
                token=_field(form, "csrf"),
                code=_field(form, "code"),
                deny=_field(form, "action") == "deny",
            )
        return _answer(outcome)

    return consent
