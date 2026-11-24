# TLG-IPAM

A clean, modern, homelab-first IP address manager — faster than phpIPAM, simpler than Netbox, and built from day one to be driven by AI assistants through MCP.

- **Web UI** — dark-mode-first React SPA: section/subnet sidebar tree, host tables, slide-over editing, one-click "allocate next free IP"
- **REST API** — FastAPI with API-key auth, OpenAPI docs at `/api/v1/docs`
- **MCP server** — 16 hand-curated tools at `/mcp` (Streamable HTTP) so your AI assistant can query and update your IPAM
- **Custom device types** — define your own types with icons in Settings (seeded with sensible defaults); renames cascade to hosts
- **Bulk operations** — CSV import in the UI and `bulk_create_hosts`/`bulk_delete_hosts` MCP tools (paste a UniFi export at your assistant); per-record results, partial failures never abort the batch. Multi-select delete in the host table, and force-delete for a whole subnet with confirmation.
- **Audit log** — every write (UI, API, or MCP) is recorded with before/after snapshots, so you can always review what your assistant changed
- **Zero ops** — single container, SQLite in a volume, backup = download one file

See [TLG-IPAM-Project-Brief.md](TLG-IPAM-Project-Brief.md) for the full specification.

## Quick start

```sh
cp .env.example .env          # then replace API_KEY with a real key, e.g. openssl rand -hex 32
docker compose up --build
```

Open http://localhost:8080, paste your API key, done.

The server will not start without a real key: with `API_KEY` unset, empty, still the `change-me` placeholder from `.env.example`, or with a space, tab or newline at either end, it exits with a message that names `API_KEY`. A padded key is refused rather than trimmed, so check a key pasted from a file or a secret store.

## Backups and restore

`GET /api/v1/backup` returns a consistent snapshot of the database (also the Settings → backup button). [docs/backup-and-restore.md](docs/backup-and-restore.md) sets up a scheduled pull of it from another host with dated files and retention, and walks through restoring from one. Try the restore once before you need it.

## One worker

The image starts uvicorn with `--workers 1`, so the container runs a single worker whatever the environment says: setting `WEB_CONCURRENCY` (or `UVICORN_WORKERS`) does not change it. It should stay that way, so do not change the `CMD`, override it with a different command, or run several replicas behind a load balancer. MCP sessions are held in the memory of the process that opened them, and SQLite allows one writer at a time, so a second process would not share them, and adds no capacity a homelab needs.

## Connect an AI assistant

The MCP server at `/mcp` speaks Streamable HTTP. It takes the same `API_KEY` as the REST API, sent as a bearer token (`Authorization: Bearer <API_KEY>`), so any MCP-capable client that can send a header can use it. `X-API-Key` works on the REST API only. Claude Desktop setup is documented below; instructions for other clients (ChatGPT, Codex CLI, …) coming soon.

### Claude Desktop

