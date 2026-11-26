"""The built-in OAuth server as FastMCP sees it (v0.2 spec §6.2, #26).

`IpamOAuthProvider` is the layer between the MCP SDK's handlers and
`services/oauth`, where every rule lives. It is not thinner than this in four places,
because the SDK's interface puts the decision there:

- **Routes.** The provider builds its own route list (`get_routes`) from the SDK's
  handlers, where FastMCP would take the SDK's ready-made list: the token endpoint
  must be given this module's client authenticator, register and authorise are
  closed outside a link window before the handler parses anything, and the consent
  page (`consent.py`, #27) is served beside them.
- **Client authentication** (`HashedSecretAuthenticator`). The SDK's authenticator
  compares the presented secret with a plain one from the provider, and no provider
  hook is handed the presented secret, so a secret stored as a hash can only be
  checked by an authenticator that reads the request itself.
- **Redirect matching** (`RegisteredClient.validate_redirect_uri`). The SDK asks the
  client object it was handed, not the provider.
- **The resource URL** (`_get_resource_url`, here and on `IpamAuth`). FastMCP derives
  it from the path its app serves MCP on, which is `/` under the `/mcp` mount, and
  would publish `<base>/mcp/`. Clients are given, and send, `<base>/mcp`.

Which URLs the server publishes, all from `PUBLIC_BASE_URL` and never from a request
(spec §6.7): the issuer is the base URL itself, the resource is `<base>/mcp`, and the
authorise, token and register endpoints are under the mount, at `<base>/mcp/…`.
"""

import base64
import binascii
from typing import Any
from urllib.parse import unquote

from fastmcp.server.auth import (
    AccessToken,
    AuthProvider,
    MultiAuth,
    OAuthProvider,
    TokenVerificationError,
)
from fastmcp.server.auth.auth import TokenHandler
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_protected_resource_routes,
    validate_issuer_url,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import consent
from .auth import OwnerKeyVerifier
from .database import SessionLocal
from .models import OAuthClient
from .services import oauth

# The names MultiAuth qualifies a token's `client_id` with, by where it was verified
OAUTH_SOURCE = "oauth"
OWNER_SOURCE = "owner"

AUTHORIZE_PATH = "/authorize"
TOKEN_PATH = "/token"
REGISTER_PATH = "/register"
_NO_STORE = {"Cache-Control": "no-store"}
# The codes a registration error answer may carry (RFC 7591 §3.2.2), which the SDK's
# handler turns into a `400`
_REGISTRATION_ERRORS = ("invalid_redirect_uri", "invalid_client_metadata")


