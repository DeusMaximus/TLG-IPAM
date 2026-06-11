# TLG-IPAM — Homelab-First IP Address Manager
### Project Brief & Build Specification · v1.1 · June 2026

---

## 1. Purpose & goals

TLG-IPAM is a clean, modern IP address manager designed for homelabs. It replaces phpIPAM without inheriting its complexity, and avoids Netbox's enterprise scope entirely. The product should feel fast, obvious, and pleasant to use — a tool you actually want to open.

**Design target:** a homelab operator with 4–12 VLANs, 50–300 hosts, and zero tolerance for configuration overhead. Every workflow should complete in ≤3 clicks from the dashboard.

**Non-goals — explicitly out of scope for v1:**
- BGP / MPLS routing tables
- Multi-user / RBAC (single-user, API-key auth only)
- Discovery agents or active network scanning
- Rack diagrams or physical layer mapping
- IPv6 (out of scope for v1, but all IP handling uses Python's `ipaddress` module and avoids `/24`-shaped assumptions, so IPv6 is a feature later — not a rewrite)

---

## 2. Architecture overview

### 2.1 Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + Vite + Tailwind CSS 4 |
| Backend API | Python 3.12 + FastAPI |
| Database | SQLite via SQLAlchemy 2 (async, `aiosqlite`) |
| MCP server | FastMCP 3.x — curated tools, mounted at `/mcp` in the same process |
| Container | Docker — **single container**: FastAPI serves the API, MCP, and the built SPA |
| Reverse proxy | Cloudflare Tunnel / Caddy compatible (`/mcp` path exposed) |

### 2.2 Container layout

One container. FastAPI serves the built Vite `dist/` as static files with an SPA fallback, so `/`, `/api`, and `/mcp` share a single origin — no nginx config, no CORS, one port to tunnel. SQLite lives in a named volume.

```yaml
services:
  ipam:
    build: .
    volumes: [ipam-data:/data]
    environment:
      - API_KEY=${API_KEY:?set API_KEY in .env}
      - DB_PATH=/data/ipam.db
    ports: ["8080:8080"]
volumes:
  ipam-data:
```

### 2.3 URL layout

| Path | Purpose |
|---|---|
| `/` | React SPA (static files + SPA fallback, served by FastAPI) |
| `/api/v1/...` | REST API |
| `/mcp` | Streamable HTTP MCP endpoint |
| `/api/v1/docs` | OpenAPI / Swagger (FastAPI auto-generated) |

---

## 3. Data model

### 3.1 Section

Top-level grouping. Maps to a network segment or physical location.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `name` | str — e.g. `Home LAN`, `Homelab`, `IoT`, `VPN` |
| `description` | str, nullable |
| `colour` | hex string for UI badge, nullable |

### 3.2 Subnet

A CIDR block, always belonging to one Section.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `section_id` | FK → Section |
| `cidr` | str — e.g. `10.10.30.0/24`. Validated on write. |
| `vlan_id` | int, nullable — VLAN tag number (1–4094) |
| `name` | str — friendly label |
| `domain` | str, nullable — default domain suffix for hosts in this subnet (e.g. `lab.home.arpa`) |
| `gateway` | str, nullable — IP within subnet |
| `dns_servers` | JSON array of IP strings, nullable |
| `dhcp_start` / `dhcp_end` | str, nullable — inclusive bounds of the DHCP pool. Both within the subnet; allocation skips this range. |
| `notes` | text, nullable |

**Overlap rule:** overlapping or duplicate CIDRs are allowed *across* sections (useful for VPN vs LAN views of the same space) but rejected *within* a section.

### 3.3 ReservedRange

A block of IPs within a subnet that automatic allocation skips — e.g. addresses held for network gear not yet installed. Manual host creation inside a reserved range is still allowed. A subnet can have any number of these.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet |
| `start_ip` / `end_ip` | str — inclusive bounds, both within the subnet CIDR, start ≤ end |
| `description` | str, nullable — e.g. `network gear` |

### 3.4 Host

A single IP assignment within a Subnet.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet |
| `ip` | str — must be within parent subnet CIDR |
| `hostname` | str, nullable |
| `domain` | str, nullable — per-host domain-suffix override; null inherits the subnet's `domain`. API responses include a computed `fqdn` (hostname + effective domain). |
| `mac` | str, nullable — stored normalised (lowercase colon-separated) |
| `device_type` | str — must match a DeviceType name (see 3.5); matched case-insensitively and stored canonically |
| `status` | enum: `active` \| `reserved` \| `dhcp` \| `offline` |
| `owner` | str, nullable — e.g. `proxmox`, `personal` |
| `notes` | text, nullable |
| `last_seen` | datetime, nullable — set manually or via future ping sweep |
| `created_at` / `updated_at` | datetime, auto-managed |

### 3.5 DeviceType

User-customisable device types (managed in Settings). Seeded on first boot with: `server`, `vm`, `lxc`, `network`, `iot`, `desktop`, `laptop`, `phone`, `other`.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `name` | str, unique — display name, e.g. `Raspberry Pi` |
| `icon` | str — identifier from the curated icon set (~28 Lucide icons) |

Rules: renaming a type cascades to all hosts using it (one transaction). A type in use cannot be deleted. `other` is the fallback default for new hosts — it cannot be renamed or deleted, but its icon can change.

### 3.6 Change (audit log)

Append-only record of every write. Because Claude can write to this system via MCP, you need to be able to review what it did. Also powers the dashboard "recent activity" feed.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `ts` | datetime, auto |
| `entity_type` | enum: `section` \| `subnet` \| `host` |
| `entity_id` | UUID of the affected record |
| `action` | enum: `create` \| `update` \| `delete` |
| `summary` | str — human-readable one-liner, e.g. `host pbs-01 created at 10.10.20.45` |
| `before` / `after` | JSON snapshots, nullable |
| `source` | enum: `ui` \| `api` \| `mcp` — derived from request path/client |

### 3.7 Validation constraints

- Subnet CIDR must be valid — reject on write, return 422 with message.
- Subnet CIDRs must not overlap within the same section (see 3.2).
- Host IP must fall within its subnet's CIDR — enforce at API layer.
- No duplicate IPs within a subnet — enforced by `UNIQUE(subnet_id, ip)` in the database, not just the API layer.
- `dhcp_start`/`dhcp_end` must both be set or both null, both within the CIDR, start ≤ end.
- Reserved ranges must be within the subnet CIDR, start ≤ end.
- Domain suffixes normalised to lowercase, leading/trailing dots stripped, charset-validated.
- MAC addresses normalised to lowercase with colons on write.
- VLAN IDs 1–4094 only.
- Deleting a section requires no child subnets. Deleting a subnet requires no hosts, unless `force=true` is passed (cascades to hosts and reserved ranges; the UI requires explicit confirmation).

---

## 4. REST API

### 4.1 Auth

Single static API key passed as a header:

```
X-API-Key: <value from API_KEY env var>
```

All endpoints except `GET /health` require this header. Return `401` if missing, `403` if wrong. Key is set at container start — no key rotation UI needed in v1.

### 4.2 Endpoints

| Route | Notes |
|---|---|
| `GET  /health` | No auth. Returns `{status: "ok", version}`. |
| `GET  /api/v1/sections` | List all sections with subnet count. |
| `POST /api/v1/sections` | Create section. |
| `PUT  /api/v1/sections/{id}` | Update. |
| `DELETE /api/v1/sections/{id}` | Delete only if no child subnets. |
| `GET  /api/v1/subnets` | List. Optional `?section_id=` filter. |
| `POST /api/v1/subnets` | Create. Validate CIDR + overlap rule. |
| `GET  /api/v1/subnets/{id}` | Detail including utilisation stats. |
| `PUT  /api/v1/subnets/{id}` | Update. |
| `DELETE /api/v1/subnets/{id}` | Delete only if no hosts; `?force=true` cascades, deleting all hosts and reserved ranges too. |
| `GET  /api/v1/subnets/{id}/free` | Return list of free IPs in the subnet (excludes reserved space, see 4.4). |
| `GET  /api/v1/hosts` | List. Filters: `?subnet_id=` `?status=` `?q=` (search hostname/ip/mac). |
| `POST /api/v1/hosts` | Create host. Validate IP in subnet. |
| `GET  /api/v1/hosts/{id}` | Detail. |
| `PUT  /api/v1/hosts/{id}` | Update. |
| `DELETE /api/v1/hosts/{id}` | Delete (frees the IP). |
| `POST /api/v1/hosts/bulk-delete` | Delete up to 1000 hosts by ID. Returns deleted count + not-found IDs. One audit entry per affected subnet, with before-snapshots. |
| `POST /api/v1/subnets/{id}/next-free` | Allocate next free IP — creates host record, returns it. |
| `POST /api/v1/subnets/{id}/hosts/bulk` | Bulk import (max 1000). Single transaction, per-record results (`created`/`skipped`/`error` + reason) — partial failures never abort the batch. One audit entry for the whole import. |
| `POST /api/v1/subnets/{id}/reserved` | Add a reserved range. Reserved ranges are embedded in subnet GET responses. |
| `DELETE /api/v1/subnets/{id}/reserved/{range_id}` | Remove a reserved range. |
| `GET  /api/v1/changes` | Recent changes, newest first. Filters: `?entity_type=` `?limit=`. |
| `GET/POST /api/v1/device-types` + `PUT/DELETE /api/v1/device-types/{id}` | Device type CRUD with host counts. Rename cascades to hosts; delete refused while in use or for `other`. |
| `GET  /api/v1/backup` | Download the SQLite database file (powers the Settings backup button). |

### 4.3 Standard response shapes

```json
// Success — 200 / 201
{ "data": { ... } }             // single object
{ "data": [ ... ], "total": N } // collection

// Error — 4xx / 5xx
{ "error": { "code": "VALIDATION_ERROR", "message": "IP 10.10.30.300 is invalid" } }
```

### 4.4 Free-IP and allocation semantics

An IP is **free** iff all of the following hold:
- within the subnet's usable range — network and broadcast addresses are never free (special case: /31 and /32 have no network/broadcast exclusion)
- not the subnet's gateway
- not within the DHCP pool (`dhcp_start`–`dhcp_end`), if one is set
- not within any reserved range
- no host record exists for it

Manual host creation (`POST /hosts`) only rejects the network/broadcast addresses — placing a host at the gateway, inside the DHCP pool, or inside a reserved range is deliberate and allowed.

`next-free` allocates the lowest free IP and creates the host record in a single transaction. The `UNIQUE(subnet_id, ip)` constraint is the backstop against races.

---

## 5. MCP server

### 5.1 Overview

The MCP server runs in the same FastAPI process, mounted at `/mcp` (Streamable HTTP transport), implemented with **FastMCP 3.x**.

Tools are **hand-curated, not auto-generated** from the OpenAPI schema. Auto-generated REST-shaped tools are worse for Claude: generic names, HTTP-flavoured parameters, paginated CRUD responses instead of answers. Since Claude Desktop usage is the headline feature, the tools are first-class citizens.

**No sync risk:** REST routers and MCP tools are both thin wrappers over the same `services/` layer. Business logic (validation, allocation, audit logging) lives in services and is written exactly once.

```python
# mcp_server.py
from fastmcp import FastMCP
mcp = FastMCP("TLG-IPAM")

@mcp.tool
async def get_free_ips(subnet_id: str, limit: int = 20) -> dict:
    """Return unallocated IP addresses in a subnet. ..."""
    ...calls services.subnets.free_ips(...)

# main.py
mcp_app = mcp.http_app(path="/")
app = FastAPI(lifespan=mcp_app.lifespan, ...)
app.mount("/mcp", mcp_app)
```

Auth for `/mcp` uses the same `X-API-Key` header, enforced by ASGI middleware on the mount path.

### 5.2 MCP tool inventory

Tool descriptions are the documentation Claude reads at runtime — write them well. Tools may return richer, denormalised answers than the REST API where it saves Claude round-trips.

| Tool | Description / behaviour |
|---|---|
| `list_sections` | List all network sections/segments. Use to get an overview of how the network is organised before querying subnets. |
| `list_device_types` | List defined device types with usage counts — the valid `device_type` values for host writes. |
| `list_subnets` | List subnets, optionally filtered by section. Returns CIDR, VLAN ID, name, and utilisation (used/total) in one call. |
| `get_subnet` | Full detail for a subnet: VLAN tag, gateway, DNS servers, DHCP pool, utilisation, and free-IP count — one call, no follow-ups needed. |
| `get_free_ips` | Return unallocated IPs in a subnet (respects gateway/DHCP-pool/broadcast exclusions). Use before allocating to check availability or find a specific address. |
| `list_hosts` | Search and filter host records across the network. Free-text search (hostname, IP, MAC) plus filtering by subnet or status. |
| `get_host` | Full detail for a specific host record including MAC, device type, owner, and last seen. |
| `create_host` | Register a new host at a specific IP address. Validates that the IP falls within the target subnet, is not reserved, and is not already allocated. |
| `bulk_create_hosts` | Create many hosts in one subnet in a single call (e.g. importing devices from a UniFi export). Per-record results; duplicates skipped, bad records errored, batch never aborts. One audit entry. |
| `allocate_next_ip` | Allocate the next available IP in a subnet and create a host record in one step. Use when the caller does not need a specific IP address. |
| `update_host` | Update hostname, domain override, MAC address, status, device type, owner, or notes for an existing host record. |
| `delete_host` | Remove a host record, freeing the IP address for future allocation. |
| `bulk_delete_hosts` | Delete many hosts by ID in one call (cleanup workflows). Returns deleted count + not-found IDs. |
| `add_reserved_range` | Reserve a block of IPs in a subnet so automatic allocation skips it (manual creation inside it stays allowed). |
| `delete_reserved_range` | Remove a reserved range, returning its addresses to the allocation pool. |
| `list_changes` | Recent changes to the IPAM (audit log), newest first. Use to answer "what changed recently?" or to review previous MCP-driven edits. |

All MCP writes are recorded in the change log with `source = mcp`.

### 5.3 Claude Desktop config

Claude Desktop's native remote connectors require OAuth or no auth, so the API-key path goes through `mcp-remote`. Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"mcpServers": {
  "tlg-ipam": {
    "command": "npx",
    "args": [
      "-y", "mcp-remote",
      "https://ipam.example.com/mcp",
      "--header", "X-API-Key:${TLG_IPAM_KEY}"
    ]
  }
}
```

Set `TLG_IPAM_KEY` in your shell environment (e.g. `~/.zshenv`). The `mcp-remote` package handles Streamable HTTP → stdio translation for Claude Desktop.

### 5.4 Example interactions

These should work once Claude Desktop has the server connected:

- "What IPs are free in the homelab VLAN?" → `list_subnets` → `get_free_ips`
- "Register the new PBS container at 10.10.20.45, VLAN 20" → `create_host`
- "What device is at 10.10.10.1?" → `list_hosts` with `q=10.10.10.1`
- "Allocate the next free IP in the services subnet for a new LXC" → `allocate_next_ip`
- "Show me all offline hosts across the whole network" → `list_hosts` with `status=offline`
- "Reserve 10.10.30.2 through .19 for switches" → `add_reserved_range`
- "Here's my UniFi client export — register everything in the IoT subnet" → `bulk_create_hosts`
- "What did you change yesterday?" → `list_changes`

The server's `instructions` string tells clients this is the authoritative standalone IPAM (not phpIPAM/NetBox/UniFi), so Claude routes IP questions here instead of to networking-adjacent skills.

---

## 6. Frontend specification

### 6.1 Design direction

Clean, data-dense, dark-mode-first. Reference aesthetic: linear.app sidebar density meets Tailwind UI components. Not a dashboard with big KPI cards — a tool with a sidebar tree and a content area. The UI should feel like it was built by someone who uses the product.

| Element | Spec |
|---|---|
| Colour scheme | Dark mode default. `slate-900` background, `slate-800` cards, `blue-500` accent. |
| Typography | Inter via Google Fonts. 13–14px base, 12px in tables. |
| Sidebar | Section tree with collapsible subnets. Utilisation bar per subnet (used/total). |
| Subnet utilisation | Inline pill: e.g. `12/254` with a thin coloured bar. Green <70%, amber 70–90%, red >90%. |
| Host table | Virtualised if >200 rows. Columns: checkbox multi-select (with select-all), IP (sortable), Hostname, Device type icon, Status badge, MAC, Last seen. Selection enables "Delete N selected" with confirm. |
| Status badges | `active`=green, `reserved`=amber, `dhcp`=blue, `offline`=slate. |
| Device type icons | Per-type icon chosen from a curated Lucide set (~28 icons), user-assignable in Settings. |
| Empty states | Helpful, not apologetic. e.g. "No hosts yet — add one or allocate the next free IP." |
| Inline editing | Click any host row to open a slide-over drawer. No separate edit page. |

### 6.2 Views

- **Dashboard** — summary stats: total subnets, total hosts, hosts by status, plus a **recent activity feed** driven by the change log (shows source: UI / API / MCP)
- **Section view** — click section in sidebar; shows its subnets with utilisation
- **Subnet view** — click subnet; shows host table + free IP count + VLAN/gateway/domain/DHCP-pool info header, with reserved ranges shown as removable chips + a "Reserve range" action
- **Host drawer** — slide-over on row click; all fields editable (domain field shows the inherited subnet default as placeholder; blank = inherit); delete button with confirmation
- **Allocate modal** — triggered by "Allocate next free IP" button; prefills next IP; hostname required
- **CSV import modal** — on the subnet view; file picker or paste, header-mapped columns, per-row results shown after import (same bulk endpoint as MCP)
- **Settings** — API key display (masked), device type management (add/rename/re-icon/delete with a curated icon picker), SQLite backup download, app version

No separate login page — access control is handled at the reverse proxy / Cloudflare Access layer.

---

## 7. Project structure

```
tlg-ipam/
├── docker-compose.yml
├── Dockerfile               # multi-stage: node builds web/dist, python runs API + serves it
├── api/
│   ├── requirements.txt
│   ├── tests/                # pytest — validation + allocation logic
│   └── app/
│       ├── main.py           # FastAPI app + static SPA serving + MCP mount
│       ├── config.py         # env vars (pydantic-settings)
│       ├── database.py       # SQLAlchemy async engine + session
│       ├── models.py         # ORM models (Section, Subnet, Host, Change)
│       ├── schemas.py        # Pydantic request/response schemas
│       ├── auth.py           # API key dependency + /mcp ASGI middleware
│       ├── mcp_server.py     # FastMCP tools (thin wrappers over services)
│       ├── services/         # business logic — single source of truth
│       │   ├── sections.py
│       │   ├── subnets.py    # incl. free-IP + next-free allocation logic
│       │   ├── hosts.py
│       │   └── changes.py    # audit log writes + reads
│       └── routers/
│           ├── sections.py
│           ├── subnets.py
│           ├── hosts.py
│           └── changes.py
└── web/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── api/             # fetch wrappers per resource
        ├── components/      # Sidebar, HostTable, HostDrawer, AllocateModal, etc.
        └── pages/           # Dashboard, SectionView, SubnetView, Settings
