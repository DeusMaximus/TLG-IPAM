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
| MCP server | FastMCP 4.x (locked at 4.1.8, MCP SDK 2.3.0) — curated tools, mounted at `/mcp` in the same process |
| Container | Docker — **single container**: FastAPI serves the API, MCP, and the built SPA |
| Reverse proxy | Cloudflare Tunnel / Caddy compatible (`/mcp` path exposed) |

### 2.2 Container layout

One container. FastAPI serves the built Vite `dist/` as static files with an SPA fallback, so `/`, `/api`, and `/mcp` share a single origin — no nginx config, no CORS, one port to tunnel. SQLite lives in a named volume.

**One uvicorn worker, by design.** The image starts uvicorn with `--workers 1` in its `CMD`, so the single process does not depend on the environment: an explicit `--workers` takes precedence over `WEB_CONCURRENCY` (measured, #43), and the variable no longer changes the count. It must stay that way: no other `--workers`, no replicas behind a load balancer. The MCP server runs stateful Streamable HTTP, so a session's state lives in the memory of the process that opened it, and a request carrying that session id that reached another process would not find it. SQLite is a single file with one writer at a time; one process is what the `busy_timeout` queueing in §9 is sized for. Startup (create tables, add missing columns, seed device types) runs once per process with no coordination between processes: two workers starting on a fresh database sometimes fail to come up with `database is locked`. Scale-out is not a goal for a homelab tool.

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
| `/api/v1/...` | REST API (owner key) |
| `/api/v1/agent/...` | Agent-facing REST API (agent token; §4.1): check-in (§4.5). Results ingest arrives with v0.2 M2a (#11) |
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
| `scan_excluded` | bool, nullable — `true` takes the range out of its subnet's scan scope (§3.10). Null or `false` means scanned, and responses return `false` for null. Ranges from before the column existed gain it, null, on startup, so they read as scanned. Set on create over REST, or later with `PATCH`; never over MCP |

### 3.4 Host

A single IP assignment within a Subnet.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet |
| `ip` | str — must be within parent subnet CIDR |
| `ip_int` | int — integer form of `ip`, kept for range queries and returned in API responses as the numeric sort key so clients never parse address strings |
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
| `entity_type` | enum: `section` \| `subnet` \| `host` \| `device_type` \| `agent` \| `token` \| `scan_target`. Reserved-range writes, a range's scan exclusion included, are logged as `subnet` updates |
| `entity_id` | UUID of the affected record |
| `action` | enum: `create` \| `update` \| `delete` \| `revoke` (`revoke` is for tokens only) |
| `summary` | str — human-readable one-liner, e.g. `host pbs-01 created at 10.10.20.45` |
| `before` / `after` | JSON snapshots, nullable |
| `source` | enum: `ui` \| `api` \| `mcp` \| `agent` — MCP and `agent` are server-assigned; REST uses the advisory `X-TLG-Client: ui` hint, so `ui` versus `api` is a UX label rather than trusted provenance. `agent` is a check-in that changed what the agent reports (§4.5); each agent keeps its newest 100 of those, and over MCP the reported values in `agent` snapshots are withheld. Not logged: heartbeats (`tokens.last_used_at`, `agents.last_checkin_at`) and job scheduling (the lease columns) |
| `actor` | str(80), nullable — who acted, when that is narrower than `source`. Null is the owner key, whether over REST or MCP. An agent's own write records `agent:<agent name>`; `grant:<client name>` arrives with OAuth (v0.2 spec §4.3). It is a field of its own and never part of `summary`. Databases created before it gain the column, null in existing rows, on startup. |

### 3.7 Validation constraints

- Subnet CIDR must be valid — reject on write, return 422 with message.
- Subnet CIDRs must not overlap within the same section (see 3.2).
- Host IP must fall within its subnet's CIDR — enforce at API layer.
- No duplicate IPs within a subnet — enforced by `UNIQUE(subnet_id, ip)` in the database, not just the API layer.
- `dhcp_start`/`dhcp_end` must both be set or both null, both within the CIDR, start ≤ end.
- Reserved ranges must be within the subnet CIDR, start ≤ end.
- Changing a subnet CIDR must preserve every child invariant: existing hosts must remain assignable (including network/broadcast rules), and reserved ranges must remain within the new CIDR.
- Update requests may omit fields they do not change. Explicit `null` is accepted only for nullable fields; required fields return `422 VALIDATION_ERROR` rather than reaching a database constraint.
- Domain suffixes normalised to lowercase, leading/trailing dots stripped, charset-validated.
- MAC addresses normalised to lowercase with colons on write.
- VLAN IDs 1–4094 only.
- Deleting a section requires no child subnets. Deleting a subnet requires no hosts, unless `force=true` is passed (cascades to hosts and reserved ranges; the UI requires explicit confirmation). Either way the subnet's scan target is deleted with it, with its own change-log entry.
- A scan target needs a subnet of `/20` or a longer prefix, and its subnet's CIDR cannot then be changed to a shorter one.

### 3.8 Agent

A scan agent (v0.2 spec §5.4). The owner creates it over REST; the agent checks in over `/api/v1/agent/checkin` (§4.5). Results ingest and the Agents page arrive with v0.2 M2a.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `name` | str. 1–74 printable characters, trimmed: `agent:<name>` is the audit `actor` for the agent's own writes and must fit `changes.actor` (80). Unique case-insensitively (`409`) |
| `name_key` | str, unique — `name.casefold()`, so the database itself refuses two names that differ only in case, even from racing requests. Internal: not returned, not in audit snapshots |
| `state` | enum: `pending` (has never held an agent token; enrolment, v0.2 M2b) \| `active` \| `disabled`. An agent created over REST gets its token at once, so it starts `active` |
| `version`, `platform`, `hostname`, `interfaces` | nullable; reported by the agent at check-in, and null until its first. Trimmed, printable characters only, at most 40, 120 and 253 characters; `interfaces` is up to 64 `{name, cidr, mac}` (name at most 15 characters; `cidr` an address with its prefix length, normalised, no IPv6 scope id; `mac` normalised or null). A check-in that fails these checks changes none of them |
| `last_checkin_at` | datetime, nullable — set by every authenticated check-in, refused ones included |
| `current_job_id`, `job_issued_at` | nullable, read-only — the job the agent was last issued, and when (§4.5). The agent is busy while that lease is live, whatever has since happened to the target. Databases created before them gain the columns on startup |
| `last_error` | text, nullable — why the last check-in was refused (protocol mismatch, or the first field that failed validation, by name and never by value). Cleared by a check-in that succeeds |
| `created_at` | datetime, auto |

Rules: disabling an agent refuses its token without revoking it, and enabling it accepts the token again. Deleting an agent revokes its unrevoked tokens (each logged as a `token` `revoke`), sets `agent_id` to null on its scan targets (each logged as a `scan_target` update; their settings are kept), then deletes the agent and its token rows.

### 3.9 Token

A credential bound to an agent (v0.2 spec §4.2). An agent token is `tlga_` followed by 43 URL-safe random characters (256 bits).

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `kind` | enum: `agent` \| `enrol`. The `enrol` kind exists in the schema; nothing issues one until v0.2 M2b |
| `agent_id` | FK → Agent |
| `token_hash` | SHA-256 of the token, hex, unique. Never returned and never in an audit snapshot |
| `token_prefix` | the first 13 characters (`tlga_` + 8), for display |
| `created_at`, `last_used_at` | `last_used_at` is set when the token authenticates and the stored time is at least 10 seconds old (`agents.LAST_USED_GRANULARITY`), so a token used at request rate drives no writes |
| `expires_at`, `used_at` | enrolment tokens only; null for agent tokens |
| `revoked_at` | datetime, nullable |

Rules: the plain token appears in exactly one response, the one that creates it; it cannot be recovered afterwards. An agent holds at most one unrevoked `agent` token, enforced by a partial unique index. Token create and revoke are audit-logged with `entity_type="token"`; agent and token writes are owner writes, so `actor` is null.

### 3.10 ScanTarget and scan scope

A subnet the owner has chosen to scan, and how (v0.2 spec §5.3, §5.4). At most one per subnet. Configured over REST only; no MCP tool reads or writes it. Check-in hands out jobs for it (§4.5); results ingest arrives with v0.2 M2a (#11).

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet, unique. Fixed once created |
| `agent_id` | FK → Agent, nullable. Null = unassigned: nothing is scanned until an agent is chosen |
| `enabled` | bool, default `true` |
| `methods` | JSON list — a subset of `icmp`, `arp`, `rdns`, `fdns` that includes `icmp` or `arp` (the liveness methods). Stored de-duplicated in that order. Default `["icmp", "arp", "rdns"]` |
| `scan_dhcp_pool` | bool, default `false` — whether the subnet's DHCP pool is in scope |
| `interval_minutes` | int, nullable — 5 to 10080 (a week); null = manual only |
| `scan_requested_at` | datetime, nullable, read-only — when a scan was last requested (`POST /subnets/{id}/scan`). Never cleared: it also times the cooldown |
| `current_job_id`, `job_issued_at`, `job_methods` | nullable, read-only — the job lease, set when check-in issues a job (§4.5); `job_methods` is what the job was issued with. Databases created before `job_methods` gain it on startup |
| `last_scan_at`, `last_scan_duration_ms`, `last_scan_complete` | nullable, read-only — the last-scan record. `last_scan_at` is when the last completed job was issued, by the server's clock. Written by results ingest (#11); nothing writes them yet |
| `created_at` | datetime, auto |

**Scan scope** is the set of addresses a scan may send packets to. It is computed in one place, `services/scans.scan_scope(subnet)`, as inclusive address ranges: the subnet's usable addresses (no network or broadcast for `/30` and shorter), minus the DHCP pool unless `scan_dhcp_pool` is set, minus every reserved range with `scan_excluded`. Exclusion wins: an address in both an excluded and a scanned reserved range is excluded. A subnet without a scan target has the default scope (pool not scanned). Scan-target responses carry it as `scope: [{start, end}]`.

Rules: one target per subnet (`409`, also enforced by `UNIQUE(subnet_id)`). Unknown subnet or agent: `404`. A method outside the four, or a set without `icmp` or `arp`: `422`. Every create, update and delete is audit-logged as `scan_target`; an update that changes nothing writes no entry.

---

## 4. REST API

### 4.1 Auth

Single static API key passed as a header:

```
X-API-Key: <value from API_KEY env var>
```

All endpoints except `GET /health` and the agent-facing `/api/v1/agent/*` require this header. Return `401` if missing, `403` if wrong. The key is compared in constant time (`hmac.compare_digest`, in `auth.is_owner_key`). Key is set at container start — no key rotation UI needed in v1. The server refuses to start unless `API_KEY` is set, non-empty (not only whitespace), free of whitespace at either end and not `change-me`, the placeholder `.env.example` ships: `config.load_settings` runs when `app.config` is imported, so uvicorn exits with a message that names `API_KEY` and says how to generate a key, before it serves anything (under `--reload`, the reloader stays up and its server process exits). A key with a leading or trailing space, tab or newline is refused, not trimmed: most HTTP clients cannot send such a header value, a raw one can, and trimming would leave two candidate keys. The message is fixed and never contains the key. Separately, `is_owner_key` never matches an empty value, so an empty `X-API-Key` is refused (`403`) whatever the setting holds. REST does not accept the key as a bearer, and `/mcp` does not accept `X-API-Key` (§5.1).

Agent-facing routes, `/api/v1/agent/*`, take an agent token instead (§3.9), as `Authorization: Bearer tlga_…`, through the `auth.require_agent` dependency and nothing else. It accepts only an unrevoked `agent` token of an `active` agent and records `last_used_at`, at most once every 10 seconds per token. The token is found by the SHA-256 of the presented value, so the stored secret is never compared. Refusals use the REST error shape: `401 UNAUTHORIZED` when no bearer is sent (including `X-API-Key` alone), `403 FORBIDDEN` "Invalid agent token" for any bearer that is not a usable agent token — the owner key, an unknown, revoked or `enrol` token, or the token of a disabled or deleted agent. The owner routes refuse an agent token (`403` as `X-API-Key`, `401` as a bearer), and so does `/mcp` (`401 invalid_token`). The router for `/api/v1/agent/*` carries `require_agent` itself, so a route added to it is guarded too, and it is mounted apart from the owner routers, so the owner key never reaches it. The one route today is check-in (§4.5); results ingest arrives with v0.2 M2a (#11).

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
| `DELETE /api/v1/subnets/{id}` | Delete only if no hosts; `?force=true` cascades, deleting all hosts and reserved ranges too. Its scan target is deleted either way. |
| `GET  /api/v1/subnets/{id}/free` | Return list of free IPs in the subnet (excludes reserved space, see 4.4). |
| `GET  /api/v1/hosts` | List. Filters: `?subnet_id=` `?status=` `?q=` (search hostname/ip/mac). |
| `POST /api/v1/hosts` | Create host. Validate IP in subnet. |
| `GET  /api/v1/hosts/{id}` | Detail. |
| `PUT  /api/v1/hosts/{id}` | Update. |
| `DELETE /api/v1/hosts/{id}` | Delete (frees the IP). |
| `POST /api/v1/hosts/bulk-delete` | Delete up to 1000 hosts by ID. Returns deleted count + not-found IDs. One audit entry per affected subnet, with before-snapshots. |
| `POST /api/v1/subnets/{id}/next-free` | Allocate next free IP — creates host record, returns it. |
| `POST /api/v1/subnets/{id}/hosts/bulk` | Bulk import (max 1000). Single transaction, per-record results (`created`/`skipped`/`error` + reason) — partial failures never abort the batch. One audit entry for the whole import. |
| `POST /api/v1/subnets/{id}/reserved` | Add a reserved range (`{start_ip, end_ip, description?, scan_excluded?}`; `scan_excluded` defaults to `false`). Reserved ranges are embedded in subnet GET responses, each with `scan_excluded`; subnet responses also carry `scan_dhcp_pool` from the subnet's scan target, or null when it has none. |
| `PATCH /api/v1/subnets/{id}/reserved/{range_id}` | Set `{scan_excluded}` (required, not null). A request that changes nothing writes no change-log entry. |
| `DELETE /api/v1/subnets/{id}/reserved/{range_id}` | Remove a reserved range. |
| `POST /api/v1/subnets/{id}/scan` | Request a scan ("Scan now"): sets the scan target's `scan_requested_at`, logged as a `scan_target` update. `202` with the target. The agent picks it up at its next check-in. At most one request per subnet in 15 minutes, from any caller: a second is `429 COOLDOWN`, changes nothing and does not restart the wait. No scan target, a disabled one, or one with no agent: `409`. |
| `GET  /api/v1/changes` | Recent changes, newest first. Filters: `?entity_type=` `?limit=`. |
| `GET  /api/v1/agents` | List agents, by name, each with its tokens (prefix and timestamps only, newest first). |
| `POST /api/v1/agents` | Create an agent (`{name}`) with its agent token. `201`; the response carries the plain token as `data.token`, the only response that ever does. Duplicate name (case-insensitive): `409`. |
| `GET  /api/v1/agents/{id}` | Detail, with its tokens. |
| `PUT  /api/v1/agents/{id}` | Update `name` and/or `enabled` (`false` → `disabled`; `true` → `active`, or `pending` for an agent that has never held an agent token). A request that changes nothing writes no change-log entry. |
| `POST /api/v1/agents/{id}/revoke` | Revoke every unrevoked token of the agent; the agent is kept. `409` if it has none. |
| `DELETE /api/v1/agents/{id}` | Revoke its tokens, unassign its scan targets, then delete the agent and its tokens. |
| `GET  /api/v1/scan-targets` | List scan targets, oldest first, each with its `scope` (§3.10). |
| `POST /api/v1/scan-targets` | Create (`{subnet_id, agent_id?, enabled?, methods?, scan_dhcp_pool?, interval_minutes?}`). `201`. Subnet shorter than `/20`, bad methods or interval: `422`; subnet already has one: `409`. |
| `GET  /api/v1/scan-targets/{id}` | Detail, with `scope`. |
| `PUT  /api/v1/scan-targets/{id}` | Update any of `agent_id` (null unassigns), `enabled`, `methods`, `scan_dhcp_pool`, `interval_minutes` (null = manual only). |
| `DELETE /api/v1/scan-targets/{id}` | Delete the target; the subnet is kept. |
| `GET/POST /api/v1/device-types` + `PUT/DELETE /api/v1/device-types/{id}` | Device type CRUD with host counts. Rename cascades to hosts; delete refused while in use or for `other`. |
| `GET  /api/v1/backup` | Download a consistent temporary snapshot made with SQLite's online backup API (powers the Settings backup button). `docs/backup-and-restore.md` documents the scheduled pull from another host and the restore. |

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

`next-free` allocates the lowest free IP and creates the host record in a single transaction. The `UNIQUE(subnet_id, ip)` constraint is the backstop against races: if a concurrent writer claims the chosen IP first, allocation re-reads the free list and rolls to the next candidate (up to 5 attempts) rather than surfacing a conflict.

Gateway, DHCP, reserved-range, and allocated-address exclusions are represented as clipped, merged integer intervals. Utilisation counts interval lengths, and free-IP iteration skips blocked spans rather than materialising every address in a range.

### 4.5 Agent check-in and jobs

`POST /api/v1/agent/checkin` (agent token, §4.1). The request and response shapes live in `api/app/agent_protocol.py`, the contract the agent package is built against (v0.2 spec §5.7). It depends on the standard library and pydantic only.

```json
// request
{ "version": "0.2.0", "protocol": 1, "hostname": "scanner-20", "platform": "linux",
  "interfaces": [{ "name": "eth0", "cidr": "10.0.20.5/24", "mac": "02:00:00:00:00:05" }],
  "capabilities": [] }
// 200
{ "data": { "checkin_interval_s": 60,
            "jobs": [{ "job_id": "…", "subnet_id": "…", "cidr": "10.0.20.0/24",
                       "ranges": [{ "start": "10.0.20.1", "end": "10.0.20.99" }],
                       "methods": ["icmp", "arp", "rdns"], "dns_servers": ["10.0.20.1"],
                       "timeout_s": 600, "fdns": [] }] } }
```

- **Rate limit first.** An agent may check in once every 10 seconds (`jobs.MIN_CHECKIN_GAP`; its own interval is 60). A check-in inside that gap is `429 RATE_LIMITED`, written as a compare-and-set on `last_checkin_at`, before any other write and before the body is looked at: it changes nothing and is not recorded. Inside the gap it is refused from the agent row loaded at authentication, so with `last_used_at`'s 10-second granularity (§3.9) a refused check-in issues no write statement at all; the compare-and-set catches check-ins that race. The agent waits `checkin_interval_s` and tries again.
- **Protocol first.** `protocol` is checked before anything else in the body, since another protocol may shape the rest differently. Anything but the integer `PROTOCOL_VERSION` (1) is `409 PROTOCOL_MISMATCH` with a message naming both protocols; it issues no job, stores none of the body, and is recorded in the agent's `last_error`.
- **Then the fields** (§3.8). The first that fails is `422 VALIDATION_ERROR` "Check-in refused: <field>: <reason>", recorded in `last_error` the same way; the reported fields keep their earlier values. Unknown fields are ignored. `capabilities` (up to 16 tokens of `a-z0-9_-`) is checked and not yet used.
- **A good check-in** stores the reported fields and `last_checkin_at`, clears `last_error`, and returns at most one job.
- **Audit.** A check-in that changes `version`, `platform`, `hostname`, `interfaces` or `last_error` writes one `agent` update with `source="agent"` and `actor="agent:<name>"`, naming the changed fields in the summary and their values only in the snapshots. A check-in that changes nothing else is not logged (as `last_used_at` is not), and neither is issuing a job. An agent keeps only its newest 100 entries of its own (`source="agent"`); older ones are deleted when a new one is written, and the owner's entries are never touched. Together with the rate limit, that bounds what one agent token can add to the log.
- **Agent text stays out of assistant context.** `list_changes` over MCP replaces the values of `version`, `platform`, `hostname` and `interfaces` in every `agent` snapshot with a placeholder (`changes.assistant_view`), whoever wrote the entry; the field names, summary, source and actor stay. Owner REST (`GET /api/v1/changes`) shows them.

**When a target is due.** It is enabled, assigned to this agent, not under a live lease, and either a scan was requested after its last scan started (`scan_requested_at` > `last_scan_at`), or it has an interval and has never been scanned or its last scan started at least `interval_minutes` ago. Among due targets, a request no job has been issued for since it was made goes first; every other due target, a request already tried once included, goes by when a job for it last started (`last_scan_at` or `job_issued_at`, whichever is later), oldest or never first. A target whose jobs keep timing out therefore goes to the back each time and cannot starve the agent's others; a request still stands until a completed scan serves it.

**Leases.** Issuing a job sets `current_job_id`, `job_issued_at` and `job_methods` (the methods issued). The lease is live for 15 minutes (the 10-minute job timeout plus a margin); a target under a live lease is not issued again, and once it expires the target is due again. The write is a compare-and-set on the lease the check-in read, so two check-ins never lease one target twice. The same lease is recorded on the agent (`agents.current_job_id`, `job_issued_at`), and an agent whose lease is live gets no new job: it runs one job at a time. Because the record is on the agent, reassigning, disabling or deleting the target (or its subnet) mid-scan does not free an agent that is still scanning; nothing is sent to cancel a job. A reassigned target keeps its lease until it expires or its result arrives, so the new agent waits for that target only.

**What results ingest (#11) must do with a lease.** A result is accepted only for a job that is still current on both copies: its `job_id` equals the target's `current_job_id` and the reporting agent's `current_job_id`, whatever the target is assigned to now. A superseded job (re-issued after expiry, or the agent given another) or a second result for one job is refused. Addresses are checked against the subnet's scan scope at ingest, so a range excluded mid-scan stays absolute. Liveness and MACs follow `job_methods`, the methods the server issued, not what the agent says it ran or a later check-in's interfaces. Accepting clears both copies of the lease in one transaction, sets `last_scan_at` to the job's `job_issued_at`, and leaves `scan_requested_at` alone.

**The job.** `ranges` is the subnet's scan scope from `services/scans.scan_scope` (§3.10); a target whose scope is empty gets no job. `methods` are the target's, minus `arp` when the subnet is not on the agent's link, and minus `fdns` until the forward-DNS check exists (#20), so `fdns` is always empty. The subnet is on the agent's link when one reported IPv4 interface's network contains the whole subnet. A target left with neither `icmp` nor `arp` gets no job, and is not leased. `dns_servers` are the subnet's, or empty. `timeout_s` is 600.

---

## 5. MCP server

### 5.1 Overview

The MCP server runs in the same FastAPI process, mounted at `/mcp` (Streamable HTTP transport), implemented with **FastMCP 4.x** (`fastmcp>=4.1,<5`, locked).

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

Auth for `/mcp` is the same owner key (`API_KEY`), sent as a bearer token: `Authorization: Bearer <API_KEY>`. FastMCP enforces it through its own `auth=`: `OwnerKeyVerifier` in `auth.py` compares the token in constant time and grants the scopes `ipam:read` and `ipam:write`. No tool checks the scopes yet. `X-API-Key` is not accepted on `/mcp`, and an empty bearer is never the owner key (§4.1).

A refused request gets FastMCP's RFC 6750 response, not the REST error envelope:

| Request | Status | Response |
|---|---|---|
| No `Authorization: Bearer` header (none, `X-API-Key` only, or another scheme) | `401` | Empty body; `WWW-Authenticate: Bearer` |
| A bearer that is not the owner key | `401` | `WWW-Authenticate: Bearer error="invalid_token", error_description="…"`; JSON body `{"error": "invalid_token", "error_description": "…"}` |

Both `/mcp` and `/mcp/` behave the same.

### 5.2 MCP tool inventory

Tool descriptions are the documentation Claude reads at runtime — write them well. Tools may return richer, denormalised answers than the REST API where it saves Claude round-trips.

| Tool | Description / behaviour |
|---|---|
| `list_sections` | List all network sections/segments. Use to get an overview of how the network is organised before querying subnets. |
| `list_device_types` | List defined device types with usage counts — the valid `device_type` values for host writes. |
| `list_subnets` | List subnets, optionally filtered by section. Returns CIDR, VLAN ID, name, and utilisation (used/total) in one call. |
| `get_subnet` | Full detail for a subnet: VLAN tag, gateway, DNS servers, DHCP pool, utilisation, and free-IP count — one call, no follow-ups needed. Shows each reserved range's `scan_excluded` and the subnet's `scan_dhcp_pool` (null when the subnet has no scan target), so an assistant can explain why an address is not scanned. `list_subnets` returns the same shape. |
| `get_free_ips` | Return unallocated IPs in a subnet (respects gateway, DHCP-pool, reserved-range, and network/broadcast exclusions). Use before allocating to check availability or find a specific address. |
| `list_hosts` | Search and filter host records across the network. Free-text search (hostname, IP, MAC) plus filtering by subnet or status. |
| `get_host` | Full detail for a specific host record including MAC, device type, owner, and last seen. |
| `create_host` | Register a new host at a specific IP address. Validates that it is assignable within the target subnet and not already allocated. Manual placement at the gateway or inside DHCP/reserved ranges is allowed. |
| `bulk_create_hosts` | Create many hosts in one subnet in a single call (e.g. importing devices from a UniFi export). Per-record results; duplicates skipped, bad records errored, batch never aborts. One audit entry. |
| `allocate_next_ip` | Allocate the next available IP in a subnet and create a host record in one step. Use when the caller does not need a specific IP address. |
| `update_host` | Update hostname, domain override, MAC address, status, device type, owner, or notes for an existing host record. Pass nullable field names in `clear_fields` to clear them; clearing a domain override restores subnet-domain inheritance. |
| `delete_host` | Remove a host record, freeing the IP address for future allocation. |
| `bulk_delete_hosts` | Delete many hosts by ID in one call (cleanup workflows). Returns deleted count + not-found IDs. |
| `add_reserved_range` | Reserve a block of IPs in a subnet so automatic allocation skips it (manual creation inside it stays allowed). Always creates a scanned range: the tool has no `scan_excluded` argument. |
| `delete_reserved_range` | Remove a reserved range, returning its addresses to the allocation pool. Refuses a range with `scan_excluded`, because removing it would widen what is scanned; the error says to remove it in the web UI. |
| `list_changes` | Recent changes to the IPAM (audit log), newest first, each with its `source` (`ui`, `api`, `mcp` or `agent`) and `actor` (null for the owner's own writes, `agent:<name>` for an agent). In `agent` snapshots the values the agent reports about itself are withheld (§4.5). Use to answer "what changed recently?" or to review previous MCP-driven edits. |

All MCP writes are recorded in the change log with `source = mcp`. No tool creates, changes or lists agents, tokens or scan targets, and no tool can widen a subnet's scan scope (v0.2 spec §7.2, invariant 2).

### 5.3 Claude Desktop config

Claude Desktop's native remote connectors require OAuth or no auth, so the owner-key path goes through `mcp-remote`, which sends the key as a bearer token. Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"mcpServers": {
  "tlg-ipam": {
    "command": "npx",
    "args": [
      "-y", "mcp-remote",
      "https://ipam.example.com/mcp",
      "--header", "Authorization: Bearer ${TLG_IPAM_KEY}"
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

- **Dashboard** — summary stats: total subnets, total hosts, hosts by status, plus a **recent activity feed** driven by the change log (shows source: UI / API / MCP, and the actor when set)
- **Section view** — click section in sidebar; shows its subnets with utilisation
- **Subnet view** — click subnet; shows host table + free IP count + VLAN/gateway/domain/DHCP-pool info header, with reserved ranges shown as removable chips + a "Reserve range" action
- **Host drawer** — slide-over on row click; all fields editable (domain field shows the inherited subnet default as placeholder; blank = inherit); delete button with confirmation
- **Allocate modal** — triggered by "Allocate next free IP" button; prefills next IP; hostname required
- **CSV import modal** — on the subnet view; file picker or paste, header-mapped columns, per-row results shown after import (same bulk endpoint as MCP)
- **Agents** (`/agents`, v0.2 spec §5.8) — one row per scan agent: a status dot from `last_checkin_at` (online within three check-in intervals, late within 15 minutes, then offline; "never checked in" and "disabled" show as such), the reported version, hostname, platform and interfaces, the subnets its scan targets cover ("unknown" while the scan targets have not loaded or failed to load, never "none"), and its token by display prefix (or "revoked"). A refused check-in shows the agent's `last_error`. Agent-reported text is rendered as text only. The list refreshes every 30 seconds. **Add agent** names the agent and then shows its `tlga_…` token once, with the container command to run it (`docker run … --network host --cap-add NET_RAW -e IPAM_URL=<this origin> -e IPAM_AGENT_TOKEN=<token> ghcr.io/deusmaximus/tlg-ipam-agent:<server version>`, spec §5.6). The token lives only in that dialog: nothing closes the dialog while the create request is in flight, a backdrop click does not close the token step, the list reloads from `GET /agents` (which never carries a token), and once the dialog closes nothing on the page can show the token again. Row actions: Disable / Enable (`PUT` `enabled`), Revoke (with a confirmation that says a revoked token cannot be reissued, so the agent must be deleted and added again), Delete (with a confirmation naming how many subnets are left unassigned)
- **Settings** — API key display (masked), device type management (add/rename/re-icon/delete with a curated icon picker), SQLite backup download, app version

When no API key is stored in the browser, the SPA shows an in-app API-key connection gate. The key is stored only in that browser. A reverse proxy / Cloudflare Access layer may provide an additional outer access-control boundary.

---

## 7. Project structure

```
tlg-ipam/
├── docker-compose.yml
├── Dockerfile               # multi-stage: node builds web/dist, python runs API + serves it
├── api/
│   ├── requirements.in / requirements-dev.in    # direct dependencies (edit these)
│   ├── requirements.txt / requirements-dev.txt  # generated locks: pinned versions + hashes, installed with --require-hashes
│   ├── tests/                # pytest — API, MCP, validation, allocation, and backup coverage
│   └── app/
│       ├── main.py           # FastAPI app + static SPA serving + MCP mount
│       ├── config.py         # env vars (pydantic-settings)
│       ├── database.py       # SQLAlchemy async engine + session
│       ├── models.py         # ORM models (Section, Subnet, ReservedRange, Host, DeviceType, Change, Agent, Token, ScanTarget)
│       ├── schemas.py        # Pydantic request/response schemas
│       ├── agent_protocol.py # the agent wire contract: check-in request and job shapes, PROTOCOL_VERSION
│       ├── auth.py           # owner-key check (REST dependency + /mcp bearer verifier); agent-token dependency
│       ├── mcp_server.py     # FastMCP tools (thin wrappers over services)
│       ├── services/         # business logic — single source of truth
│       │   ├── backups.py    # consistent SQLite online-backup snapshots
│       │   ├── sections.py
│       │   ├── subnets.py    # incl. free-IP + next-free allocation logic
│       │   ├── hosts.py
│       │   ├── device_types.py
│       │   ├── agents.py     # agents, agent tokens (hashed), agent-token authentication
│       │   ├── scans.py      # scan targets; scan_scope, the one place scan scope is computed; request-scan and its cooldown
│       │   ├── jobs.py       # agent check-in, due targets, job leases
│       │   └── changes.py    # audit log writes + reads
│       └── routers/
│           ├── sections.py
│           ├── subnets.py
│           ├── hosts.py
│           ├── device_types.py
│           ├── agents.py     # owner CRUD on /api/v1/agents
│           ├── agent.py      # agent-facing /api/v1/agent/* (agent token only)
│           ├── scan_targets.py # owner CRUD on /api/v1/scan-targets
│           └── changes.py
└── web/
    ├── package.json
    ├── eslint.config.js
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── api/             # fetch wrappers per resource
        ├── components/      # Sidebar, HostTable, HostDrawer, AllocateModal, AddAgentModal, etc.
        ├── utils/           # csv.js; agents.js (status dot, server-time parsing, agent container command)
        └── pages/           # Dashboard, SectionView, SubnetView, Agents, Settings
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
| 6 | API: services + routers — subnets: CRUD + `/free` + `next-free` allocation logic incl. interval-based reserved-space exclusions |
| 7 | API: services + routers — hosts: CRUD + search (`?q=`); reserved ranges, device types, changes, and snapshot backup |
| 8 | Tests: pytest for validation + allocation edge cases (gateway/DHCP skipping, large exclusions, /31, duplicate IPs, overlap and CIDR-change invariants) |
| 9 | MCP: `mcp_server.py` — all 16 curated tools wrapping services, mounted at `/mcp` with auth |
| 10 | **Test MCP with `mcp-remote` locally before touching the frontend** |
| 11 | Frontend: scaffold Vite + Tailwind + React Router, `api/` fetch layer |
| 12 | Frontend: Sidebar with section/subnet tree |
| 13 | Frontend: Subnet view + sortable host table, virtualised above 200 rows |
| 14 | Frontend: Host drawer (view + edit + delete) |
| 15 | Frontend: Allocate modal + Dashboard (stats + activity feed) |
| 16 | Quality gate and polish: ESLint, Vitest component coverage, CI, empty states, error toasts, loading skeletons, dark mode QA |

---

## 9. Key decisions & rationale

| Decision | Rationale |
|---|---|
| SQLite not Postgres | Zero ops overhead. A homelab IPAM will never have sustained write contention. Backup = copy one file. Connections run with `journal_mode=WAL` and `busy_timeout=5000` so the occasional simultaneous UI/MCP write queues instead of raising "database is locked". |
| FastAPI not Flask | Native async, automatic OpenAPI schema generation, Pydantic validation built in. |
| FastMCP with curated tools, not OpenAPI auto-generation | Hand-written tools have better names, parameters, and denormalised answers — materially better Claude ergonomics. Shared `services/` layer eliminates the drift risk that auto-generation was meant to solve. FastMCP is the actively-maintained de facto standard. |
| Streamable HTTP not stdio | Works with mcp-remote for Claude Desktop. Also compatible with any future MCP client without modification. |
| One uvicorn worker, enforced by the image (`--workers 1`) | MCP session state is per-process memory and SQLite has one writer; the session state is not shared between processes, and a second process adds no capacity a homelab needs (see §2.2). |
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
- Every write appears in the change log. MCP provenance is authoritative; UI/API attribution is advisory because REST clients share one API key and can set `X-TLG-Client`.
- An agent's `tlga_…` token appears in exactly one response, the create response, and is stored only as its SHA-256 and display prefix. Its create and revoke each have a change-log entry.
- The owner key is refused on agent routes; an agent token is refused on owner routes and on `/mcp`. A revoked token, and the token of a disabled agent, are refused.
- A subnet's scan scope leaves out network and broadcast, the DHCP pool unless pool scanning is on, and every excluded reserved range; an address in both an excluded and a scanned range is excluded. Ranges from a database made before scan exclusion existed read as scanned.
- Scan targets are refused for prefixes shorter than `/20` and for method sets without `icmp` or `arp`. Deleting a subnet deletes its scan target; deleting an agent unassigns its targets. Every scan-target and scan-exclusion change is in the change log.
- A scan target is issued to its agent at check-in when its interval has passed or a scan was requested, and is not issued again while its lease is live; an expired lease makes it due again. A job's ranges are the scan scope, and `arp` is dropped for a subnet that is not on the agent's link; a target with no liveness method left gets no job.
- A check-in on another protocol gets no jobs and a `409 PROTOCOL_MISMATCH`, recorded in the agent's `last_error`. Agent-reported fields are length-capped and printable-only; a check-in that breaks either is refused and recorded the same way.
- A second scan request for a subnet within 15 minutes is refused, whoever asks.
- An agent checks in at most once every 10 seconds, and keeps at most 100 audit entries of its own; text it reports about itself never reaches an assistant through `list_changes`. A target whose jobs keep timing out does not starve the agent's other due targets.

### Testing
- pytest covers CIDR/IP validation, CIDR child invariants, explicit-null updates, the overlap rule, interval maths, consistent backups, MCP clearing, and next-free allocation edge cases.
- ESLint, Vitest component tests, and the production frontend build run in CI alongside pytest, with a check that the dependency locks match their `.in` files.

### MCP integration
- Claude Desktop connects via `mcp-remote` with the owner key as a bearer and lists all available tools.
- `/mcp` answers `401` to a missing or wrong bearer, and to `X-API-Key` alone.
- All 16 MCP tools are callable from Claude Desktop.
- Bulk import reports per-record outcomes; a batch with some bad rows still imports the good ones.
- No MCP tool configures agents, scan targets or scan scope. `add_reserved_range` creates a scanned range; `delete_reserved_range` on an excluded range fails with a message pointing at the web UI; `get_subnet` shows `scan_excluded` and `scan_dhcp_pool`.
- Natural-language queries resolve to correct tool calls without ambiguity.

### Operations
- `docker compose up --build` produces a running stack with no manual steps.
- All data persists across container restarts (named volume).
- API key changeable via env var without rebuild. The server refuses to start with `API_KEY` unset, empty, or `change-me` (§4.1).
- Settings exposes a SQLite backup download created through the online backup API rather than streaming the active database file.
- `docs/backup-and-restore.md` describes a scheduled pull of `GET /api/v1/backup` from another host (dated files, retention) and a restore procedure; following it against a running instance produces a dated snapshot and a working restore from it. Its examples use placeholder domains only.
- The container runs one uvicorn worker, pinned by `--workers 1` in the image's `CMD` (§2.2).

### UI quality
- Dark mode correct — no hardcoded colours bleeding through.
- Works at 1280px and 1920px wide.
- Empty states present on all list views.
- Host IP sorting is numeric and keyboard accessible; inventories above 200 rows use windowed rendering.
- No console errors in production build.

---

*TLG-IPAM Project Brief · Built for Claude Fable*