class RegisteredClient(OAuthClientInformationFull):
    """A stored registration as the SDK's handlers are handed it. It never holds the
    client secret, which the server does not have; `HashedSecretAuthenticator` checks
    the stored hash. Its redirect check is the provider's (`oauth.match_redirect`),
    not the SDK's whole-URI comparison."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        try:
            return oauth.match_redirect(list(self.redirect_uris or []), redirect_uri)
        except oauth.Refused as refusal:
            raise InvalidRedirectUriError(refusal.description) from None


def _registered(client: OAuthClient) -> RegisteredClient:
    return RegisteredClient(
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uris=client.redirect_uris,
        token_endpoint_auth_method=client.token_endpoint_auth_method,
        grant_types=list(client.grant_types),
        scope=client.scope,
    )


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    """The client id and secret of an `Authorization: Basic` header (RFC 6749 §2.3.1:
    both parts are form-encoded), or None when there is none that can be read."""
    scheme, _, encoded = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        name, separator, secret = base64.b64decode(encoded, validate=True).decode().partition(":")
    except (ValueError, binascii.Error):
        return None
    if not separator:
        return None
    return unquote(name), unquote(secret)


class HashedSecretAuthenticator(ClientAuthenticator):
    """Client authentication for the token endpoint against a hashed secret (§6.4).

    Runs before the token handler reads the grant, so a request that fails it is
    answered `401 invalid_client` with no code or refresh token looked at, and both
    stay usable by the right secret. A confidential client must present its secret the
    way it registered to (`client_secret_post` in the form, `client_secret_basic` in
    the header); a public client presents none. Every failure is one answer: it does
    not say whether the client exists.

    A client registered for `client_secret_basic` may name itself in the header alone
    (RFC 6749 §2.3.1 asks for no `client_id` in the body of an authenticated request).
    The SDK's token request models require the form field, so once such a client has
    authenticated its id is added to the form the handler parses, as FastMCP's own
    authenticator does for a client that names itself in a JWT assertion. A client id
    in both places must be the same one.
    """

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form = await request.form()
        in_form = form.get("client_id")
        basic = _basic_credentials(request)
        if isinstance(in_form, str) and in_form:
            client_id = in_form
        elif in_form is None and basic is not None:
            client_id = basic[0]
        else:
            raise AuthenticationError("Missing client_id")
        async with SessionLocal() as session:
            client = await oauth.get_client(session, client_id)
        presented = None
        named_in_header_only = in_form is None
        if client is not None:
            if client.token_endpoint_auth_method == "client_secret_basic":
                if basic is not None and basic[0] == client_id:
                    presented = basic[1]
            elif named_in_header_only:
                # only a client that authenticates in the header is named by it
                client = None
            elif client.token_endpoint_auth_method == "client_secret_post":
                secret = form.get("client_secret")
                presented = secret if isinstance(secret, str) else None
        if client is None or not oauth.secret_matches(client, presented):
            raise AuthenticationError("Client authentication failed")
        if named_in_header_only:
            request._form = FormData([*form.multi_items(), ("client_id", client_id)])
        return _registered(client)


def _while_linking_is_open(handle, *, page: bool):
    """An SDK handler that answers only while linking is open (`oauth.linking_open`,
    the link window of #27). Outside it the request is refused before the handler reads
    it, so nothing is parsed, looked up or stored. The browser at the authorise
    endpoint is shown a plain page; a client at the register endpoint gets the error as
    JSON. Neither is a redirect, and neither names the client. Inside it, the answer is
    the handler's, marked not to be stored."""

    async def gated(request: Request) -> Response:
        refusal = oauth.LINKING_CLOSED
        if oauth.linking_open():
            try:
                response = await handle(request)
            except oauth.Refused as refused:
                # a refusal that is none of the handler's own error codes: the
                # registry is full of linked clients
                refusal = refused.description
            else:
                # a registration answer holds the client secret in plain (RFC 7591 §3.2.1)
                response.headers.setdefault("Cache-Control", "no-store")
                return response
        if page:
            return PlainTextResponse(refusal + "\n", 403, headers=_NO_STORE)
        return JSONResponse(
            {"error": "access_denied", "error_description": refusal}, 403, headers=_NO_STORE
        )

    return gated


class IpamOAuthProvider(OAuthProvider):
    """The OAuth authorisation server for `/mcp`, on the app's SQLite database."""

    def __init__(self, public_base_url: str, *, remote_writes: bool = False):
        super().__init__(
            # where the operational routes are served: inside the `/mcp` mount
            base_url=public_base_url + oauth.MCP_MOUNT,
            # what clients discover the server by (RFC 8414): the public origin
            issuer_url=public_base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                # A client that registers without a scope is registered for both, so its
                # authorise request for either passes the SDK's check. What a grant may
                # do is decided by `oauth.grant_scopes`, not by what was asked.
                valid_scopes=list(oauth.VALID_SCOPES),
                default_scopes=list(oauth.VALID_SCOPES),
            ),
            # No revocation endpoint: a link is revoked by the owner (#30)
            revocation_options=RevocationOptions(enabled=False),
        )
        self.public_base_url = public_base_url
        # `MCP_REMOTE_WRITES` (#28), passed to every `services/oauth` function that
        # answers with a token's scopes (`oauth.grant_scopes`), and read by the owner's
        # link-window routes through `remote_writes_of`: one value for both rules
        self.remote_writes = remote_writes

    # --- URLs and routes ---

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl:
        """The protected resource, `<base>/mcp`, whatever path FastMCP's app serves MCP
        on inside the mount (see the module docstring)."""
        return AnyHttpUrl(self.public_base_url + oauth.MCP_MOUNT)

    def _well_known_routes(self) -> list[Route]:
        """The two discovery documents: authorisation-server metadata (RFC 8414) and
        protected-resource metadata (RFC 9728, at the well-known path with the
        resource's path after it)."""
        options = self.client_registration_options
        metadata = build_metadata(self.base_url, None, options, self.revocation_options)
        metadata.issuer = self.issuer_url
        return [
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            ),
            *create_protected_resource_routes(
                resource_url=self._get_resource_url(),
                authorization_servers=[self.issuer_url],
                scopes_supported=self.scopes_supported,
            ),
        ]

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Every route of the OAuth server, built here and not taken from
        `create_auth_routes`, so that no endpoint is served that this list does not
        name: discovery, authorise and register (each closed outside a link window),
        the token endpoint with `HashedSecretAuthenticator`, and the consent page the
        authorise step sends the browser to (`consent.py`). FastMCP adds them to the
        app that serves `/mcp`, so the operational ones are at `/mcp/authorize`,
        `/mcp/token`, `/mcp/register` and `/mcp/consent`."""
        self.set_mcp_path(mcp_path)
        validate_issuer_url(self.base_url)
        authorize = AuthorizationHandler(self)
        token = TokenHandler(provider=self, client_authenticator=HashedSecretAuthenticator(self))
        register = RegistrationHandler(self, options=self.client_registration_options)
        return [
            *self._well_known_routes(),
            Route(
                AUTHORIZE_PATH,
                # no CORS: a client sends the owner's browser here, it does not fetch it
                endpoint=_while_linking_is_open(authorize.handle, page=True),
                methods=["GET", "POST"],
            ),
            Route(
                TOKEN_PATH,
                endpoint=cors_middleware(token.handle, ["POST", "OPTIONS"]),
                methods=["POST", "OPTIONS"],
            ),
            Route(
                REGISTER_PATH,
                endpoint=cors_middleware(
                    _while_linking_is_open(register.handle, page=False), ["POST", "OPTIONS"]
                ),
                methods=["POST", "OPTIONS"],
            ),
            Route(
                oauth.CONSENT_PATH,
                # the owner's browser, never a client's fetch: no CORS
                endpoint=consent.endpoint(self.public_base_url),
                methods=["GET", "POST"],
            ),
        ]

    def get_well_known_routes(self, mcp_path: str | None = None) -> list[Route]:
        """The discovery routes for the parent app's root (spec §6.7). FastMCP's own
        list also answers `/.well-known/openid-configuration` with the OAuth metadata;
        this server is no OpenID provider, and that path is a 404 here."""
        return self._well_known_routes()

    # --- Registration and authorisation ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with SessionLocal() as session:
            client = await oauth.get_client(session, client_id)
        return _registered(client) if client is not None else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        try:
            async with SessionLocal() as session:
                stored = await oauth.register_client(
                    session,
                    client_id=client_info.client_id,
                    client_name=client_info.client_name,
                    redirect_uris=list(client_info.redirect_uris or []),
                    auth_method=client_info.token_endpoint_auth_method or "",
                    client_secret=client_info.client_secret,
                    grant_types=list(client_info.grant_types),
                    scope=client_info.scope,
                )
        except oauth.Refused as refusal:
            if refusal.error not in _REGISTRATION_ERRORS:
                # not one of RFC 7591's codes (the registry is full): the route answers
                raise
            raise RegistrationError(refusal.error, refusal.description) from None
        # the handler answers with this object: it says what was registered
        client_info.client_name = stored.client_name
        client_info.grant_types = stored.grant_types
        client_info.scope = stored.scope or None

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Keep the request for the owner's decision and send the browser to the
        consent page (`consent.py`) with the transaction's id. No code is issued here."""
        try:
            txn = oauth.begin_authorisation(
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                state=params.state,
                code_challenge=params.code_challenge,
                resource=params.resource,
                public_base_url=self.public_base_url,
            )
        except oauth.Refused as refusal:
            raise AuthorizeError(refusal.error, refusal.description) from None
        return f"{self.public_base_url}{oauth.MCP_MOUNT}{oauth.CONSENT_PATH}?txn={txn.id}"

    # --- Codes and tokens ---

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with SessionLocal() as session:
            code = await oauth.load_code(
                session, client.client_id, authorization_code, remote_writes=self.remote_writes
            )
        if code is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=code.scopes,
            expires_at=code.expires_at.timestamp(),
            client_id=code.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=AnyUrl(code.redirect_uri),
            redirect_uri_provided_explicitly=code.redirect_uri_explicit,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        try:
            async with SessionLocal() as session:
                issued = await oauth.exchange_code(
                    session,
                    client.client_id,
                    authorization_code.code,
                    remote_writes=self.remote_writes,
                )
        except oauth.Refused as refusal:
            raise TokenError(refusal.error, refusal.description) from None
        return _token_answer(issued)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with SessionLocal() as session:
            found = await oauth.load_refresh(session, client.client_id, refresh_token)
        if found is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=int(found.expires_at.timestamp()),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        """`scopes` is what the request asked, which the SDK has held to
        `load_refresh_token`'s answer; the new token carries what the grant allows now
        (`oauth.grant_scopes`), and the answer's `scope` says so."""
        try:
            async with SessionLocal() as session:
                issued = await oauth.exchange_refresh(
                    session, client.client_id, refresh_token.token, remote_writes=self.remote_writes
                )
        except oauth.Refused as refusal:
            raise TokenError(refusal.error, refusal.description) from None
        return _token_answer(issued)

    async def load_access_token(self, token: str) -> AccessToken | None:
        """The bearer check for `/mcp`. A failed read is no answer about the token:
        raised as `TokenVerificationError`, it is a server error and not the `401` that
        tells a client its token is no good."""
        try:
            async with SessionLocal() as session:
                found = await oauth.load_access(session, token, remote_writes=self.remote_writes)
        except SQLAlchemyError as exc:
            raise TokenVerificationError("the token store could not be read") from exc
        if found is None:
            return None
        return AccessToken(
            token=token,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=int(found.expires_at.timestamp()),
            claims={"grant_id": found.grant_id},
        )

    async def revoke_token(self, token: Any) -> None:
        # the SDK calls this from its revocation endpoint, which is not served
        raise NotImplementedError("the OAuth revocation endpoint is not served")