Claude Desktop's native connectors want OAuth, so the key goes through `mcp-remote`. Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"mcpServers": {
  "tlg-ipam": {
    "command": "npx",
    "args": [
      "-y", "mcp-remote",
      "http://localhost:8080/mcp",
      "--header", "Authorization: Bearer ${TLG_IPAM_KEY}"
    ]
  }
}
```

with `TLG_IPAM_KEY` set in your environment. Then ask your assistant things like:

> *"What IPs are free in the services VLAN?"*
> *"Register the new PBS container at 10.10.20.45."*
> *"Allocate the next free IP for a new LXC called grafana-01."*
> *"What did you change yesterday?"*

## Scan agent

The scan agent is a small container you run on a Linux host on the VLAN you want swept. It checks in with the server every minute, and when a subnet assigned to it is due it pings every address in the subnet's scan scope, sends an ARP request to each (which finds hosts that drop ping, and their MACs), looks up reverse DNS names for the ones that answered, and reports them. It only ever connects out to the server; nothing listens on it. It never runs, downloads or installs anything the server sends.

This release does ICMP, ARP and reverse DNS. Reverse DNS asks the subnet's DNS servers, or, when the subnet names none, the ones in the agent host's `/etc/resolv.conf` (read again for every scan, so fixing the file needs no restart; it must be a regular file, or a link to one, of at most 64 KiB, readable within a second). With neither, the scan sends no DNS query, keeps the names already stored, and is marked incomplete in the subnet's Scanning section; the scan target's `last_scan_error` in the API says the agent has no DNS server to ask. ARP only works on the agent's own VLAN: the server sends it only for a subnet one of the agent's interfaces is on, and a subnet reached through a router gets ICMP and reverse DNS alone, with no MACs. Forward DNS checks (`fdns`) come in a later release.

### Run one

1. In the UI, open **Agents → Add agent** and name it. The dialog shows the agent's enrolment token (`tlge_…`) once; it can be exchanged once, within an hour.
2. On a Linux host on the target VLAN, run the agent in a container or as a systemd unit, as below, with that enrolment token. The agent exchanges it for its long-lived agent token (`tlga_…`) itself and keeps that.
3. Assign the subnet to the agent in the subnet's **Scanning** section, and save.

The dialog still shows the interim install from before the agent could enrol itself: a `curl` command that makes the exchange by hand and prints the agent token, and a container command that takes it as `IPAM_AGENT_TOKEN`. That works too, and needs no volume; a later release of the dialog shows the commands below.

A used, expired or revoked enrolment token is refused (`403`); so, for a minute, is every attempt after ten that failed (`429`). If the answer to the exchange is lost, or the token runs out before it is used, issue a new enrolment token for the same agent. Its id is in `GET /api/v1/agents`, and it keeps its scan targets:

```sh
curl -fsS -X POST https://ipam.home.arpa/api/v1/agents/<agent id>/re-enrol \
  -H 'X-API-Key: <owner key>'
```

The answer's `enrol_token` is the new one. Exchanging it also revokes the agent token the agent held before, which is the way to replace a lost or leaked one. (Deleting the agent and adding it again works too, but leaves its subnets unassigned.) The Agents page gains a Re-enrol button in a later release.

#### In a container

Rootful Docker or Podman:

```sh
docker run -d --name tlg-ipam-agent --restart unless-stopped \
  --network host --cap-add NET_RAW \
  -v tlg-ipam-agent:/var/lib/tlg-ipam-agent \
  -e IPAM_URL=https://ipam.home.arpa \
  -e IPAM_ENROL_TOKEN=tlge_… \
  ghcr.io/deusmaximus/tlg-ipam-agent:<version>
