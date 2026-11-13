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
cp .env.example .env          # then set a strong API_KEY, e.g. openssl rand -hex 32
docker compose up --build
```

Open http://localhost:8080, paste your API key, done.

## Backups and restore

`GET /api/v1/backup` returns a consistent snapshot of the database (also the Settings → backup button). [docs/backup-and-restore.md](docs/backup-and-restore.md) sets up a scheduled pull of it from another host with dated files and retention, and walks through restoring from one. Try the restore once before you need it.

## One worker

The container runs a single uvicorn worker, and it should stay that way: do not add `--workers`, set `WEB_CONCURRENCY`, or run several replicas behind a load balancer. MCP sessions are held in the memory of the process that opened them, and SQLite allows one writer at a time, so a second process would not share them, and adds no capacity a homelab needs.

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

## Development

Backend (Python 3.12+):

```sh
cd api
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pytest                  # test suite
API_KEY=dev-key .venv/bin/uvicorn app.main:app --port 8080 --reload
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