def _token_answer(issued: oauth.Issued) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        token_type="Bearer",
        expires_in=issued.expires_in,
        scope=" ".join(issued.scopes),
        refresh_token=issued.refresh_token,
    )


class IpamAuth(MultiAuth):
    """`MultiAuth` with the provider's resource URL: FastMCP asks the outer object
    which protected-resource metadata URL to name in a `401`'s `WWW-Authenticate`."""

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        return self.server._get_resource_url(path)


def public_base_url_of(auth: AuthProvider | None) -> str:
    """The public base URL the OAuth server behind `auth` publishes, or "" when `auth`
    is not the OAuth provider (`PUBLIC_BASE_URL` unset)."""
    return auth.server.public_base_url if isinstance(auth, IpamAuth) else ""


def remote_writes_of(auth: AuthProvider | None) -> bool:
    """Whether the OAuth server behind `auth` may give a grant write access
    (`MCP_REMOTE_WRITES`, #28): False when `auth` is not the OAuth provider, since no
    grant can reach `/mcp` then."""
    return auth.server.remote_writes if isinstance(auth, IpamAuth) else False


def mcp_auth(public_base_url: str, *, remote_writes: bool = False) -> AuthProvider:
    """What guards `/mcp` (spec §6.1, §6.2). Without a public base URL, the owner key
    alone, as before M3, and no OAuth route exists (`remote_writes` then changes
    nothing). With one, the OAuth provider with the owner-key verifier beside it: a
    bearer is tried as an access token first, which costs the owner key no statement
    (`oauth.TOKEN_FORMS`), then as the owner key. `remote_writes` is
    `MCP_REMOTE_WRITES`: whether a grant with `can_write` carries `ipam:write`."""
    if not public_base_url:
        return OwnerKeyVerifier()
    return IpamAuth(
        server=IpamOAuthProvider(public_base_url, remote_writes=remote_writes),
        verifiers={OWNER_SOURCE: OwnerKeyVerifier()},
        server_source_id=OAUTH_SOURCE,
    )