```

On its first start the agent exchanges the enrolment token and stores the agent token in the volume (`/var/lib/tlg-ipam-agent/token`, mode 0600). Every later start uses the stored token and ignores `IPAM_ENROL_TOKEN`, so the container can be recreated with the same command and volume, to update the image say, without a new token. Without the volume the token is lost when the container is recreated, and the agent says so in its log when it enrols. If the exchange is refused the log says `enrolment refused (HTTP 403 FORBIDDEN)` and the agent tries again every 15 minutes; it does not exit, so the restart policy does not hammer the server. To enrol a container again with a re-enrol token, remove the old token with its volume first (`docker rm -f tlg-ipam-agent && docker volume rm tlg-ipam-agent`), then run the command with the new token.

Host networking puts the agent on the VLAN itself, and `NET_RAW` lets it send ICMP echo requests and ARP requests. `--cap-add NET_RAW` adds that capability to Docker's default set rather than leaving the container with it alone; add `--cap-drop ALL` before it if you want only that one. Rootless container runtimes cannot grant a usable `NET_RAW` on the host network and are not supported. `docker logs tlg-ipam-agent` shows the enrolment, each check-in refusal, each job and how it ended.

The image is not published yet. Until it is, build it from this repository and tag it with the version in the command:

```sh
docker build -f agent/Dockerfile -t ghcr.io/deusmaximus/tlg-ipam-agent:<version> agent
```

#### Run it as a systemd unit

On an LXC or VM with systemd and Python 3.11 or later (with `venv`), as root:

```sh
sudo python3 -m venv /opt/tlg-ipam-agent
sudo /opt/tlg-ipam-agent/bin/pip install <the agent's wheel>
sudo /opt/tlg-ipam-agent/bin/tlg-ipam-agent install --url https://ipam.home.arpa --enrol-token -
```

`--enrol-token -` asks for the enrolment token, without echoing it (or reads it from a pipe: `echo tlge_… | sudo … --enrol-token -`). The token itself is never accepted on the command line: any local user can read a command line while it runs, and `sudo` logs it, which on a host with a persistent journal writes the token to disk. `--allowed-cidrs`, `--max-pps` and `--ca-file` set `ALLOWED_CIDRS`, `MAX_PPS` and `SSL_CERT_FILE` for the agent.

`install` creates the system user `tlg-ipam-agent`, exchanges the enrolment token, writes the agent token to `/var/lib/tlg-ipam-agent/token` (owned by that user, mode 0600: nobody else can read it), writes the settings to `/etc/tlg-ipam-agent/agent.env` (no secrets), and installs, enables and starts `tlg-ipam-agent.service`, which runs the agent as that user with `CAP_NET_RAW` as its only capability. The enrolment token is written nowhere. Before it imports anything but its own entry point, it checks that only root can change the code it is about to run: the interpreter and every directory and link on the way to it, and everything on Python's import path (the virtual environment, its dependencies and `.pth` files, the standard library). Anything else is refused, since it would run as root and the unit runs it with that capability. It also refuses a virtual environment the service user could not read (made under `umask 077`, say: `chmod -R o+rX /opt/tlg-ipam-agent` fixes it), and a `--ca-file` the service user could not read. Run it as `/opt/tlg-ipam-agent/bin/tlg-ipam-agent`, not `python -m` from a directory others can write, which Python would put on the import path. If the exchange is refused, it takes back what it created and says whether the token is spent. `journalctl -u tlg-ipam-agent.service` shows the agent.

To update, install the new wheel into the same environment and run `install` again without `--enrol-token`: it keeps the stored token (and makes sure the service user owns it). `sudo /opt/tlg-ipam-agent/bin/tlg-ipam-agent uninstall` stops the unit and removes it, both directories, and the service user if `install` created it (an account it found there is left; if removing the account fails, say because a process still runs as it, the record of it is kept and `uninstall` can simply be run again); then remove `/opt/tlg-ipam-agent` yourself, and revoke or delete the agent on the Agents page. To enrol a host again, uninstall and install with the new token.

The wheel is not published yet. Until it is, build it from this repository (`agent/README.md` says how) and give its path to `pip install`.

### Settings

| Variable | Default | |
|----------|---------|---|
| `IPAM_URL` | (required) | The server's URL. Must be `https://`: the token is a credential, and the server's certificate is always verified, with no setting to turn that off. Plain `http://` is accepted only for a server on the agent host's own loopback address (`http://127.0.0.1:8080`). |
| `IPAM_ENROL_TOKEN` | | The `tlge_…` enrolment token, for the container's first start. Ignored once an agent token is stored in `/var/lib/tlg-ipam-agent`. |
| `IPAM_AGENT_TOKEN` | | An agent token you hold (the interim install's `curl` printed it). Used as it is: nothing is read from or stored in `/var/lib/tlg-ipam-agent`. Set this or `IPAM_ENROL_TOKEN`, not both. |
| `ALLOWED_CIDRS` | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10` | The agent's own limit on what it will scan, whatever the server asks. A job for a subnet outside it is refused without a packet being sent, and the subnet's Scanning section shows why. Set it to your own networks if they use other ranges. |
| `MAX_PPS` | `200` | The most packets a second the agent sends (1–1000): echo requests, ARP requests and DNS queries together, each ARP retry and each failover to another DNS server counted, from one scan into the next. The agent hands each to the kernel at least a slot (`1/MAX_PPS`) after it handed over the one before, and a slot that comes late is not made up for. What the kernel and the network card do after that hand-off is outside the agent's control and can put two packets closer together on the wire. The host's kernel also sends ARP of its own to reach an address it pings on the agent's subnet (up to 3 requests for an address that does not answer); that is not counted. |

A server certificate from a private CA: mount the CA's bundle and point `SSL_CERT_FILE` at it, for example `-v /etc/ipam-ca.pem:/etc/ipam-ca.pem:ro -e SSL_CERT_FILE=/etc/ipam-ca.pem`. The bundle replaces the system's trusted CAs for the agent, so it has to contain the CA that signed the server's certificate. For the systemd unit, pass it to `install` as `--ca-file /etc/ipam-ca.pem`, in a place the `tlg-ipam-agent` user can read; the exchange `install` makes trusts it too, and nothing else. `install` refuses to run with `SSL_CERT_FILE` (other than the `--ca-file`) or `SSL_CERT_DIR` set in its own environment, since the unit would not have them; unset them and use `--ca-file`. A directory of CAs is not supported.

### What it does when the server says no

- **Rate limited (`429`) or unavailable (`503`, other 5xx), or no answer:** it tries again after 10 seconds, then waits up to twice as long each time, never more than 5 minutes, with random jitter.
- **Token refused (`401`/`403`), protocol mismatch (`409`), or a check-in the server could not read (`422`):** each needs you (a new token, re-enabling the agent, the agent release that matches the server), so it tries again every 15 minutes rather than hammering the server. The Agents page shows a protocol mismatch.
- A refused check-in never starts a scan. A scan that runs out of time (10 minutes) reports what it found as incomplete, which only adds to what the server knows.
- Every request to the server is abandoned after 30 seconds, however slowly the answer arrives, and is retried as above.

The log shows each refusal as its HTTP status and error code (`HTTP 409 PROTOCOL_MISMATCH`), never the server's message. For a check-in the server refused after accepting the token (a protocol mismatch, a report it could not read), the Agents page shows the full reason as the agent's last error. A refused token (`401`/`403`) and a refused result set leave no reason there: the status and code in the log are what there is. The agent's token is replaced with `tlga_[redacted]`, and the enrolment token with `tlge_[redacted]`, in anything it logs.

## Development

Backend (Python 3.12+):

```sh
cd api
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pytest                  # test suite
API_KEY=dev-key .venv/bin/uvicorn app.main:app --port 8080 --reload
```

Any non-empty key other than `change-me`, with no whitespace at either end, starts the server; without one, uvicorn exits before it serves anything (with `--reload`, the reloader waits for a file change; stop it with Ctrl+C). The test suite sets its own key.

Scan agent (Python 3.11+ to run, 3.12+ for its tests, which include the server):

```sh
cd agent
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pytest                  # sends no packet: fake prober, real routes in process
```

Frontend (Node 20+), with `/api` proxied to the backend on :8080:

```sh
cd web
npm install
npm run dev
```

### Allocation semantics

"Next free IP" never hands out the network or broadcast address, the subnet gateway, anything inside the DHCP pool (`dhcp_start`–`dhcp_end`), or anything in a reserved range — but manually registering a host inside those is deliberate and allowed. Duplicate IPs are blocked by a database unique constraint, not just API validation. Overlapping CIDRs are rejected within a section but allowed across sections (useful for VPN vs LAN views of the same space).

Hosts inherit their subnet's default domain suffix, with an optional per-host override; the API returns the computed FQDN either way.

## Licence

MIT — see [LICENSE](LICENSE).