```

---

## 8. Recommended build order

Follow this sequence to stay unblocked. Each step is independently runnable and testable.

| Step | Task |
|---|---|
| 1 | Scaffold repo: `docker-compose.yml`, `Dockerfile`, `requirements.txt`, `package.json` |
| 2 | API: `database.py`, `models.py`, `config.py` — tables created on startup (`create_all`); adopt Alembic once the schema stabilises post-v1 |
| 3 | API: `schemas.py` — all Pydantic request/response models |
| 4 | API: `auth.py` dependency + health endpoint. Verify API key auth works end-to-end. |
| 5 | API: services + routers — sections (full CRUD, change-logged) |
| 6 | API: services + routers — subnets: CRUD + `/free` + `next-free` allocation logic incl. reserved-space exclusions |
| 7 | API: services + routers — hosts: CRUD + search (`?q=`); changes router |
| 8 | Tests: pytest for validation + allocation edge cases (gateway/DHCP-pool skipping, /31, duplicate IPs, overlap rule) |
| 9 | MCP: `mcp_server.py` — all 11 tools wrapping services, mounted at `/mcp` with auth |
| 10 | **Test MCP with `mcp-remote` locally before touching the frontend** |
| 11 | Frontend: scaffold Vite + Tailwind + React Router, `api/` fetch layer |
| 12 | Frontend: Sidebar with section/subnet tree |
| 13 | Frontend: Subnet view + host table |
| 14 | Frontend: Host drawer (view + edit + delete) |
| 15 | Frontend: Allocate modal + Dashboard (stats + activity feed) |
| 16 | Polish: empty states, error toasts, loading skeletons, dark mode QA |

---

## 9. Key decisions & rationale

| Decision | Rationale |
|---|---|
| SQLite not Postgres | Zero ops overhead. A homelab IPAM will never have concurrent write contention. Backup = copy one file. |
| FastAPI not Flask | Native async, automatic OpenAPI schema generation, Pydantic validation built in. |
| FastMCP with curated tools, not OpenAPI auto-generation | Hand-written tools have better names, parameters, and denormalised answers — materially better Claude ergonomics. Shared `services/` layer eliminates the drift risk that auto-generation was meant to solve. FastMCP is the actively-maintained de facto standard. |
| Streamable HTTP not stdio | Works with mcp-remote for Claude Desktop. Also compatible with any future MCP client without modification. |
| Single container, FastAPI serves the SPA | One origin (no CORS), no nginx config, one port to tunnel, `docker compose up` is the whole story. |
| Audit log from day one | Claude writes to this system; you need to be able to review what it did. Also free "recent activity" UI. |
| React + Vite not Next.js | No SSR needed. Vite builds faster, static output served straight from FastAPI. |
| Single API key not JWT | Homelab single-user context. Adding OAuth/OIDC later is a one-file change in `auth.py`. |
| No discovery/scanning in v1 | Keeps scope sane. Manual + MCP-driven allocation is the primary workflow. Scanning can be added later. |

---

## 10. Acceptance criteria

### Core functionality
- Can create sections, subnets (with VLAN tag, gateway, DNS, DHCP pool), and hosts via both UI and API.
- IP validation rejects out-of-subnet addresses with a clear error message.
- "Allocate next free IP" finds the next unallocated address — skipping network, broadcast, gateway, DHCP pool, and reserved ranges — and creates the host record atomically.
- Hosts inherit the subnet's default domain suffix; a per-host override wins, and clearing the override re-inherits. API responses expose the computed FQDN.
- Reserved ranges block automatic allocation but not manual host creation.
- Overlapping CIDRs rejected within a section, allowed across sections.
- Search returns results across hostname, IP, and MAC.
- Subnet utilisation is accurate (allocated / total usable addresses).
- Every write (UI, API, or MCP) appears in the change log with the correct source.

### Testing
- pytest suite covers CIDR/IP validation, the overlap rule, and next-free allocation edge cases (gateway/DHCP skipping, /31, full subnet, duplicate IP rejection).

### MCP integration
- Claude Desktop connects via `mcp-remote` and lists all available tools.
- All 16 MCP tools are callable from Claude Desktop.
- Bulk import reports per-record outcomes; a batch with some bad rows still imports the good ones.
- Natural-language queries resolve to correct tool calls without ambiguity.

### Operations
- `docker compose up --build` produces a running stack with no manual steps.
- All data persists across container restarts (named volume).
- API key changeable via env var without rebuild.
- Settings page exposes SQLite backup download.

### UI quality
- Dark mode correct — no hardcoded colours bleeding through.
- Works at 1280px and 1920px wide.
- Empty states present on all list views.
- No console errors in production build.

---

*TLG-IPAM Project Brief · Built for Claude Fable · tlgnet.org*
