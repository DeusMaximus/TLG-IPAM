import ast
import hmac
import inspect
import json
import re

import anyio
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app import auth
from app.auth import OWNER_SCOPES, OwnerKeyVerifier
from app.main import app

from .test_mcp import EXPECTED_TOOLS

MCP_PATHS = ("/mcp", "/mcp/")
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
MCP_ACCEPT = "application/json, text/event-stream"
OWNER_BEARER = {"Authorization": "Bearer test-key"}


async def test_health_needs_no_auth(anon_client):
    res = await anon_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_missing_key_is_401(anon_client):
    res = await anon_client.get("/api/v1/sections")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_wrong_key_is_403(anon_client):
    res = await anon_client.get("/api/v1/sections", headers={"X-API-Key": "nope"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


async def test_non_ascii_key_is_403_not_500(anon_client):
    # hmac.compare_digest raises TypeError on a non-ASCII str; the check compares bytes
    res = await anon_client.get("/api/v1/sections", headers={"X-API-Key": "café".encode("latin-1")})
    assert res.status_code == 403


async def test_rest_does_not_accept_bearer(anon_client):
    res = await anon_client.get("/api/v1/sections", headers={"Authorization": "Bearer test-key"})
    assert res.status_code == 401


def running_app():
    """The app's lifespan, entered in the test body: MCP requests that pass auth
    need the session manager it starts. (Not a fixture: the lifespan's cancel
    scope must be exited by the task that entered it.)

    The refusal tests enter it too, so that a check which wrongly lets a request
    through fails on the status assertion, not on an uninitialised session manager.
    """
    return app.router.lifespan_context(app)


async def test_mcp_mount_requires_key(anon_client):
    async with running_app():
        res = await anon_client.post("/mcp/", json={})
    assert res.status_code == 401


async def test_mcp_reachable_without_trailing_slash(anon_client):
    # mcp-remote and Claude Desktop request the bare /mcp path; a 401 here proves the
    # request reached the MCP auth layer instead of falling through to the SPA
    async with running_app():
        res = await anon_client.post("/mcp", json={})
    assert res.status_code == 401


def _asgi_client_factory(**kwargs):
    kwargs.setdefault("timeout", httpx2.Timeout(10.0))
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_accepts_owner_key_as_bearer(path):
    transport = StreamableHttpTransport(
        f"http://test{path}", auth="test-key", httpx_client_factory=_asgi_client_factory
    )
    async with running_app(), Client(transport) as mcp_client:
        tools = await mcp_client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS
        result = await mcp_client.call_tool("list_sections", {})
        assert result.data == {"sections": []}


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_bearer_initialize_is_answered_in_place(anon_client, path):
    # Neither path redirects: the client above follows redirects, this one does not
    async with running_app():
        res = await anon_client.post(
            path,
            json=INITIALIZE,
            headers={**OWNER_BEARER, "Accept": MCP_ACCEPT},
        )
    assert res.status_code == 200, res.text
    assert "mcp-session-id" in res.headers


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_missing_bearer_is_401(anon_client, path):
    async with running_app():
        res = await anon_client.post(path, json=INITIALIZE, headers={"Accept": MCP_ACCEPT})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.content == b""


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_x_api_key_alone_is_401(anon_client, path):
    async with running_app():
        res = await anon_client.post(
            path, json=INITIALIZE, headers={"X-API-Key": "test-key", "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer nope",
        "Bearer test-key-and-more",
        "Bearer ",
        b"Bearer caf\xe9",
    ],
)
@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_wrong_bearer_is_401(anon_client, path, authorization):
    async with running_app():
        res = await anon_client.post(
            path, json=INITIALIZE, headers={"Authorization": authorization, "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
    assert res.json()["error"] == "invalid_token"


async def test_mcp_other_auth_scheme_is_401(anon_client):
    async with running_app():
        res = await anon_client.post(
            "/mcp", json=INITIALIZE, headers={"Authorization": "Basic dGVzdC1rZXk=", "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


async def test_owner_key_verifier_grants_both_scopes():
    verifier = OwnerKeyVerifier()
    token = await verifier.verify_token("test-key")
    assert token is not None
    assert set(token.scopes) == {"ipam:read", "ipam:write"} == set(OWNER_SCOPES)
    assert await verifier.verify_token("nope") is None
    assert await verifier.verify_token("") is None


# --- Empty keys ---------------------------------------------------------------------
#
# The server refuses to start with an empty API_KEY (test_startup.py). These tests
# set the key to "" after startup, so they show that an empty X-API-Key or bearer is
# refused by the request check itself, not only because startup refused the setting.

CONFIGURED_KEYS = pytest.mark.parametrize("configured", ["test-key", ""], ids=["key-set", "key-empty"])


@CONFIGURED_KEYS
async def test_empty_value_is_never_the_owner_key(monkeypatch, configured):
    monkeypatch.setattr(auth.settings, "api_key", configured)
    assert auth.is_owner_key("") is False
    assert await OwnerKeyVerifier().verify_token("") is None


@CONFIGURED_KEYS
async def test_empty_x_api_key_is_403(anon_client, monkeypatch, configured):
    monkeypatch.setattr(auth.settings, "api_key", configured)
    res = await anon_client.get("/api/v1/sections", headers={"X-API-Key": ""})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


@CONFIGURED_KEYS
@pytest.mark.parametrize("path", MCP_PATHS)
async def test_empty_bearer_is_401(anon_client, monkeypatch, path, configured):
    monkeypatch.setattr(auth.settings, "api_key", configured)
    async with running_app():
        res = await anon_client.post(
            path, json=INITIALIZE, headers={"Authorization": "Bearer ", "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith('Bearer error="invalid_token"')


# --- The owner-key comparison -------------------------------------------------------
#
# Every surface that takes the owner key must reach a decision through
# hmac.compare_digest, for accepted keys as well as refused ones, and the digest's
# answer must be the decision. A call spy shows the digest ran; it cannot show that
# nothing compared the key before it, which is what the source check is for.

SURFACES = ("rest", *MCP_PATHS)
ACCEPTED = {"rest": 200, "/mcp": 200, "/mcp/": 200}
REFUSED = {"rest": 403, "/mcp": 401, "/mcp/": 401}


class DigestStandIn:
    """Takes the place of `hmac` inside `app.auth`. Records each compare_digest
    call and returns the real answer, or `answer` when one is forced."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def compare_digest(self, a, b):
        self.calls.append((a, b))
        real = hmac.compare_digest(a, b)
        return real if self.answer is None else self.answer


async def _present_key(anon_client, surface, key):
    """Present `key` where `surface` takes the owner key; return the response."""
    if surface == "rest":
        return await anon_client.get("/api/v1/sections", headers={"X-API-Key": key})
    async with running_app():
        return await anon_client.post(
            surface,
            json=INITIALIZE,
            headers={"Authorization": f"Bearer {key}", "Accept": MCP_ACCEPT},
        )


@pytest.mark.parametrize("key", ["test-key", "test-kez", "nope"])
@pytest.mark.parametrize("surface", SURFACES)
async def test_owner_key_is_compared_in_constant_time(anon_client, monkeypatch, surface, key):
    digest = DigestStandIn()
    monkeypatch.setattr(auth, "hmac", digest)

    res = await _present_key(anon_client, surface, key)

    expected = ACCEPTED if key == "test-key" else REFUSED
    assert res.status_code == expected[surface]
    assert (key.encode(), b"test-key") in digest.calls, (
        f"{key!r} on {surface} was decided without hmac.compare_digest"
    )


@pytest.mark.parametrize(
    ("answer", "key", "outcome"),
    [(False, "test-key", REFUSED), (True, "nope", ACCEPTED)],
    ids=["digest-refuses-the-owner-key", "digest-accepts-a-wrong-key"],
)
@pytest.mark.parametrize("surface", SURFACES)
async def test_digest_answer_decides_acceptance(anon_client, monkeypatch, surface, answer, key, outcome):
    # Forcing the digest's answer flips the outcome: nothing else decides
    monkeypatch.setattr(auth, "hmac", DigestStandIn(answer=answer))

    res = await _present_key(anon_client, surface, key)

    assert res.status_code == outcome[surface], (
        f"hmac.compare_digest answered {answer} for {key!r} on {surface}, "
        f"but the response was {res.status_code}: something besides the digest decided"
    )


# --- Where the owner key may go -----------------------------------------------------
#
# The spy and the forced answers above show that the digest decides. They cannot show
# that nothing looked at the key before it: an `==` that only feeds a log line, or a
# `startswith` whose answer is thrown away, changes no outcome. This check reads the
# source of `app.auth` and follows two values through every function in it:
#
# - the configured key: every read of an `api_key` attribute;
# - the presented key: `is_owner_key`'s parameter, and each name a function of the
#   module passes to `is_owner_key` (`x_api_key` in `require_api_key`, `token` in
#   `OwnerKeyVerifier.verify_token`).
#
# Either value, and a local name assigned from one, may only be:
#
# - an argument of `hmac.compare_digest`, as it is or through `.encode()`;
# - tested for emptiness (`not value`, `if value:`) or for None (`is None`, `is not None`);
# - assigned to a local name, which is then followed in its place;
# - passed to another function of the module, which is then followed with it;
# - for the presented key only, handed to FastMCP as `AccessToken(token=...)`.
#
# Any other use is reported with the expression that makes it. This is a bounded
# check, not a proof of constant time: it reads one module, by name, and does not
# follow the key through attributes, containers, globals or other modules.

PRESENTED, CONFIGURED = "presented key", "configured key"
PRESENTED_KEY_SINKS = {("AccessToken", "token")}


class OwnerKeyUses:
    """Every use of the owner key in a module's source that the rules above do not
    allow, as `findings`, and the names followed as the presented key, as `presented`."""

    def __init__(self, source):
        tree = ast.parse(source)
        self.parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.functions = {}
        for node in tree.body:
            methods = node.body if isinstance(node, ast.ClassDef) else [node]
            prefix = f"{node.name}." if isinstance(node, ast.ClassDef) else ""
            for fn in methods:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions[prefix + fn.name] = fn

        self.seeds = {name: {} for name in self.functions}
        if "is_owner_key" in self.functions:
            self.seeds["is_owner_key"][self._params("is_owner_key")[0]] = PRESENTED
        for name, fn in self.functions.items():
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "is_owner_key":
                    for arg in call.args:
                        if isinstance(arg, ast.Name):
                            self.seeds[name].setdefault(arg.id, PRESENTED)

        # A function is checked again whenever a call gives it another parameter to follow
        per_function, checked, self.followed = {}, {}, {}
        self._pending = list(self.functions)
        while self._pending:
            name = self._pending.pop()
            if checked.get(name) == self.seeds[name]:
                continue
            checked[name] = dict(self.seeds[name])
            self.followed[name] = dict(self.seeds[name])
            per_function[name] = self._check(name, self.functions[name], self.followed[name])

        # Outside a function nothing is followed, so every read of the key there is
        # reported: a module-level copy would otherwise reach functions unseen
        inside = {id(n) for fn in self.functions.values() for n in ast.walk(fn)}
        per_function["<module>"] = {
            (node.lineno, "<module>", self._describe(self.parents[node]))
            for node in ast.walk(tree)
            if id(node) not in inside and self._kind(node, {})
        }

        found = sorted({f for fs in per_function.values() for f in fs})
        self.findings = [f"{name}, line {line}: {text}" for line, name, text in found]
        self.presented = {
            (name, var) for name, names in self.followed.items() for var, kind in names.items() if kind == PRESENTED
        }

    def _params(self, name):
        args = self.functions[name].args
        return [a.arg for a in args.posonlyargs + args.args]

    def _check(self, name, fn, tainted):
        # A local assigned from a followed value is followed too, until nothing changes
        changed = True
        while changed:
            changed = False
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    target = node.target
                else:
                    continue
                kind = self._kind(node.value, tainted)
                if kind and isinstance(target, ast.Name) and target.id not in tainted:
                    tainted[target.id] = kind
                    changed = True
        return self._uses(name, list(ast.walk(fn)), tainted)

    def _uses(self, name, nodes, tainted):
        found = set()
        for node in nodes:
            kind = self._kind(node, tainted)
            if kind:
                use = self._disallowed(node, kind, name)
                if use:
                    found.add((node.lineno, name, use))
        return found

    @staticmethod
    def _kind(node, tainted):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return tainted.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "api_key" and isinstance(node.ctx, ast.Load):
            return CONFIGURED
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and any(isinstance(a, ast.Constant) and a.value == "api_key" for a in node.args)
        ):
            return CONFIGURED
        return None

    def _disallowed(self, node, kind, name):
        """None if this use of the key is allowed; otherwise the expression that uses it."""
        parent = self.parents[node]
        if isinstance(parent, ast.Attribute) and parent.attr == "encode":
            call = self.parents.get(parent)
            if isinstance(call, ast.Call) and call.func is parent and not (call.args or call.keywords):
                if self._is_digest_argument(call):
                    return None
            return self._describe(parent)
        if self._is_digest_argument(node) or self._is_truth_test(node):
            return None
        if (
            isinstance(parent, ast.Compare)
            and len(parent.ops) == 1
            and isinstance(parent.ops[0], (ast.Is, ast.IsNot))
            and any(
                isinstance(side, ast.Constant) and side.value is None
                for side in (parent.left, *parent.comparators)
            )
        ):
            return None
        if isinstance(parent, ast.Assign) and parent.value is node and len(parent.targets) == 1:
            if isinstance(parent.targets[0], ast.Name):
                return None
        if isinstance(parent, ast.AnnAssign) and parent.value is node and isinstance(parent.target, ast.Name):
            return None
        keyword = parent if isinstance(parent, ast.keyword) else None
        call = self.parents.get(parent) if keyword else parent
        if isinstance(call, ast.Call) and call.func is not node:
            callee = ast.unparse(call.func)
            if kind == PRESENTED and keyword and (callee, keyword.arg) in PRESENTED_KEY_SINKS:
                return None
            if self._follow(call, node, keyword, kind, name):
                return None
        return self._describe(parent)

    def _is_digest_argument(self, node):
        call = self.parents.get(node)
        return (
            isinstance(call, ast.Call)
            and any(arg is node for arg in call.args)
            and ast.unparse(call.func) == "hmac.compare_digest"
        )

    def _is_truth_test(self, node):
        while isinstance(self.parents.get(node), ast.BoolOp):
            node = self.parents[node]
        parent = self.parents.get(node)
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            return True
        return isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)) and parent.test is node

    def _follow(self, call, node, keyword, kind, name):
        """Follow the key into a function of the module it is passed to."""
        func, bound = call.func, False
        if isinstance(func, ast.Name) and func.id in self.functions:
            callee = func.id
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = name.split(".")[0] if func.value.id == "self" and "." in name else func.value.id
            callee, bound = f"{owner}.{func.attr}", func.value.id == "self"
            if callee not in self.functions:
                return False
        else:
            return False
        params = self._params(callee)[1:] if bound else self._params(callee)
        params_by_keyword = params + [a.arg for a in self.functions[callee].args.kwonlyargs]
        if keyword:
            if keyword.arg not in params_by_keyword:
                return False
            param = keyword.arg
        else:
            index = next(i for i, arg in enumerate(call.args) if arg is node)
            if index >= len(params) or any(isinstance(a, ast.Starred) for a in call.args[: index + 1]):
                return False
            param = params[index]
        if param not in self.seeds[callee]:
            self.seeds[callee][param] = kind
            self._pending.append(callee)
        return True

    def _describe(self, node):
        """The use, as source: the largest expression it sits in, or the first line of
        the statement or match case that holds it."""
        while not isinstance(node, (ast.stmt, ast.match_case)):
            parent = self.parents[node]
            if isinstance(parent, ast.stmt) and not isinstance(node, ast.pattern):
                break
            node = parent
        if isinstance(node, ast.match_case):
            guard = f" if {ast.unparse(node.guard)}" if node.guard else ""
            return f"case {ast.unparse(node.pattern)}{guard}:"
        if isinstance(node, ast.expr) and not isinstance(
            self.parents[node], (ast.If, ast.While, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Assert)
        ):
            node = self.parents[node]
        return ast.unparse(node).splitlines()[0]


def test_owner_key_reaches_only_the_digest():
    findings = OwnerKeyUses(inspect.getsource(auth)).findings
    assert findings == [], "app.auth uses the owner key outside hmac.compare_digest:\n" + "\n".join(findings)


def test_owner_key_guard_finds_every_surface():
    # The guard finds the surfaces itself, from the calls to is_owner_key. These are
    # the ones on main: a change that stopped it finding one fails here.
    assert {
        ("is_owner_key", "presented"),
        ("require_api_key", "x_api_key"),
        ("OwnerKeyVerifier.verify_token", "token"),
    } <= OwnerKeyUses(inspect.getsource(auth)).presented


def _mutated_auth_source(edits):
    source = inspect.getsource(auth)
    for anchor, replacement in edits:
        assert source.count(anchor) == 1, f"mutation anchor is not in app/auth.py exactly once: {anchor!r}"
        source = source.replace(anchor, replacement)
    return source


def _before(anchor, lines):
    return (anchor, "".join(lines) + anchor)


VERIFY_CHECK = "        if not is_owner_key(token):\n"
REST_CHECK = "    if not is_owner_key(x_api_key):\n"
DIGEST = "    return hmac.compare_digest(presented.encode(), expected.encode())\n"
LOG = 'logging.getLogger(__name__).debug("Owner credential matched")\n'

# Changes to app/auth.py that leave every behavioural test above green: each one
# compares the owner key, and the digest still decides. They are the three kinds the
# briefed review of PR #48 found (finding 1): an `==` feeding a log line in a caller,
# the same in a helper that is_owner_key calls, and other comparisons than `==`
# inside is_owner_key. The last two (a hash compared in a helper, a module-level
# copy of the key) are ways around the guard itself.
SURVIVORS = {
    "verify-token-logs-a-match": (
        [_before(VERIFY_CHECK, ["        if token == settings.api_key:\n", "            " + LOG])],
        "OwnerKeyVerifier.verify_token",
        "token == settings.api_key",
    ),
    "require-api-key-logs-a-match": (
        [_before(REST_CHECK, ["    if x_api_key == settings.api_key:\n", "        " + LOG])],
        "require_api_key",
        "x_api_key == settings.api_key",
    ),
    "helper-logs-a-match": (
        [
            (DIGEST, "    return _compare(presented)\n"),
            _before(
                "def is_owner_key(",
                [
                    "def _compare(presented):\n",
                    "    if presented == settings.api_key:\n",
                    "        " + LOG,
                    "    return hmac.compare_digest(presented.encode(), settings.api_key.encode())\n\n\n",
                ],
            ),
        ],
        "_compare",
        "presented == settings.api_key",
    ),
    "startswith": (
        [_before(DIGEST, ["    prefix = presented.startswith(settings.api_key)\n"])],
        "is_owner_key",
        "presented.startswith(settings.api_key)",
    ),
    "set-membership": (
        [_before(DIGEST, ["    member = presented in {settings.api_key}\n"])],
        "is_owner_key",
        "presented in {settings.api_key}",
    ),
    "set-issuperset": (
        [_before(DIGEST, ["    member = {settings.api_key}.issuperset({presented})\n"])],
        "is_owner_key",
        "{settings.api_key}.issuperset({presented})",
    ),
    "dict-lookup": (
        [_before(DIGEST, ['    known = {settings.api_key: "owner"}.get(presented)\n'])],
        "is_owner_key",
        "{settings.api_key: 'owner'}.get(presented)",
    ),
    "operator-eq": (
        [_before(DIGEST, ["    same = operator.eq(presented, expected)\n"])],
        "is_owner_key",
        "operator.eq(presented, expected)",
    ),
    "match": (
        [_before(DIGEST, ["    match presented:\n", "        case settings.api_key:\n", "            " + LOG])],
        "is_owner_key",
        "case settings.api_key:",
    ),
    "hash-equality": (
        [_before(DIGEST, ["    same = sha256(presented.encode()).digest() == sha256(expected.encode()).digest()\n"])],
        "is_owner_key",
        "sha256(presented.encode()).digest() == sha256(expected.encode()).digest()",
    ),
    "hash-equality-in-a-helper": (
        [
            _before(DIGEST, ["    _same_hash(presented, expected)\n"]),
            _before(
                "def is_owner_key(",
                [
                    "def _same_hash(a, b):\n",
                    "    return sha256(a.encode()).digest() == sha256(b.encode()).digest()\n\n\n",
                ],
            ),
        ],
        "_same_hash",
        "return sha256(a.encode()).digest() == sha256(b.encode()).digest()",
    ),
    "module-level-copy": (
        [
            _before("def is_owner_key(", ["OWNER_KEY = settings.api_key\n\n\n"]),
            _before(DIGEST, ["    same = presented == OWNER_KEY\n"]),
        ],
        "<module>",
        "OWNER_KEY = settings.api_key",
    ),
}


@pytest.mark.parametrize(("edits", "function", "comparison"), SURVIVORS.values(), ids=SURVIVORS)
def test_owner_key_guard_names_each_comparison(edits, function, comparison):
    findings = OwnerKeyUses(_mutated_auth_source(edits)).findings
    assert any(f.startswith(f"{function}, line ") and f.endswith(f": {comparison}") for f in findings), (
        f"the guard did not report `{comparison}` in {function}; it reported {findings}"
    )


# Changes that compare nothing secret. The guard must leave them alone, or it would be
# a ban on touching the key rather than a check on comparing it.
ALLOWED = {
    "presented-is-none": [
        _before("    expected = settings.api_key\n", ["    if presented is None:\n", "        return False\n"]),
    ],
    "configured-is-not-none": [
        _before("    if not presented or not expected:\n", ["    assert expected is not None\n"]),
    ],
    "configured-read-into-the-digest": [
        (DIGEST, "    return hmac.compare_digest(presented.encode(), settings.api_key.encode())\n"),
    ],
    "helper-that-only-digests": [
        (DIGEST, "    return _digest(presented, expected)\n"),
        _before(
            "def is_owner_key(",
            ["def _digest(a, b):\n", "    return hmac.compare_digest(a.encode(), b.encode())\n\n\n"],
        ),
    ],
}


@pytest.mark.parametrize("edits", ALLOWED.values(), ids=ALLOWED)
def test_owner_key_guard_allows_what_compares_nothing(edits):
    # Measured against app/auth.py as it stands, so that these fail only for their own
    # reason: a finding in the module itself is test_owner_key_reaches_only_the_digest's
    def uses(source):
        # Without line numbers, which the edits shift
        return {re.sub(r", line \d+:", ":", f) for f in OwnerKeyUses(source).findings}

    assert uses(_mutated_auth_source(edits)) - uses(inspect.getsource(auth)) == set()


# --- Session reuse ------------------------------------------------------------------
#
# A session id from an authenticated initialize is not a credential: every request
# on the session needs the bearer again.

REPLAY_CREDENTIALS = {
    "no-credential": {},
    "wrong-bearer": {"Authorization": "Bearer nope"},
    "x-api-key-only": {"X-API-Key": "test-key"},
}
REPLAY_REQUESTS = {
    "GET": ({"Accept": "text/event-stream"}, b""),
    "POST": (
        {"Accept": MCP_ACCEPT, "Content-Type": "application/json"},
        json.dumps(TOOLS_LIST).encode(),
    ),
    "DELETE": ({}, b""),
}


async def _open_session(anon_client, path):
    res = await anon_client.post(path, json=INITIALIZE, headers={**OWNER_BEARER, "Accept": MCP_ACCEPT})
    assert res.status_code == 200, res.text
    session = {"mcp-session-id": res.headers["mcp-session-id"]}
    res = await anon_client.post(
        path, json=INITIALIZED, headers={**OWNER_BEARER, **session, "Accept": MCP_ACCEPT}
    )
    assert res.status_code == 202, res.text
    return session


async def _response_start(method, path, headers, body=b""):
    """Send one request straight to the app and return the status and headers it
    starts its response with, without reading the body.

    An accepted GET opens an SSE stream that never ends, and the ASGI transports
    buffer the whole body, so a check that wrongly accepted the request would hang
    the test instead of failing it on the status.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in {"Host": "test", **headers}.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    request_sent = False
    start = {}

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.sleep_forever()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tg:

            async def send(message):
                if message["type"] == "http.response.start":
                    start["status"] = message["status"]
                    start["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}
                    tg.cancel_scope.cancel()

            tg.start_soon(app, scope, receive, send)
    return start["status"], start["headers"]


@pytest.mark.parametrize("credential", REPLAY_CREDENTIALS)
@pytest.mark.parametrize("method", REPLAY_REQUESTS)
@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_session_id_without_owner_bearer_is_401(anon_client, path, method, credential):
    request_headers, body = REPLAY_REQUESTS[method]
    async with running_app():
        session = await _open_session(anon_client, path)
        status, headers = await _response_start(
            method, path, {**request_headers, **session, **REPLAY_CREDENTIALS[credential]}, body
        )
        # Control: the session is live, and the refused request did not end it
        control = await anon_client.post(
            path, json=TOOLS_LIST, headers={**OWNER_BEARER, **session, "Accept": MCP_ACCEPT}
        )

    # Asserted outside the lifespan, so a failure is reported here and not
    # re-raised through the lifespan's task group
    assert status == 401, f"{method} {path} on an open session with {credential} was not refused"
    assert headers["www-authenticate"].startswith("Bearer")
    assert control.status_code == 200, control.text
    assert "list_sections" in control.text
