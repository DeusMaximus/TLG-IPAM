# Deployment and exposure

How to run TLG-IPAM on your own network, and how to publish its MCP server for a web AI assistant without publishing the rest of it.

The examples use `ipam.example.com` for the public name and `ipam.home.arpa` for the private one. Replace them with yours.

| Setup | Configuration | What is reachable from the internet |
|-------|---------------|-------------------------------------|
| Local | `API_KEY` | Nothing. Local MCP clients send the owner key (README, "Connect an AI assistant") |
| Remote | also `PUBLIC_BASE_URL` | The paths in [What you publish](#what-you-publish), through your tunnel or reverse proxy |
| Remote with SSO or MFA | as Remote | The same, with your access proxy's login on [two of the paths](#sso-or-mfa) |

If you only use local clients, you need [Running the server](#running-the-server) and [Scan agents](#scan-agents).

## Running the server

- **An unprivileged container or LXC.** A Docker host in an unprivileged LXC or its own VM will do. The image already runs the server as an unprivileged user, uid 10001 (README, "The container's user"). Do not add `--privileged`, host networking or a mount of the Docker socket: the server needs none of them. If the server were broken into, whatever it can reach on your network is what the intruder can reach.
- **Isolated.** Give it its own VLAN, or firewall rules that allow only what it needs:

  | Who | Connects to |
  |-----|-------------|
  | Your own browser and local MCP clients | the server, over your LAN or VPN |
  | Your tunnel connector or reverse proxy (remote setups) | the server's port, for [the published paths](#what-you-publish) |
  | Scan agents | `/api/v1/agent/` over HTTPS ([below](#scan-agents)) |
  | The backup host | `GET /api/v1/backup` ([backup and restore](backup-and-restore.md)) |

  The server's own code starts no connection to anything, so it needs no route to the rest of your network. A tunnel connector connects out to its provider by itself. Building or updating the image needs the internet, on whichever host builds it.
- **Port 8080 is never reachable from the internet.** It serves everything: the web UI, the REST API, the backup. `docker-compose.yml` publishes it on every address of the host, for your LAN, so do not forward it, and if the host has a public address (a public IPv6 address counts), keep the internet off the port with a firewall in front of the host: ports Docker publishes [get past `ufw`'s rules](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw). Only your tunnel or reverse proxy publishes anything, and only the paths below.
- **One worker.** Run one copy of the server. Do not change the image's command, add workers or run replicas behind a load balancer (README, "One worker").
- **In the backup schedule.** Set up the [scheduled pull](backup-and-restore.md) from another host. The one database file holds the OAuth links too, as hashes. A client that has refreshed its tokens since a snapshot was taken may have to be linked again after you restore that snapshot.

## Scan agents

- **One agent per VLAN, on a host with one interface.** ARP, and so MAC addresses, works only on the agent's own VLAN. Use a Linux host that is already there, or a small container.
- **Check `ALLOWED_CIDRS`.** It is the agent's own limit on what it scans, whatever the server asks. The default is the private and carrier-grade NAT ranges. If your network uses other ranges, or you want an agent held to its own VLAN, set it (README, "Settings"). `ALLOWED_DNS_SERVERS` limits the DNS servers it asks in the same way.
- **Agents are not public.** An agent connects to the server over HTTPS (plain HTTP only to its own loopback address) and needs `/api/v1/agent/` alone. If the agent and the server share a network, publish nothing for it: put TLS in front of the server on the private side, on an address or port the internet cannot reach ([never as another name on the public listener](#publish-with-nginx)). For an agent on a VLAN that should not reach the web UI or the API, give that private proxy a listener that forwards `/api/v1/agent/` only: a firewall cannot tell one path from another. For an agent at another site, use a VPN. The public configurations on this page do not pass the agent's paths.

### What to scan

- **Leave DHCP pools unscanned**, which is the default, unless you keep static leases inside a pool and want them tracked ("Scan DHCP pool" in the subnet's **Scanning** section). The pool is where phones and laptops live, and scan results for it are something a linked client can read.
- **Exclude any reserved range holding devices that should not be probed**, or that somebody else manages (untick **Scan** on the range). No packet is sent to an excluded address, even one with a host recorded on it.
- Add subnets one at a time. Nothing is scanned until you set a subnet up for it.

## Remote MCP

With `PUBLIC_BASE_URL` set, the server is its own OAuth authorisation server for `/mcp`, so that web MCP clients, which cannot send a static key, can be linked to it. Read this before turning it on:

- **A linked client can read the whole IPAM**: subnets, VLANs, gateways, hostnames, MAC addresses, notes, scan findings and the change log. That is the cost of remote MCP. A link is read-only unless you [allow writes](#writes), which takes both a server setting and a choice made for that link.
- **You can revoke any link** on the web UI's **Linked clients** page. Its tokens are refused from the client's next request. See [Ending a link](#ending-a-link).
- Linking needs a code that only the holder of the owner key can get. There is no standing password for it.

### What you publish

| Path | Who requests it | An interactive login in front of it |
|------|-----------------|-------------------------------------|
| `/mcp` (and `/mcp/`) | the client | breaks the client |
| `/mcp/token` | the client | breaks the client |
| `/mcp/register` | the client | breaks the client |
| `/.well-known/oauth-authorization-server` | the client | breaks the client |
| `/.well-known/oauth-protected-resource/mcp` | the client | breaks the client |
| `/mcp/authorize` | your browser | [allowed](#sso-or-mfa) |
| `/mcp/consent` | your browser | [allowed](#sso-or-mfa) |

Publish these seven and nothing else. Everything else stays private: the web UI (`/` and its pages), every `/api/` path (the REST API, the API docs, the backup, the link window's own routes and the agents' routes) and `/health`. The server answers `404` to any other path under `/.well-known/`, and a proxy that answers `404` there itself does no harm.

### Turn it on

In `.env`, beside `API_KEY`:

```sh
PUBLIC_BASE_URL=https://ipam.example.com
MCP_REMOTE_WRITES=false
```

then `docker compose up -d`. Until `PUBLIC_BASE_URL` is set, the OAuth and discovery paths answer `404`.

**Apply every later change to these with `docker compose up -d` too. `docker compose restart` does not read `.env` again**: it starts the same container with the old values, so a setting you turned off stays on.

`docker-compose.yml` passes both values from `.env` into the container. The server itself reads only its environment, never a `.env` file. If you run it another way (an older copy of the Compose file, `docker run`, by hand), set them in its environment.

`PUBLIC_BASE_URL` is the public origin and nothing else: `https://`, a lowercase host name, and a port only when it is not 443. No path (not `/mcp`), no trailing slash, query, fragment, user name or spaces. The server refuses to start with any other spelling. (Plain `http://` is taken only for `localhost`, `127.0.0.1` and `[::1]`, to test on one machine.) Every URL the server tells a client comes from this value, never from the request's `Host` or `X-Forwarded-*` headers, so set it to exactly the name your tunnel or proxy serves. Choose it once: a client keeps the URL it was linked with, so changing it means linking every client again. Changing it does not revoke anything.

`MCP_REMOTE_WRITES` says whether a link may be allowed to make changes ([Writes](#writes)). It is `true` or `false`, in any case; unset or empty is `false`. With any other value (`1`, `yes`, `on`, a value with a space) the server does not start, and its log says why. It is read when the server starts. Without `PUBLIC_BASE_URL` it changes nothing.

The owner key keeps working on `/mcp` for your local clients. It works on the published `/mcp` too, so whoever has it can use every MCP tool from the internet: use a long random key, as `.env.example` shows.

Pick **one** of the two ways to publish below, then [check it from outside](#check-it-from-outside).

### Publish with a Cloudflare Tunnel

> Written from Cloudflare's documentation. The rules were checked offline with `cloudflared` 2026.11.2 (`tunnel ingress validate` and `tunnel ingress rule`); no tunnel was run.

For a [locally managed tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/): [create the tunnel and its DNS record](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/) for `ipam.example.com` as Cloudflare describes, and use this as its `config.yml`, with your tunnel's ID. It assumes `cloudflared` runs on the host that runs the server:

```yaml
tunnel: <Tunnel-UUID>
credentials-file: /etc/cloudflared/<Tunnel-UUID>.json

originRequest:
  # under the server's 5 s, so cloudflared never reuses a connection the server is closing
  keepAliveTimeout: 4s

ingress:
  - hostname: ipam.example.com
    path: ^/(mcp(/(authorize|token|register|consent)?)?|\.well-known/oauth-(authorization-server|protected-resource/mcp))$
    service: http://127.0.0.1:8080
  - service: http_status:404
```

The `path` is a regular expression, and `^` and `$` are what make it a list of exact paths: without them `cloudflared` matches any path that contains one. Whatever does not match gets the `404` of the last rule and never reaches the server. If `cloudflared` runs elsewhere, change `service` to the server's address.

**No other rule or route, in this tunnel or another, may point at the server.** A second public host name for the web UI, or a wildcard rule that reaches the IPAM's port, publishes everything under that name.

Test the rules before you run the tunnel, and again after any edit:

```sh
cloudflared tunnel --config config.yml ingress validate
cloudflared tunnel --config config.yml ingress rule https://ipam.example.com/mcp            # rule #0
cloudflared tunnel --config config.yml ingress rule https://ipam.example.com/api/v1/hosts   # rule #1, the 404
```

With a tunnel [managed from the dashboard](https://developers.cloudflare.com/cloudflare-one/networks/routes/add-routes/#add-a-published-application-route), add one published application route for the host name, with the same expression as its path and the server as its service, keep the catch-all answering `404`, and add no other route for that host name. Under the route's additional settings, set the idle connection expiration (`keepAliveTimeout`) to 4 s. This form was not checked at all; the outside check below is what shows it is right.

**Leave "Protect with Access" off** (`originRequest.access`) on every rule of this tunnel. With a rule that requires Access validation, `cloudflared` 2026.10.0 and later can [normalise a path before matching it](https://developers.cloudflare.com/changelog/post/2026-10-22-cloudflared-path-normalization/) while sending the server the path as it arrived (off by default in 2026.10 and 2026.11; Cloudflare plans to turn it on by default in releases from 2027-01-25). A path can then match the list above and mean something else to the server. Offline, `cloudflared` 2026.11.2's `ingress rule` matched `/api/v1/hosts/..%5C..%5C..%5C..%5Cmcp` to the allowed rule once a rule required Access, and this server treats that path as a request to `/api/v1/hosts/{id}`. It would still need the owner key, but the API would no longer be private. An Access login on [the two browser paths](#sso-or-mfa) does not need this setting.

Settings elsewhere in the same Cloudflare zone that can break clients or the consent page:

- **Challenges.** A client cannot solve one. [Bot Fight Mode](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/) covers the whole domain and cannot be skipped by a WAF custom rule: turn it off for this zone. Do not put a managed or JavaScript challenge rule on the five client paths.
- **AI agent policies.** If the zone [blocks AI agents](https://developers.cloudflare.com/bots/additional-configurations/block-ai-bots/), check that your assistant's requests still arrive: an assistant's own connector may be counted as one.
- **Page rewriting.** Turn off features that add scripts or rewrite HTML for this host name (Zaraz, Rocket Loader, Web Analytics injection and the like). See [what the consent page needs](#what-the-consent-page-needs).

### Publish with nginx

> This configuration was run against the server with nginx 1.30.8 in a container, a self-signed certificate and both on one machine, as were the checks on this page that name nginx. It was not run on a public host.

For nginx on the host that runs the server, terminating TLS with your own certificate. For example, `/etc/nginx/conf.d/tlg-ipam.conf`:

```nginx
limit_req_zone  $binary_remote_addr zone=ipam_req:1m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=ipam_conn:1m;

upstream tlg_ipam {
    server 127.0.0.1:8080;
    keepalive 4;
    # under the server's 5 s, so nginx never reuses a connection the server is closing
    keepalive_timeout 4s;
}

server {
    listen 443 ssl;
    server_name ipam.example.com;
    ssl_certificate     /etc/nginx/tls/ipam.example.com.crt;
    ssl_certificate_key /etc/nginx/tls/ipam.example.com.key;

    # what the proxy holds for the server (see "What the proxy holds")
    client_header_timeout       10s;
    large_client_header_buffers 4 8k;
    client_body_timeout         60s;
    client_max_body_size        2m;
    keepalive_timeout           75s;
    send_timeout                60s;
    limit_req        zone=ipam_req burst=20 nodelay;
    limit_req_status 429;
    limit_conn       ipam_conn 20;

    proxy_http_version 1.1;
    proxy_set_header   Connection "";
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-For $remote_addr;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;

    # The published paths, each matched exactly. The path after the upstream's name is
    # the one the server is sent, so it is only ever sent these.
    location = /mcp           { proxy_pass http://tlg_ipam/mcp; }
    location = /mcp/          { proxy_pass http://tlg_ipam/mcp/; }
    location = /mcp/token     { proxy_pass http://tlg_ipam/mcp/token; }
    location = /mcp/register  { proxy_pass http://tlg_ipam/mcp/register; }
    location = /.well-known/oauth-authorization-server {
        proxy_pass http://tlg_ipam/.well-known/oauth-authorization-server;
    }
    location = /.well-known/oauth-protected-resource/mcp {
        proxy_pass http://tlg_ipam/.well-known/oauth-protected-resource/mcp;
    }
    # the two pages your own browser visits: an SSO login goes here and nowhere else
    location = /mcp/authorize { proxy_pass http://tlg_ipam/mcp/authorize; }
    location = /mcp/consent   { proxy_pass http://tlg_ipam/mcp/consent; }

    location / { return 404; }
}
```

Check it with `nginx -t`, then reload. Three things to keep as they are:

- Keep the `=` on every `location`, and the path on every `proxy_pass`. A `location /mcp` without `=` is a prefix. A `proxy_pass` with no path sends the server the request's path as the client wrote it, which is not always the path nginx matched.
- Do not add a second `location` that forwards anything else, in this `server` block or another for the same name.
- **No other `server` block on a listener the internet can reach may forward to the IPAM**, under any name. A name is not a lock: whoever can reach the port picks the virtual server by sending that name in `Host` or SNI. A private block such as `server_name ipam.home.arpa; location / { proxy_pass … }` on the same port 443 gives them the whole UI and API. In a run of exactly that, `Host: ipam.home.arpa` got `200` on `/` and `/health`, and the API answered. Put a private proxy on an address the internet cannot reach (`listen 192.168.1.10:443 ssl;`, with the public block on another address or port), or on another host.

If nginx serves other names on the same port, note that a connection [starts in the port's default server](https://nginx.org/en/docs/http/server_names.html#virtual_server_selection), whose settings apply to some of the reading of a request's head before nginx knows which name was asked for. Make this block the default (`listen 443 ssl default_server;`), or set `client_header_timeout` and `large_client_header_buffers` in the default one too, and run the header checks below.

If nginx runs on another host, change the `upstream`'s `server` line, and firewall port 8080 so that of that network's hosts only the proxy, and whatever else the table in [Running the server](#running-the-server) names, can reach it.

#### Client addresses in the server's log

The server takes `X-Forwarded-For` only from an address it is told to trust, by default `127.0.0.1`. With Docker it may see your proxy under another address, such as the compose network's gateway. Make a request through the proxy, read the address at the start of its line in `docker compose logs ipam`, and if it is the proxy's and not the client's, set in `.env`:

```sh
UVICORN_FORWARDED_ALLOW_IPS=172.18.0.1     # the address the log showed
```

Never set `*` unless only the proxy can reach port 8080. This changes what the log shows; the server decides nothing from a client's address.

### Another reverse proxy

Whatever you use has to do the same: forward the seven paths, matched exactly, and answer everything else itself. Check how it matches paths. If it normalises a path to match it (resolving `..`, merging slashes, treating `\` as `/`) but forwards the original, a request can match your list and reach something else. Either forward the path it matched, or use a proxy that matches the path as the server will read it. Then run the checks below, and the ones in [What the proxy holds](#what-the-proxy-holds).

### Check it from outside

From a machine outside your network, with no link window open:

```sh
base=https://ipam.example.com

# published: the server answers
for p in /.well-known/oauth-authorization-server /.well-known/oauth-protected-resource/mcp \
         /mcp /mcp/authorize /mcp/consent; do
  printf '%-45s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' "$base$p")"
done
for p in /mcp/token /mcp/register; do
  printf '%-45s %s\n' "POST $p" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base$p")"
done

# private: your proxy answers 404
for p in / /agents /health /api/v1/hosts /api/v1/backup /api/v1/docs /api/v1/openapi.json \
         /api/v1/link-window /api/v1/agent/checkin /.well-known/openid-configuration \
         /mcp/authorize/ /mcp/../api/v1/hosts /mcp%2F..%2Fapi%2Fv1%2Fhosts \
         '/api/v1/hosts/..%5C..%5C..%5C..%5Cmcp'; do
  printf '%-45s %s\n' "$p" "$(curl -s --path-as-is -o /dev/null -w '%{http_code}' "$base$p")"
done
```

Expected:

| Request | Status |
|---------|--------|
| the two discovery documents | `200` |
| `/mcp` | `401` |
| `/mcp/authorize`, `/mcp/consent` | `403` (no link window is open) |
| `POST /mcp/token` | `401` |
| `POST /mcp/register` | `403` (no link window is open) |
| every private path | `404` |

If the same proxy serves any private name, run the private paths again with that name, from outside: `curl -s -o /dev/null -w '%{http_code}\n' --connect-to ::ipam.example.com: -k https://ipam.home.arpa/health` asks your public address for the private name. It must not be `200`.

A `200` on `/` or `/health`, or a `401` on an `/api/` path, means the request reached the server: stop publishing and fix the rules. To see for yourself which side answered, read the body: the server's errors are JSON (`{"error": …}` or `{"detail": …}`), your proxy's `404` is its own (empty from `cloudflared`, an HTML page from nginx). The server's log (`docker compose logs ipam`) lists the requests that reached it, and no private path should be in it from this run.

Also open `https://ipam.example.com/.well-known/oauth-protected-resource/mcp`: `resource` must be `https://ipam.example.com/mcp`, exactly the URL you will give a client. If you have put [SSO](#sso-or-mfa) in front of the two browser paths, `/mcp/authorize` and `/mcp/consent` answer with your access proxy's login instead of `403`.

## SSO or MFA

You can put your access proxy's login, with its MFA, in front of linking, in addition to the link code. This needs no setting in TLG-IPAM, which has no OIDC support of its own. It works because **only your own browser ever visits `/mcp/authorize` and `/mcp/consent`**. So:

- **Require the login on exactly those two paths**: `/mcp/authorize` and `/mcp/consent`.
- **Pass the other five through untouched**: `/mcp`, `/mcp/token`, `/mcp/register` and the two discovery documents. No login, no redirect to one, no challenge, no "continue" page. A client cannot complete any of them. Leave their headers alone too, `Authorization` above all (it carries the client's token, and on `/mcp/token` it may carry the client's secret), along with `Mcp-Session-Id` and `WWW-Authenticate`, and do not buffer or cut the event streams on `/mcp`.

The login guards the making of a link. It does not guard a link already made: the client's requests go to `/mcp` with its own token and never see your login again.

### What the consent page needs

`/mcp/consent` is an HTML form, and the server is strict about it. Through your access proxy:

- **The browser must see it at `PUBLIC_BASE_URL`.** The form's post is refused ("This form was not accepted") when its `Origin` header is present and is not `PUBLIC_BASE_URL`. So the proxy must not serve the page under another host name, and must not rewrite the `Origin` header.
- **Do not set another `Referrer-Policy` on it.** The page sends `same-origin`. With `no-referrer`, which a proxy's security-header settings may add, a browser sends [`Origin: null`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin) with the form, and it is refused.
- **Pass the query string** (`?txn=…`, and the whole of `/mcp/authorize`'s) through the login and back.
- **Add nothing to the page**: no injected script, banner, toolbar or analytics. The page is served with a Content-Security-Policy that loads nothing but its own stylesheet, so what a proxy adds is blocked or breaks the page. Do not remove or replace that header, or the page's other security headers, to make an addition work.
- **Sign in before you submit.** If your login session runs out while the consent page is open, the form's post goes to the login instead of the server. Start again from the client.

### With nginx

Add your login to the two `location` blocks for `/mcp/authorize` and `/mcp/consent`, and to no other: `auth_request` to your identity proxy, or, to see the arrangement work, `auth_basic`:

```nginx
    location = /mcp/authorize {
        auth_basic "TLG-IPAM linking";
        auth_basic_user_file /etc/nginx/tls/linking.htpasswd;
        proxy_pass http://tlg_ipam/mcp/authorize;
    }
```

(and the same two lines in `location = /mcp/consent`). This was run with `auth_basic`. No identity proxy or `auth_request` was run. Basic authentication is a password, not MFA.

### With Cloudflare Access

> Written from Cloudflare's documentation. Not run.

1. Create a [self-hosted application](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) with two public hostname entries for `ipam.example.com`, with the paths `mcp/authorize` and `mcp/consent`, and an Allow policy for yourself alone. Require MFA there or at your identity provider.
2. Make no application that covers the whole host name. If something already puts Access on the whole host name, the other paths need their own applications with a [Bypass policy](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/#bypass) for Everyone: `mcp`, and `.well-known`. Access gives the [more specific path precedence](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/), so `mcp/authorize` and `mcp/consent` keep your Allow policy while `mcp`, `mcp/token` and `mcp/register` are bypassed. Bypass means Access neither checks nor logs those requests; the server's own credentials guard them, and your tunnel's path list still decides what reaches it.
3. In the application's [cookie settings](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/), do not set `SameSite` to `Strict`: your browser arrives from the client's own site, and Cloudflare warns that `Strict` can cause a redirect loop.
4. Leave the tunnel's **Protect with Access** off ([why](#publish-with-a-cloudflare-tunnel)). Cloudflare recommends that an origin also validate the Access token. Here Access is a second check in front of a link that still needs the link code, which Access cannot supply.

### Check the login

From a browser or `curl` that has not signed in:

```sh
base=https://ipam.example.com
curl -si "$base/mcp/authorize"        # your login, not the server's "Linking a client is started from…"
curl -si "$base/mcp/consent"          # your login, not the server's "No link window is open"
curl -si "$base/mcp/%63onsent"        # your login: an encoded spelling is the same page to the server
curl -s -o /dev/null -w '%{http_code}\n' "$base/mcp"                                         # 401, from the server
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$base/mcp/token"                           # 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$base/mcp/register"                        # 403
curl -s -o /dev/null -w '%{http_code}\n' "$base/.well-known/oauth-authorization-server"      # 200
```

None of the last four may be a redirect or your login page. Then link a client, which shows the consent form's post gets through.

## Linking a client

A client can register and ask to be authorised only while a **link window** is open. You open it from your private network, on the web UI's **Linked clients** page: choose **Link a client**, then **Open link window**. The dialog shows the one-time 10-character **link code**, the URL to give the client, and how long the window has left. The code is shown only in that dialog, so closing the dialog, or leaving the page, cancels the window. The dialog follows the window: it says when the client is linked or the window runs out, and how many wrong codes have been typed.

Without the web UI, open it with the API and the owner key:

```sh
read -rs API_KEY                      # paste the owner key; it is not echoed
printf 'X-API-Key: %s\n' "$API_KEY" |
  curl -sS -X POST http://ipam.home.arpa:8080/api/v1/link-window \
    -H @- -H 'Content-Type: application/json' -d '{}'
```

The answer holds `code`, the link code, `expires_in_s`, and `window_id`. The code is shown this once.

1. **Open the window first**, then add `https://ipam.example.com/mcp` to the client. Some clients register as soon as you enter the URL, and a registration with no window open is refused.
2. Connect in the client. Your browser is sent to the consent page (through your access proxy's login, if you set one up).
3. **Check the host in large type.** That is where the access goes. It must be the one you expect for the assistant you are linking. The client's name is whatever the client chose to call itself, and is marked unverified. If the host is wrong, choose **Do not link**.
4. Type the link code and choose **Link this client**. The window closes: one window makes one link. To link another client, open another window.

The window lasts 10 minutes, and there is one at a time: opening another replaces it, with a new code. With the API, to see it or cancel it, send the same request with `GET` or `DELETE` in place of `-X POST …`. `DELETE` with `?window_id=<the window_id you were given>` cancels that window only, and leaves alone one opened since, from the web UI or another script. Without it, `DELETE` cancels whatever window is open:

```sh
printf 'X-API-Key: %s\n' "$API_KEY" | curl -sS -X DELETE http://ipam.home.arpa:8080/api/v1/link-window -H @-
```

Five wrong codes on one consent page end that request: start again from the client. They do not close the window or change its code. The window's state counts them (`wrong_codes`), and the change log gets one entry for a window's first.

**What somebody else can do while your window is open.** Anyone who can reach the public paths can tell whether a window is open. They can send enough authorise requests to push yours out of the 32 the server holds (your page then says the request is no longer pending), or enough registrations to evict your client's before you approve it (the page then says to start again from the client). They can do either as often as they like until the window ends. The server does not try to protect a registration whose request is waiting for you: that would make the second way harder, but the first still ends your request with at most 32 requests of theirs, so it was not worth it ([#190](https://github.com/DeusMaximus/TLG-IPAM/issues/190)). A per-client request rate on the proxy in front of the server makes either slower. Neither closes the window or links anything without your code: start the client again, and type the same code. Open a window only when you are about to use it. If you did not start the request on your consent page, do not type the code into it, and give the code to nobody.

### Writes

A link is read-only unless you do both of these:

1. Set `MCP_REMOTE_WRITES=true` in `.env` and run `docker compose up -d`.
2. Open its link window for a read-write link: in the **Link a client** dialog, tick **Allow this client to make changes**, which is offered only while the setting is on. With the API, send `-d '{"can_write": true}'` in place of `-d '{}'`. The consent page then shows **Read and write** as the access. If it shows that and you did not mean it, choose **Do not link**.

While the setting is off, an API request for a read-write window is refused (`409 REMOTE_WRITES_OFF`) and no window opens. The window's state says whether the setting is on (`remote_writes`) and, while a window is open, what its link may do (`can_write`).

A read-only link is not shown the write tools, and a call to one answers `Unknown tool`. A read-write link can create, change and delete hosts, add and delete reserved ranges, and ask for scans. It cannot configure scanning or widen what is scanned: it cannot delete a range that is excluded from scanning. Whoever holds its tokens can change or delete those records, and text it puts in a hostname or a note is read later by your own assistants, so allow it only for a client you would trust with that, and keep the [backups](backup-and-restore.md) running. The change log names the link as the `actor` of each of its writes: `grant:<client name>`, a name the client chose for itself, or `grant-id:<grant id>` if the server kept no name.

The choice is fixed when the window is opened. Turning the setting on does not upgrade a read-only link.

**To stop links writing**, set `MCP_REMOTE_WRITES=false` (or remove the line) and run `docker compose up -d`, not `restart`. Every link loses write access at once, without being linked again, and goes on reading and refreshing. This ends no link. **A link made read-write writes again whenever the setting is turned back on.** While it is off, **Linked clients** shows such a link as **Read-only now**. To stop one link for good, [revoke it](#ending-a-link).

The owner key reads and writes whatever the setting is.

### Ending a link

**Revoke it** on the web UI's **Linked clients** page. The page lists each link by the host its codes were sent to when you approved it, with the name the client gave itself, what it may do, when it was linked and its **Last contact**. **Revoke** asks first, then ends the link: its tokens are deleted, so the client's next request is refused, and the change log records the revoke as yours. A revoked link stays listed under **Recently revoked**, and cannot be restored: to use that client again, link it again with a new code. With the API, `GET /api/v1/linked-clients` lists the links and `POST /api/v1/linked-clients/<id>/revoke` revokes one, with the owner key.

- **Removing the connector in the client does not revoke anything here.** The server has no revocation endpoint. Revoke the link on this server too.
- The server revokes a link by itself when one of its old refresh tokens is used again, a sign that two parties hold its tokens, and logs it. The link then shows under **Recently revoked**. It revokes a link for no other reason.
- **A link marked "Never completed"** is one you approved whose client never collected its tokens, so the code your approval sent it ran out after five minutes (the client crashed, lost the redirect, or the tab was closed). As of the list it holds no token and cannot get one; a client that finished exchanging just as the list was read shows as completed at the next refresh. Revoke it to remove it. The server keeps 50 client registrations, and a registration keeps its place while any of its links is unrevoked, so revoking the marked link frees a place only when that registration holds no other link. The note says which: if the same registration also holds a working link, which happens when you approved the client again and that time it completed, revoking the marked one frees nothing and the client is already linked. Link the client again only if no other link to it works.

Two ways stop every link at once, and end none:

- **To stop every link writing**, leave `MCP_REMOTE_WRITES` off ([Writes](#writes)). Each can still read everything.
- **To stop every link**, remove `PUBLIC_BASE_URL` from `.env` and run `docker compose up -d` (`restart` would leave it set). Linked clients' tokens are refused from then on, and the public OAuth paths answer `404`. The links are stored, not deleted: set the value again and those not revoked work again, and a read-write one can write again if `MCP_REMOTE_WRITES` is `true` then. **Linked clients** still lists them, and revokes them, while the setting is unset.

If you think the owner key itself has leaked, change `API_KEY`. Links that somebody made with the old key stay: revoke them as above.

## What the proxy holds

The server bounds what one connection and one request cost. It cannot bound how many there are, or tell clients apart behind a proxy. That is the job of whatever is in front of it. Each item says what the nginx configuration above sets, what to use elsewhere, and how to check it **against your public name**. A refusal or timing from your proxy shows its limit is in force. The server's own JSON error (`{"error": {"code": …}}`) shows the request reached the server.

**Requests per client.**
- nginx: `limit_req` bounds each client address's request rate (10 a second with bursts of 20, answered `429`), and `limit_conn` its requests in progress (20, answered `503`). An open MCP event stream is one request in progress. A web assistant's requests may all come from a few of its provider's addresses, so raise the numbers if yours is refused. `limit_conn` counts a request only once its whole header has been read, so it does not count sockets.
- Cloudflare: a [rate limiting rule](https://developers.cloudflare.com/waf/rate-limiting-rules/) for the host name, which must block rather than challenge on the client paths. Caddy has neither built in.
- Check the rate: send requests much faster than it and see the proxy's `429`: `for i in $(seq 60); do curl -s -o /dev/null -w '%{http_code}\n' https://ipam.example.com/mcp & done; wait`.
- Check requests in progress (nginx): from one client, hold 20 requests open, each a complete `POST /mcp` head with `Content-Length: 1000000` and then a byte of body a second, and see the next one refused with `503`. Start them more slowly than the request rate allows, or `429` refuses them first. nginx holds these bodies itself and the server sees none of them, and a byte a second stays inside `client_body_timeout`, so they stay held until you close them (in the run here, one was still held after 90 s).

**Sockets with no complete request head** (silent, or sending a byte at a time).
- nginx passes a request on only when its head is complete, so these never reach the server. `client_header_timeout 10s` ends them (nginx's default is 60 s), `large_client_header_buffers` bounds their size, and `worker_connections` their number, for all clients together, so one client can take them all. nginx has no per-client limit on them. If you want one, use a per-source connection limit in the firewall of the host clients connect to directly (nftables `ct count`, iptables `connlimit`). Behind a tunnel or another proxy every client has that hop's address and would share one count.
- Caddy: the server's `read_header` timeout. Client sockets to Cloudflare end at Cloudflare.
- Check the timeout (nginx, Caddy): `time openssl s_client -quiet -connect ipam.example.com:443`, type `GET /mcp HTTP/1.1`, press Return once and wait. The connection ends at your limit, about 10 s, not at the default. nginx sends nothing back, and its access log shows `408`. For a firewall limit: open more connections from one address than it allows, send nothing, and see the extra ones refused or never connect, while another address still connects.

**Header size.**
- nginx: `large_client_header_buffers 4 8k` (a header line over 8 KiB is `400`). Caddy: `max_header_size`. Cloudflare [accepts up to 128 KB](https://developers.cloudflare.com/fundamentals/reference/connection-limits/) of request headers.
- Check: write a large header to a file (a command-line argument that long may be refused by the shell) and send it:
  `{ printf 'X-Pad: '; head -c 200000 /dev/zero | tr '\0' a; echo; } > pad.txt; curl -si -H @pad.txt https://ipam.example.com/mcp`. The refusal is your proxy's, not the server's `431 HEADERS_TOO_LARGE`. Through Cloudflare, a header between 64 KiB and its limit (use `100000`) reaches the server and gets that `431`, which shows the server's own limit at work.

**Slow readers, and event streams.** A timeout meant to end a stalled reader must not end an MCP event stream, which is held open as long as an assistant's session lasts.
- The server sends each stream with `X-Accel-Buffering: no`, which turns nginx's response buffering off for it, and a `: ping` comment every 15 s. Leave `proxy_buffering` on for everything else.
- nginx: `send_timeout 60s` and `proxy_read_timeout 60s` are times *between* writes and reads, so a stream is unaffected while they stay above 15 s. Add no limit on a whole response.
- Caddy: `write_idle` is per write and safe. Do not set `write` (a limit on the whole response, which ends every stream at that age, whatever a route's own timeouts say) or a minimum rate on `write_idle`.
- Cloudflare: its [proxy read timeout](https://developers.cloudflare.com/fundamentals/reference/connection-limits/) is longer than the 15 s between pings.
- Check the stream: open one with your owner key and leave it for twice your longest timeout. `-H @-` keeps the key off the command line:

  ```sh
  url=https://ipam.example.com/mcp
  mcp() { printf 'Authorization: Bearer %s\n' "$API_KEY" | curl -s -H @- \
    -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' "$@" "$url"; }
  sid=$(mcp -D - -o /dev/null -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}' |
    awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
  mcp -H "Mcp-Session-Id: $sid" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  mcp -N -H "Mcp-Session-Id: $sid"          # stays open, with ": ping" every 15 s; Ctrl+C ends it
  ```

- Check the stall limit (nginx, Caddy): it needs a response too large for the buffers on the way whose reader stops reading altogether. The published paths have no large file, so it takes a large MCP answer, such as `list_hosts` on a big IPAM; a client that reads nothing is then cut off at about your limit, while a stream open beside it goes on. In the run behind this page the answer was 35 MB, from 4,000 test hosts with long notes: the connection was closed about 60 s after the client stopped, part of the answer sent. The server's own limit is 60 s too, so the timing does not say which of the two closed it. If your IPAM cannot make an answer that large, treat the stall limit as set but not shown.
- No setting here bounds a client that keeps reading very slowly.

**Idle connections.**
- nginx: `keepalive_timeout 75s` for clients. For its own connections to the server, the `upstream` block's `keepalive_timeout 4s` stays under the server's 5 s, so nginx never reuses a connection the server is closing.
- cloudflared: `originRequest.keepAliveTimeout: 4s` (default 1 min 30 s). Caddy: `idle` for clients.
- Check the client side (nginx, Caddy): connect with `time openssl s_client -quiet -connect ipam.example.com:443`, send `GET /mcp HTTP/1.1`, `Host: ipam.example.com` and a blank line, then wait. The proxy closes the connection at its own limit.

**Request bodies of at least 2 MiB**, the server's own limit.
- nginx: `client_max_body_size 2m` (the default, 1 MiB, is too small for a large tool call), with request buffering on, the default, so the server receives a body at the proxy's speed. `client_body_timeout 60s` is the time allowed between two reads. Caddy: `request_body`'s `max_size`, and its `read_body` or `read_body_idle` timeouts.
- Check: post 1.5 MiB to `/mcp` with no token. The server's `401` (with `WWW-Authenticate: Bearer`) is right; the proxy's `413` means its limit is too small.
  `head -c 1572864 /dev/zero | curl -si -X POST --data-binary @- https://ipam.example.com/mcp`

**Client addresses.** See [Client addresses in the server's log](#client-addresses-in-the-servers-log). Behind a tunnel, trust `cloudflared`'s address as the server sees it. Check: a request made from outside is logged with an outside address.
