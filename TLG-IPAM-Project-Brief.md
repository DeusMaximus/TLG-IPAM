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

**The scan agent is a second image** (`agent/`, v0.2 spec §5.6), run on a Linux host on the VLAN it sweeps with host networking and `CAP_NET_RAW`, not beside the server in this compose file. It connects out to the server's `/api/v1/agent/*` over HTTPS with TLS always verified (plain `http` only to its own loopback), checks in every minute, and runs the jobs it is given one at a time: an ICMP echo sweep of the job's ranges, then an ARP sweep when the job is issued `arp` (a broadcast request from a raw `AF_PACKET` socket on the interface whose network contains the subnet, and one retry for each address still silent after a second; a reply is taken only from an address it asked, and gives the address's MAC), then PTR lookups for the addresses either found (one UDP query per DNS server, at most three servers, no retransmission or TCP, truncated replies unused; the servers are the subnet's `dns_servers`, else the addresses in the agent host's `/etc/resolv.conf`, read again for every job, in a thread so a stalled read cannot hold the agent: a path that is not a regular file or is over 64 KiB when the agent looks at it is not opened, and gives none, as does a read not done within a second, or by the job's deadline; a read that stays stuck needs the agent restarted, unless the subnet lists its own servers), every datagram and frame the agent sends for all three paced together at up to `MAX_PPS` (default 200) a second, from one job into the next: each is handed to the kernel in the step its slot is granted in or not at all (a DNS query or ARP request the socket cannot take at once is a failed send, never sent later), and the next slot comes no sooner than `1/MAX_PPS` after that step has ended, so no two are handed over closer than that, with nothing caught up after a late wake or a late hand-off, and the rest of the agent runs between any two slots (the ARP the kernel itself sends before an echo request to an on-link address, up to 3 for an address that does not answer, is outside that budget), inside a 10-minute timeout after which it reports what it saw as incomplete; a scan still running 30 s past its timeout is stopped and reported as an agent error. Before any packet it refuses a job whose subnet is outside its own `ALLOWED_CIDRS` (default RFC 1918 and CGNAT), whose ranges leave the subnet, or that holds more than 4,096 addresses, and reports the refusal, which the server keeps as the target's `last_scan_error` (§3.10). The agent's own address on that interface is reported alive with the interface's MAC, without a request (the kernel answers none of its own). A job issued `arp` on an agent with no interface on the subnet that has a MAC, or one issued a method the agent cannot run yet (`fdns`, #20), runs the rest and is reported incomplete, so it marks nothing dead. So is a job whose PTR lookups had no DNS server to ask (the subnet names none and the host's `resolv.conf` lists none it can use), with the error `rdns not run: the agent has no DNS server to ask (…)` and no DNS packet sent, or one of whose lookups tried and could send its query to none of its servers, which also cancels the lookups still running (no query is handed to a socket after it; one already handed over may still go out): a lookup not made is not one that found no name, so the stored names stay (spec §5.2). A refused check-in starts no scan: `429`/`503`/5xx are retried after 10 s to 5 min of jittered back-off, `401`/`403`/`409`/`422` every 13.5–15 min (spec §5.7). Every request to the server has a 30-second deadline covering the whole answer, which is read undecoded into at most 1 MiB, never holding more, and acted on only in its route's shape; the agent logs status codes and validated counts, never the server's text, and redacts its own token from its log. The agent never runs, downloads or installs anything the server sends (spec §7.2, invariant 1). Running it: the README's "Scan agent" section.

### 2.3 URL layout

| Path | Purpose |
|---|---|
| `/` | React SPA (static files + SPA fallback, served by FastAPI) |
| `/api/v1/...` | REST API (owner key) |
| `/api/v1/agent/...` | Agent-facing REST API (agent token; §4.1): check-in (§4.5) and scan results (§4.6) |
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
| `cidr` | str — e.g. `10.10.30.0/24`. Validated on write, and stored in its canonical spelling (`ipnet.canonical_cidr`, `str` of the parsed network): `10.10.30.0/255.255.255.0`, `10.10.30.0/0.0.0.255` and `10.10.30.0/0024` are all stored, answered and logged as `10.10.30.0/24`, and a refusal names the network that way too. A subnet stored with another spelling before #97 keeps it until an update sends `cidr` (the UI's edit form resends it on every save), which stores the canonical one; that entry's snapshots show both. Every comparison is of networks, never of strings (the overlap rule below; a CIDR edit to another spelling of the same network changes nothing else). A scan job (§4.5) and `list_scan_findings` (§5.2) carry the canonical spelling whatever is stored |
| `vlan_id` | int, nullable — VLAN tag number (1–4094) |
| `name` | str — friendly label |
| `domain` | str, nullable — default domain suffix for hosts in this subnet (e.g. `lab.home.arpa`) |
| `gateway` | str, nullable — IP within subnet |
| `dns_servers` | JSON array of IP strings, nullable. At most 8 on write (`schemas.SUBNET_DNS_SERVERS_MAX`): a longer list is `422` "dns_servers: a subnet may list at most 8 DNS servers, not N". A subnet stored with a longer list before that bound keeps it: an update that leaves `dns_servers` out, or resends the stored list unchanged (as the UI's edit form does), is applied and keeps it; an update that changes it must meet the bound. A scan job carries the first three (§4.5) |
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
| `last_seen` | datetime, nullable — the stored column is set by hand: writable, and cleared by writing null (`clear_fields: ["last_seen"]` over MCP). A value sent with a UTC offset is converted to UTC before it is stored. Responses show the later of the stored value and the `last_seen_at` of the observation at the host's subnet and address (§3.11), derived on read: scan ingest never writes hosts. Clearing the stored value therefore still shows the scan time if the address has been seen |
| `last_seen_source` | output only: `manual` (the stored value), `scan` (the observation; also on a tie), or null when neither exists. Taken from the observation's `last_seen_at`, never from `alive`, so an address a later sweep missed still shows when it last answered |
| `created_at` / `updated_at` | datetime, auto-managed. `updated_at` moves only when a write changes a stored field: a `last_seen` that is the stored instant, sent with or without an offset, is not a change |

### 3.5 DeviceType

User-customisable device types (managed in Settings). Seeded on first boot with: `server`, `vm`, `lxc`, `network`, `iot`, `desktop`, `laptop`, `phone`, `other`.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `name` | str, unique — display name, e.g. `Raspberry Pi` |
| `icon` | str — identifier from the curated icon set (~28 Lucide icons) |

Rules: renaming a type cascades to all hosts using it (one transaction). A type in use cannot be deleted. `other` is the fallback default for new hosts — it cannot be renamed or deleted, but its icon can change.

### 3.6 Change (audit log)

A record of every write, never edited, with one deletion: an agent's own check-in entries (`source="agent"`) are capped at its newest 100, so the older ones are deleted (§4.5); owner entries are never deleted. Because Claude can write to this system via MCP, you need to be able to review what it did. Also powers the dashboard "recent activity" feed.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `ts` | datetime, auto |
| `entity_type` | enum: `section` \| `subnet` \| `host` \| `device_type` \| `agent` \| `token` \| `scan_target`. Reserved-range writes, a range's scan exclusion included, are logged as `subnet` updates |
| `entity_id` | UUID of the affected record |
| `action` | enum: `create` \| `update` \| `delete` \| `revoke` (`revoke` is for tokens only) |
| `summary` | str — human-readable one-liner, e.g. `host pbs-01 created at 10.10.20.45` |
| `before` / `after` | JSON snapshots, nullable |
| `source` | enum: `ui` \| `api` \| `mcp` \| `agent` — MCP and `agent` are server-assigned; REST uses the advisory `X-TLG-Client: ui` hint, so `ui` versus `api` is a UX label rather than trusted provenance. `agent` is the agent's own write: a check-in that changed what the agent reports (§4.5), or its enrolment exchange (§4.8); each agent keeps its newest 100 such entries on its agent record (token entries are not capped), and over MCP the reported values in `agent` snapshots are withheld. Not logged: heartbeats (`tokens.last_used_at`, `agents.last_checkin_at`), job scheduling (the lease columns), and scan results ingest (observations and the target's last-scan columns, §4.6) |
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

A scan agent (v0.2 spec §5.4). The owner creates it over REST with an enrolment token; the agent exchanges that for its agent token over `/api/v1/agent/enrol` (§4.8), checks in over `/api/v1/agent/checkin` (§4.5) and reports scans over `/api/v1/agent/results` (§4.6). The Agents page arrives with v0.2 M2a.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `name` | str. 1–74 printable characters, trimmed: `agent:<name>` is the audit `actor` for the agent's own writes and must fit `changes.actor` (80). Unique case-insensitively (`409`) |
| `name_key` | str, unique — `name.casefold()`, so the database itself refuses two names that differ only in case, even from racing requests. Internal: not returned, not in audit snapshots |
| `state` | enum: `pending` (has never held an agent token: created, not yet enrolled) \| `active` \| `disabled`. An agent is created `pending` with an enrolment token, and becomes `active` when it exchanges one (§4.8) |
| `version`, `platform`, `hostname`, `interfaces` | nullable; reported by the agent at check-in, and null until its first (the first three may also be reported at enrolment, §4.8). Trimmed, printable characters only, at most 40, 120 and 253 characters; `interfaces` is up to 64 `{name, cidr, mac}` (name at most 15 characters; `cidr` an address with its prefix length, normalised, no IPv6 scope id; `mac` normalised or null). A check-in that fails these checks changes none of them |
| `last_checkin_at` | datetime, nullable — set by every check-in that takes its slot (§4.5), including one then refused for its protocol or a field (`409`, `422`). A check-in refused by the rate limit (`429`), or because the agent was disabled or its token revoked after it authenticated (`403`; §4.5 gives which of the two a request gets), does not set it |
| `current_job_id`, `job_issued_at` | nullable, read-only — the job the agent was last issued, and when (§4.5). The agent is busy while that lease is live, whatever has since happened to the target. Databases created before them gain the columns on startup |
| `last_error` | text, nullable — why the last check-in was refused (protocol mismatch, or the first field that failed validation, by name and never by value). Cleared by a check-in that succeeds |
| `created_at` | datetime, auto |

Rules: disabling an agent refuses its token without revoking it, and enabling it accepts the token again. Deleting an agent revokes its unrevoked tokens (each logged as a `token` `revoke`), sets `agent_id` to null on its scan targets (each logged as a `scan_target` update; their settings are kept), then deletes the agent and its token rows.

### 3.9 Token

A credential bound to an agent (v0.2 spec §4.2). An agent token is `tlga_` followed by 43 URL-safe random characters (256 bits); an enrolment token is `tlge_` and 43 more. An enrolment token is single-use, expires an hour after it is issued, and reaches one route, `POST /api/v1/agent/enrol` (§4.8), which exchanges it for the agent token.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `kind` | enum: `agent` \| `enrol`. "Add agent" and re-enrol issue `enrol` tokens; only the enrolment exchange issues an `agent` token |
| `agent_id` | FK → Agent |
| `token_hash` | SHA-256 of the token, hex, unique. Never returned and never in an audit snapshot |
| `token_prefix` | the first 13 characters (`tlga_` or `tlge_`, and 8 more), for display |
| `created_at`, `last_used_at` | `last_used_at` is set by a request the route serves — a check-in that takes its slot, an accepted result set — in that request's transaction, when the stored time is unset or at least 10 seconds old (`agents.LAST_USED_GRANULARITY`; the condition is in the SQL `UPDATE`, so racing requests write it once). A request refused before it takes its check-in slot, and a refused result set, never set it, so a token used at request rate drives no writes; a check-in refused after it took its slot (`409`, `422`) has set it, with `last_checkin_at` and `last_error` (§3.8) |
| `expires_at`, `used_at` | enrolment tokens only; null for agent tokens. `expires_at` is an hour after `created_at` (`agents.ENROL_TOKEN_LIFETIME`); the token is accepted strictly before it. `used_at` is set by the exchange that spends it, and is the only record the enrol route keeps of a token's use: it writes no `last_used_at`, on either token |
| `revoked_at` | datetime, nullable |

Rules: the plain token appears in exactly one response, the one that issues it (create or re-enrol for an enrolment token, the enrol exchange for an agent token); it cannot be recovered afterwards. An agent holds at most one unrevoked `agent` token, enforced by a partial unique index, and at most one enrolment token that can still be exchanged (re-enrol revokes the one before). A token is live while it is unrevoked and, for an enrolment token, unused and unexpired: revoke and delete revoke the live ones only, so a spent or expired enrolment token is never revoked. Token create, use (the exchange) and revoke are audit-logged with `entity_type="token"`. The owner's writes have a null `actor`; the exchange is the agent's own write, with `source="agent"` and `actor` `agent:<name>` (§4.8).

### 3.10 ScanTarget and scan scope

A subnet the owner has chosen to scan, and how (v0.2 spec §5.3, §5.4). At most one per subnet. Configured over REST only; no MCP tool reads or writes it. Check-in hands out jobs for it (§4.5), and results ingest records them (§4.6).

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
| `last_scan_at`, `last_scan_duration_ms`, `last_scan_complete` | nullable, read-only — the last-scan record, written by an accepted result set (§4.6). `last_scan_at` is when that job was issued, by the server's clock, so it also keeps the attempt time scheduling orders by; `last_scan_duration_ms` is the agent's own `finished_at − started_at`; `last_scan_complete` whether the agent swept every range |
| `last_scan_error` | text, nullable, read-only — the `error` the agent reported with its last result set, or null. Agent text: non-printable characters become spaces, whitespace runs collapse, at most 500 characters. Kept out of audit snapshots, which `list_changes` serves over MCP. Databases created before it gain the column on startup |
| `created_at` | datetime, auto |
| `runnable_methods` | response only, never stored — the methods a job for the target would run on its agent now, from the interfaces the agent reported at its last accepted check-in. Computed on read by `services/jobs.job_methods`, the rule check-in issues jobs by (§4.5): `fdns` dropped until #20, `arp` dropped when no reported interface is on the subnet's link. `[]` when no liveness method is left: the target cannot be scanned by this agent, and check-in issues it no job. Null when that is not known: no agent assigned, or one that has never reported its interfaces (no check-in of it has been accepted; a refused one stores none). A disabled agent is judged by what it last reported. The agent's next check-in changes it without a write to the target |
| `agent_checkin_at`, `agent_checkin_refused` | response only, never stored — how old the evidence for `runnable_methods` is: the agent's `last_checkin_at` (the same time `GET /agents` serves) and whether that check-in was refused (`409` or `422`, read from the agent's `last_error`, which only a refused check-in sets and an accepted one clears). Only an accepted check-in reports interfaces, so when the last check-in was accepted they are from `agent_checkin_at`; when it was refused they are from an earlier accepted one, whose time is not stored. Both null when `runnable_methods` is. The stored time is the server's clock; the UI does not read a time more than one check-in interval ahead of the browser's clock as an age (§6.2) |

**Scan scope** is the set of addresses a scan may send packets to. It is computed in one place, `services/scans.scan_scope(subnet)`, as inclusive address ranges: the subnet's usable addresses (no network or broadcast for `/30` and shorter), minus the DHCP pool unless `scan_dhcp_pool` is set, minus every reserved range with `scan_excluded`. Exclusion wins: an address in both an excluded and a scanned reserved range is excluded. A subnet without a scan target has the default scope (pool not scanned). Scan-target responses carry it as `scope: [{start, end, start_int, end_int}]`: each range's addresses, and the same bounds as integers of the subnet's family, so the UI places a host by its `ip_int` without parsing an address. A job's `ranges` carry the addresses only (§4.5).

Rules: one target per subnet (`409`, also enforced by `UNIQUE(subnet_id)`). Unknown subnet or agent: `404`. A method outside the four, or a set without `icmp` or `arp`: `422`. Every create, update and delete is audit-logged as `scan_target`; an update that changes nothing writes no entry.

**Leaving scope deletes observations and finding dismissals** (§3.11, §3.12). A write that can narrow the scope deletes the subnet's observations and dismissals outside the scope as it then stands, in the same transaction: turning `scan_dhcp_pool` off, deleting the target (the default scope leaves the pool out), excluding a reserved range or adding one excluded, and editing the DHCP pool or the CIDR (`services/scans.drop_out_of_scope`). The write itself is logged as usual; the deleted rows are not. The deletion judges the scope as committed, under SQLite's write lock, so a pool another request moved, or an observation an ingest stored, after the edit first read the subnet is not undone by it.

### 3.11 Observation

The latest scan state of one address (v0.2 spec §5.4), upserted by results ingest (§4.6). `UNIQUE(subnet_id, ip)`. No history. Host responses read it for `last_seen` (§3.4), in one query per batch of hosts; findings (§4.7) read it too.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet |
| `ip`, `ip_int` | the address, and its integer form (internal, as on hosts) |
| `mac` | str(17), nullable — normalised as `Host.mac`. Only from a job issued `arp`; a later answer without one keeps it |
| `mac_randomised` | bool — the locally-administered bit of `mac` is set |
| `rdns_name` | str(253), nullable — the PTR name, only from a job issued `rdns`, and only if it is a DNS hostname: ASCII letters, digits and hyphens in dot-separated labels of 1–63 characters that neither start nor end with a hyphen, at most 253 characters, one trailing dot dropped, lowercased. Anything else is dropped whole, never repaired. A name from an `rdns` answer replaces it. No name, or a dropped one, clears it only in a `complete` result set; an incomplete one leaves it (§4.6) |
| `alive` | bool — answered in the last scan that could tell (§4.6) |
| `first_seen_at`, `last_seen_at` | datetime — `last_seen_at` only moves when the address answers |
| `agent_id` | str, nullable — the agent that last reported it. No foreign key, so deleting an agent rewrites nothing |

Rules: observations exist only inside the subnet's scan scope (§3.10). Results ingest deletes every observation, in any subnet, unseen for 90 days. Deleting a subnet deletes its observations. Nothing in ingest writes `hosts` or the change log.

### 3.12 FindingDismissal

A scan finding the owner dismissed (v0.2 spec §5.5). Findings themselves are computed on read (§4.7) and never stored; a dismissal hides the finding at its address and type while the finding's observed value is the one dismissed, and stops hiding it once the value changes. `UNIQUE(subnet_id, ip, type)`: dismissing the same finding with a new value replaces the old one.

| Field | Type / notes |
|---|---|
| `id` | UUID, primary key |
| `subnet_id` | FK → Subnet |
| `ip`, `ip_int` | the address, and its integer form (internal) |
| `type` | a finding type (§4.7) |
| `observed` | str(253) — the observed value that was dismissed: a MAC, a PTR name, a last-seen timestamp, `never`, or empty |
| `created_at` | datetime — when it was dismissed, or last replaced |

Rules: dismissals exist only inside the subnet's scan scope, like observations: a write that narrows the scope deletes those outside it (§3.10), and deleting a subnet deletes its dismissals. Only `POST /findings/dismiss` writes one, and only for a finding that exists as listed, so the stored value is always one the server computed. There is no un-dismiss yet; the findings panel (#19) is where it would go.

---

## 4. REST API

### 4.1 Auth

Single static API key passed as a header:

```
X-API-Key: <value from API_KEY env var>
```

All endpoints except `GET /health` and the agent-facing `/api/v1/agent/*` (enrolment included) require this header. Return `401` if missing, `403` if wrong. The key is compared in constant time (`hmac.compare_digest`, in `auth.is_owner_key`). Key is set at container start — no key rotation UI needed in v1. The server refuses to start unless `API_KEY` is set, non-empty (not only whitespace), free of whitespace at either end and not `change-me`, the placeholder `.env.example` ships: `config.load_settings` runs when `app.config` is imported, so uvicorn exits with a message that names `API_KEY` and says how to generate a key, before it serves anything (under `--reload`, the reloader stays up and its server process exits). A key with a leading or trailing space, tab or newline is refused, not trimmed: most HTTP clients cannot send such a header value, a raw one can, and trimming would leave two candidate keys. The message is fixed and never contains the key. Separately, `is_owner_key` never matches an empty value, so an empty `X-API-Key` is refused (`403`) whatever the setting holds. REST does not accept the key as a bearer, and `/mcp` does not accept `X-API-Key` (§5.1).

Agent-facing routes, `/api/v1/agent/*` but `enrol`, take an agent token instead (§3.9), as `Authorization: Bearer tlga_…`, authenticated through the `auth.require_agent` dependency and nothing else. It accepts only an unrevoked `agent` token of an `active` agent, and only reads: the route that serves the request records `last_used_at` in its own transaction (§3.9), so a refused bearer never writes it, nor does a request refused before its check-in slot or a refused result set. A check-in then revalidates the agent's state and the token's revocation in its claim, under the write lock (§4.5), and so does results ingest before it touches the lease (§4.6). If the database refuses a write of an agent route (locked past SQLite's 5-second busy timeout, a read-only file, a full disk), the route's transaction is rolled back and the request gets `503 UNAVAILABLE`, safe to retry, and nothing of it is written. The message says "busy" only for a lock, and the server logs the operation and SQLite's error name, never SQL, parameter values or the bearer. A failed rollback, a failure of the authentication read itself, and a database error that is not an `OperationalError` (an integrity or programming error, say) are a bare 500. The token is found by the SHA-256 of the presented value, so the stored secret is never compared. Refusals use the REST error shape: `401 UNAUTHORIZED` when no bearer is sent (including `X-API-Key` alone), `403 FORBIDDEN` "Invalid agent token" for any bearer that is not a usable agent token — the owner key, an unknown, revoked or `enrol` token, or the token of a disabled or deleted agent. The owner routes refuse an agent token (`403` as `X-API-Key`, `401` as a bearer), and so does `/mcp` (`401 invalid_token`). The router for `/api/v1/agent/*` carries `require_agent` itself, so a route added to it is guarded too, and it is mounted apart from the owner routers, so the owner key never reaches it. The routes are check-in (§4.5) and scan results (§4.6). The one exception is `POST /api/v1/agent/enrol` (§4.8), on a router of its own that holds nothing else (`routers/agent.enrol_router`): it takes an enrolment token, `Authorization: Bearer tlge_…`, checked by `services/enrolment` behind its own throttle, and neither the owner key nor an agent token.

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
| `PUT  /api/v1/subnets/{id}` | Update. A DHCP-pool or CIDR edit deletes the observations it puts out of scan scope (§3.10). |
| `DELETE /api/v1/subnets/{id}` | Delete only if no hosts; `?force=true` cascades, deleting all hosts and reserved ranges too. Its scan target, observations and finding dismissals are deleted either way. |
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
| `PATCH /api/v1/subnets/{id}/reserved/{range_id}` | Set `{scan_excluded}` (required, not null). Excluding a range deletes its observations. A request that changes nothing writes no change-log entry. |
| `DELETE /api/v1/subnets/{id}/reserved/{range_id}` | Remove a reserved range. |
| `POST /api/v1/subnets/{id}/scan` | Request a scan ("Scan now"): sets the scan target's `scan_requested_at`, logged as a `scan_target` update. `202` with the target. The agent picks it up at its next check-in. At most one request per subnet in 15 minutes, from any caller: a second is `429 COOLDOWN`, changes nothing and does not restart the wait. No scan target, a disabled one, or one with no agent: `409`. |
| `GET  /api/v1/subnets/{id}/findings` | The subnet's scan findings (§4.7), by address. `?type=` filters to one type (unknown: `422`); `?include_dynamic=true` adds unknown hosts inside a scanned DHCP pool. Dismissed findings are left out. Unknown subnet: `404`. |
| `POST /api/v1/findings/accept` | Accept findings by key and fingerprint (`{findings: [{subnet_id, ip, type, observed, fingerprint}]}`, 1–1000). Per-record results: `accepted` (with `host_id`), `skipped` (`changed`), `error`. Each accepted finding is written and committed on its own through the hosts service, with its normal audit entry. |
| `POST /api/v1/findings/dismiss` | Dismiss findings by key, same body. Per-record results: `dismissed`, `skipped` (`changed`, `already dismissed`), `error`. One transaction; one `subnet` audit entry per subnet with a dismissal. |
| `GET  /api/v1/changes` | Recent changes, newest first. Filters: `?entity_type=` `?limit=`. |
| `GET  /api/v1/agents` | List agents, by name, each with its tokens (prefix and timestamps only, newest first). |
| `POST /api/v1/agents` | Create an agent (`{name}`), `pending`, with an enrolment token. `201`; the response carries the plain token as `data.enrol_token` and its expiry as `data.enrol_token_expires_at`, the only response but re-enrol's that does. The agent gets its agent token by exchanging the enrolment token (§4.8). Duplicate name (case-insensitive): `409`. |
| `GET  /api/v1/agents/{id}` | Detail, with its tokens. |
| `PUT  /api/v1/agents/{id}` | Update `name` and/or `enabled` (`false` → `disabled`; `true` → `active`, or `pending` for an agent that has never held an agent token). A request that changes nothing writes no change-log entry. |
| `POST /api/v1/agents/{id}/revoke` | Revoke every live token of the agent (its agent token, and an enrolment token it has not exchanged), read under the write lock so a token an enrolment exchange has just issued is among them; the agent is kept. `409` if it has none. |
| `POST /api/v1/agents/{id}/re-enrol` | Issue a new enrolment token for an existing agent: `201` with the agent and `enrol_token`, `enrol_token_expires_at`, as create returns them. An enrolment token the agent still holds unused is revoked; the write lock is taken before the agent's tokens are read (as for revoke and delete), so two re-enrols that meet leave one exchangeable token. Its agent token is left alone until the new enrolment token is exchanged, which revokes it; the agent keeps its id, name, state and scan targets. A disabled agent: `409` (enable it first). |
| `DELETE /api/v1/agents/{id}` | Revoke its tokens, unassign its scan targets, then delete the agent and its tokens. |
| `GET  /api/v1/scan-targets` | List scan targets, oldest first, each with its `scope`, `runnable_methods` and the agent check-in it is judged from (§3.10). The subnets, the agents and the scope's inputs are read for all the targets at once, in chunks of 500 distinct ids (`scans._IN_BATCH`), so the list runs the same six SQL statements for one target as for five hundred, and past 500 distinct subnets or agents a fixed number more per chunk (four per chunk of subnets, one per chunk of agents), never one per target. No statement binds more than one chunk's ids, so the SQLite build's parameter limit (999 before 3.32, 32,766 by default since, 250,000 in the image) does not bound how many targets the list answers for. |
| `POST /api/v1/scan-targets` | Create (`{subnet_id, agent_id?, enabled?, methods?, scan_dhcp_pool?, interval_minutes?}`). `201`. Subnet shorter than `/20`, bad methods or interval: `422`; subnet already has one: `409`. |
| `GET  /api/v1/scan-targets/{id}` | Detail, with `scope`, `runnable_methods` and the agent check-in it is judged from; the same answer the list gives for the target. |
| `PUT  /api/v1/scan-targets/{id}` | Update any of `agent_id` (null unassigns), `enabled`, `methods`, `scan_dhcp_pool`, `interval_minutes` (null = manual only). Turning `scan_dhcp_pool` off deletes the pool's observations. |
| `DELETE /api/v1/scan-targets/{id}` | Delete the target; the subnet is kept, and so are its observations outside the DHCP pool. |
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

**Datetimes are UTC and carry their offset**, in every REST response and every MCP tool result: ISO 8601 with `+00:00` (`"2026-11-16T06:38:44.765279+00:00"`), the same string in the response to the write that created a row and in every later read of it. SQLite reads a stored datetime back without its zone, so the response models give it back (`schemas.UtcDatetime`). `last_seen`, the one datetime a client writes, is taken as UTC when sent without an offset (§3.4), and compared as an instant with the stored column (not the later of it and a scan's time, which is what a response shows), so re-sending it unchanged in another form does not move `updated_at`. A change's `before`/`after` snapshots carry the offset too; audit entries written before it did hold their datetimes without one, still UTC, and the UI reads an offset-less value as UTC. The agent routes keep their own shapes (§4.5, §4.6).

**Request bodies are capped at 2 MiB** (2,097,152 bytes, `body_limit.BODY_MAX`, the one place the limit is defined), on every route: owner, agent, and `/mcp` (§5.1). `BodyLimitMiddleware` wraps the whole app, so the cap runs before routing, before any authentication check and before any body is parsed. A larger body is `413 PAYLOAD_TOO_LARGE` "The request body may be at most 2097152 bytes", with `Connection: close`, whatever credentials it carries or lacks:

- a declared `Content-Length` over the cap is refused with nothing read; one within it is left to the route to read, and the HTTP server delivers no byte past it. The length is taken as the HTTP server passes it on: uvicorn's httptools parser, the one the image runs, accepts spaces or tabs after the digits and any number of leading zeroes, so both are dropped (`Content-Length: 2097153 ` and a length padded with 4,500 zeroes are refused unread like `2097153`, and a padded length within the cap is served), and the digits are compared with the cap by their count, never converted whole (#89). h11 strips the whitespace itself and refuses the zeroes with `400`;
- any other body is read and counted, and refused at the chunk that would pass the cap: one sent without a `Content-Length` (chunked), and any request with a `Transfer-Encoding` header whatever its `Content-Length` says, since a server that accepts both frames the body by the chunks (uvicorn's h11 does; httptools refuses such a request with `400`). One that fits is handed to the route whole, so up to 2 MiB of such a body is held before authentication refuses it, within the budget and the deadline below.

The limit was chosen from the largest legitimate requests (#60): an agent's result set for a full /20, 4,094 records each with a MAC and a 253-character PTR name, is about 1.3 MB (§4.6); a bulk import of 1,000 hosts, the most one import takes, with every length-bounded field at its maximum (253-character hostname and domain, 120-character owner of 4-byte characters) is about 1.15 MB as the UI encodes it. `notes` has no length limit of its own: on a full 1,000-row import the cap leaves at least 940 bytes of notes per row (about 1,300 with an ASCII owner), and an import past it is refused whole with the `413`, to be split. A client that escapes non-ASCII as `\uXXXX` (Python's `json` by default) spends 12 bytes on a 4-byte character, so its worst case is larger than the UI's.

**All the bodies being received are bounded together, and in time** (#90). The cap bounds one body, and one worker serves everything (§2.2), so the same middleware holds two more limits:

- **Budget.** The body bytes of every request in progress, whether the middleware or the route reads them, total at most 16 MiB (16,777,216 bytes, `body_limit.BODIES_MAX`, eight bodies at the cap). A chunk that would pass it is refused with `503 UNAVAILABLE` "The server is receiving too many request bodies; retry the request" and `Connection: close`, and a request gives its bytes back when it ends. Authentication does not end the count: FastAPI parses a `Body` before the route's dependencies run, so check-in and every owner route with a body read it, a declared length included, before the credential is checked, and a request that has authenticated still holds its body while it is served. The cap is checked first, so a body too large for any request is `413`, never `503`. What a body costs is its bytes, not the number of chunks it was sent in: the middleware keeps a body it reads in one buffer, and hands a route that reads its own body blocks of at least 64 KiB (the last excepted), so the route's list of them stays short (100,000 one-byte chunks took 8.9 MB for a 100 KB body before #90's review, under 0.4 MB after).
- **Deadline.** A body must arrive whole within 60 s of its request's headers (`body_limit.BODY_TIMEOUT_S`), with a declared length or without one; otherwise it is `408 REQUEST_TIMEOUT` "The request body was not received within 60 s", with `Connection: close`. A body trickled a chunk at a time is timed as a whole, not per chunk. Once a body is whole, the route's later reads (a streamed response waiting for the client to leave) are not timed. 60 s is twice the agent's own deadline for a whole request (30 s), so an agent always gives up first; it admits a body at the cap sent at 35 KB/s and a full result set at 21 KB/s.
- **The connection closes** with every refusal, and with any response sent before its request's body was received whole (a bearer refused before the body is read, as on `/mcp` and on results): uvicorn would otherwise keep the connection to read and discard the rest of the body for as long as the sender trickles it. When the middleware refuses a body a route is reading, its refusal is the answer and the route is told the client has gone.

Measured on #90 with 32 unauthenticated connections that each sent 1.75 MiB of a body and stalled, against a loopback uvicorn: before the budget and the deadline the worker grew by 68 MiB (chunked to `/mcp`) and by 60 to 63 MiB (a declared length to check-in), on both parsers. With them it held nine bodies (the 16 MiB), refused the other 23 at once (`503`, or a reset for a client still sending), grew by 17 to 21 MiB, refused the nine with `408` at 60 s, and answered `/health` throughout.

**Left to the reverse proxy or tunnel** (the deployment guidance, #31). The limits bound the memory bodies take, not who gets it. The budget is shared by every client, so one that keeps it full (16 MiB sent again each minute, from eight connections or more) holds every other body off with `503`, small ones included; the agent retries a `503` on its back-off. The server cannot tell clients apart behind a proxy, whose address is all it sees, and it cannot see what happens before a request's headers are complete. A proxy or tunnel in front of the server is expected to:

- limit connections and request rate per client;
- bound request headers in size and in time, and close idle connections: uvicorn calls the app only once the headers are complete, arms no timeout on a connection before its first response, and its httptools parser (the image's) sets no limit on header size (it took 8 MiB of one unfinished header; h11 refuses past 16 KiB);
- bound the connections a client holds open, since what each costs below the app grows with their number: uvicorn's own buffer (it stops reading once 64 KiB the app has not taken is waiting) and the kernel's socket buffers;
- pass a body of at least 2 MiB (nginx's default `client_max_body_size`, 1 MiB, is smaller than a full result set or a full import), preferably buffering it so that the server receives it at the proxy's speed, and give a client at least the time its slowest legitimate upload needs.

`503 UNAVAILABLE` means the request was not served and wrote nothing, and the same request may succeed if retried. Agent routes return it when the database refuses a write (§4.1): "Database busy; retry the request" for a lock, "Database unavailable; retry later" for anything else, which needs the owner. Any route returns it while the bodies being received fill the budget (above). `500 INTERNAL_ERROR` is a failure the server caught before committing anything (§4.6).

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

- **Rate limit first.** An agent may check in once every 10 seconds (`jobs.MIN_CHECKIN_GAP`; its own interval is 60). A check-in inside that gap is `429 RATE_LIMITED` before the body is looked at, and changes no row: not `last_checkin_at`, not the token's `last_used_at`, nothing in the change log. Inside the gap it is refused from the agent row loaded at authentication, with no write statement at all; check-ins that race are refused by the claim, a compare-and-set on `last_checkin_at` whose losing statements match no row. The agent waits `checkin_interval_s` and tries again.
- **The claim.** That compare-and-set is the check-in's first write, and it also requires the agent `active` and its token unrevoked: a disable, revoke or delete the owner commits after a check-in authenticated refuses it with `403` "Invalid agent token", and nothing of it is written. The order is fixed: a check-in whose agent row, as loaded at authentication, took its slot inside the gap is refused `429` from that row at once, with no statement at all, whatever the owner has committed since: a disable, revoke or delete that lands in that window is met by the agent's next request, which fails authentication with `403`. A check-in the loaded row lets through goes to the claim, which re-checks the agent's state and the token's revocation under SQLite's write lock: `403` if the owner's change has landed (even when another check-in has also taken the slot), otherwise `429` if one has. Nothing is served and nothing is written on any of them. The token's use (§3.9) is recorded in the claim's transaction, and the agent row is then read again whole, so the check-in's audit snapshots and `actor` carry the name and state as committed. Everything a check-in writes is that one transaction: if the database refuses a write, it is rolled back and the check-in gets `503 UNAVAILABLE` (§4.1).
- **Protocol first.** `protocol` is checked before anything else in the body, since another protocol may shape the rest differently. Anything but the integer `PROTOCOL_VERSION` (1) is `409 PROTOCOL_MISMATCH` with a message naming both protocols; it issues no job, stores none of the body, and is recorded in the agent's `last_error`.
- **Then the fields** (§3.8). The first that fails is `422 VALIDATION_ERROR` "Check-in refused: <field>: <reason>", recorded in `last_error` the same way; the reported fields keep their earlier values. Unknown fields are ignored. `capabilities` (up to 16 tokens of `a-z0-9_-`) is checked and not yet used.
- **A good check-in** stores the reported fields and `last_checkin_at`, clears `last_error`, and returns at most one job.
- **Audit.** A check-in that changes `version`, `platform`, `hostname`, `interfaces` or `last_error` writes one `agent` update with `source="agent"` and `actor="agent:<name>"`, naming the changed fields in the summary and their values only in the snapshots. A check-in that changes nothing else is not logged (as `last_used_at` is not), and neither is issuing a job. An agent keeps only its newest 100 entries of its own (`source="agent"`); older ones are deleted when a new one is written, and the owner's entries are never touched. Together with the rate limit, that bounds what one agent token can add to the log.
- **Agent text stays out of assistant context.** `list_changes` over MCP replaces the values of `version`, `platform`, `hostname` and `interfaces` in every `agent` snapshot with a placeholder (`changes.assistant_view`), whoever wrote the entry; the field names, summary, source and actor stay. Owner REST (`GET /api/v1/changes`) shows them.

**When a target is due.** It is enabled, assigned to this agent, not under a live lease, and either a scan was requested after its last scan started (`scan_requested_at` > `last_scan_at`), or it has an interval and has never been scanned or its last scan started at least `interval_minutes` ago. Among due targets, a request no job has been issued for since it was made goes first; every other due target, a request already tried once included, goes by when a job for it last started (`last_scan_at` or `job_issued_at`, whichever is later), oldest or never first. A target whose jobs keep timing out therefore goes to the back each time and cannot starve the agent's others; a request still stands until a completed scan serves it.

**Leases.** Issuing a job sets `current_job_id`, `job_issued_at` and `job_methods` (the methods issued). The lease is live for 15 minutes (the 10-minute job timeout plus a margin); a target under a live lease is not issued again, and once it expires the target is due again. The write is a compare-and-set on the lease the check-in read, so two check-ins never lease one target twice. The same lease is recorded on the agent (`agents.current_job_id`, `job_issued_at`), and an agent whose lease is live gets no new job: it runs one job at a time. Because the record is on the agent, reassigning, disabling or deleting the target (or its subnet) mid-scan does not free an agent that is still scanning; nothing is sent to cancel a job. A reassigned target keeps its lease until it expires or its result arrives, so the new agent waits for that target only.

An accepted result set clears both copies of the lease (§4.6). Expiry clears neither: the next job issued overwrites them, so a late result for an expired lease is still accepted until then.

**The job.** `ranges` is the subnet's scan scope from `services/scans.scan_scope` (§3.10); a target whose scope is empty gets no job. `methods` are the target's, minus `arp` when the subnet is not on the agent's link, and minus `fdns` until the forward-DNS check exists (#20), so `fdns` is always empty. The subnet is on the agent's link when one reported IPv4 interface's network contains the whole subnet. A target left with neither `icmp` nor `arp` gets no job, and is not leased. `dns_servers` are the first three of the subnet's, in the order listed, or empty: three is `agent_protocol.DNS_SERVERS_MAX`, the most the agent asks per lookup, so a subnet that lists more, or holds a longer list stored before the owner API bounded it (§3.2), costs the answer nothing. `timeout_s` is 600.

`cidr` is the subnet's in its canonical spelling (§3.2), at most 18 characters, whatever the subnet row holds: a subnet stored before #97 may hold any spelling `ipaddress` takes, a prefix padded with thousands of zeroes included, which the agent refuses past 64 characters, so such a subnet was never scanned.

**The answer fits the agent.** The agent reads at most 1 MiB of an answer and retries a larger one, so a job that made the answer larger would never run. The largest the server can build is a /20 target (the largest allowed) whose scope is split at every other address, issued `icmp`, `arp` and `rdns`, with three servers, whatever spelling of its CIDR and however many DNS servers the subnet stores: 104,978 bytes, and 106,762 with every address in it and the CIDR written at its longest (`agent/tests/test_answer_limit.py`, which fails if either side's bounds move it past the limit). Before the job's servers were bounded, the same subnet holding 65,000 of them sent 1,153,843 bytes; before the job's CIDR was canonical, the same subnet stored as `192.168.240.0/` with its prefix padded by 4,298 zeroes sent 109,276 bytes, past that bound, for a job the agent refused.

### 4.6 Scan results

`POST /api/v1/agent/results` (agent token, §4.1), one result set per job. Shapes in `api/app/agent_protocol.py`; the service is `services/results.ingest`.

```json
// request
{ "job_id": "…", "subnet_id": "…",
  "started_at": "2026-11-16T10:00:00Z", "finished_at": "2026-11-16T10:00:12Z", "complete": true,
  "observations": [{ "ip": "10.0.20.7", "mac": "00:1b:21:3a:4f:9c", "rdns_name": "nas.home.arpa" }],
  "fdns": [], "error": null }
// 200
{ "data": { "accepted": 1, "rejected": [{ "ip": "10.0.20.150", "reason": "out_of_scope" }] } }
```

- **Body.** At most 2 MiB, the cap on every request body (§4.3): a larger declared or streamed body is `413 PAYLOAD_TOO_LARGE`, refused before the agent token is checked and unparsed. A body with a declared length is read only after the token is checked. Not JSON, not an object, or a field that fails validation is `422` "Result set refused: <field>: <reason>", never quoting the value: `complete` must be a JSON boolean, both times must carry a zone and `finished_at` must not precede `started_at`, `observations` holds at most 4,096 records, `fdns` entries are `{host_id, addresses}`. Unknown fields are ignored. Refusals are not recorded on the agent: the agent logs them, and the lease runs out.
- **The credential, again.** Under SQLite's write lock, before anything is written, the agent must still be `active` and the token that authenticated the request still unrevoked. A result set that authenticated before the owner disabled, revoked or deleted the agent, or before a re-enrol exchange revoked its token, is `403` "Invalid agent token" and nothing is stored: the lease stays where it was, so a job issued under an old token runs out with its lease.
- **The job.** Accepted only while it is current on both copies of the lease: `job_id` equals the reporting agent's `current_job_id` and a target's `current_job_id`, whatever the target is assigned to now. Otherwise `409 JOB_NOT_CURRENT` and nothing is stored: the job was re-issued after expiry, the agent was given another, the target or its subnet was deleted, the token is another agent's, or a result set for the job was already accepted. A `subnet_id` that is not the job's is `422`. Accepting clears the target's `current_job_id`, `job_issued_at` and `job_methods` and the agent's `current_job_id` and `job_issued_at` in one transaction, each a compare-and-set on this job, so of two racing result sets one is accepted. It sets `last_scan_at` to the job's `job_issued_at`, records the duration, `complete` and `error` (§3.10), and leaves `scan_requested_at` alone, so a scan requested while the job ran is still due. The agent can be issued its next job at its next check-in.
- **Each record** is judged on its own and a bad one never costs the others. A record is rejected, with its `ip` quoted back when that is a short, ASCII, printable string (null otherwise: JSON can carry a lone surrogate that UTF-8 cannot), for: `invalid` (not an object), `invalid_ip`, `invalid_mac`, `out_of_scope` (outside the subnet's scan scope at ingest, another address family included), `duplicate` (already in this result set), `mac_not_issued` (a MAC from a job not issued `arp`), `rdns_not_issued` (a name from a job not issued `rdns`). What a job could collect is `job_methods`, the methods the server issued, not what the agent says it ran or a later check-in's interfaces. A rejected record is not stored. An `rdns_name` that is not a hostname is not a rejection: it is dropped, and the address is stored without it (§3.11).
- **Alive and dead.** Each accepted record is upserted `alive=true` with `last_seen_at` now. Only a result set with `complete: true` from a job issued `icmp` or `arp` marks every other observation in the subnet's scope `alive=false`; an address the result set named, even in a rejected record, is not marked. An incomplete job only adds what it saw.
- **Names.** From a job issued `rdns`, a record's name (after sanitising) replaces the stored one. A record with none clears it only when the result set is `complete`; an incomplete result set may report an address whose lookup never ran (the deadline, or no DNS server to ask), so it leaves the stored name.
- **`fdns`** is checked for shape and not stored: no job names a host until the forward-DNS check (#20), so no answer can be for one the job sent.
- **Prune.** Every ingest deletes observations, in any subnet, unseen for 90 days.
- **The answer before the commit.** The `200` answer is built and UTF-8-encoded before the result set is committed, so neither step can fail for a stored result set. That covers the answer's construction and encoding, not its delivery: rendering and sending the response come after the commit, and an agent that loses the response gets `409` on a retry. Should the encoding fail, the transaction is rolled back and the answer is `500 INTERNAL_ERROR` in the error shape, logged server-side.
- **Database failures.** The lease release, the observations, the prune and the token's use (§3.9) are one transaction. If the database refuses a write, it is rolled back and the result set gets `503 UNAVAILABLE` (§4.1): both lease copies still name the job, nothing is stored, and the same result set is accepted once the database is writable. A refused result set (`409`, `422`) writes nothing, the token's use included.
- **Nothing else.** Ingest never writes `hosts` and writes nothing to the change log.

### 4.7 Findings

Where what a scan saw and what the owner recorded disagree (v0.2 spec §5.5). Computed on read in `services/findings.py` from the subnet's hosts and observations, only for addresses inside its scan scope (§3.10): a host in an excluded range, or in an unscanned DHCP pool, raises none. Nothing is stored but dismissals (§3.12), and nothing is applied until the owner accepts it.

```json
// GET /api/v1/subnets/{id}/findings
{ "data": [{ "subnet_id": "…", "ip": "10.0.20.7", "type": "name_mismatch", "observed": "storage.home.arpa",
             "ip_int": 167777287, "host_id": "…", "dynamic": false, "proposed": { "hostname": "storage" },
             "fingerprint": "3f9c…" }],
  "total": 1 }
```

| Type | Raised when | `observed` | Accepting writes (`proposed`) |
|---|---|---|---|
| `unknown_host` | the observation is `alive` and no host is recorded at the address | the MAC, or empty | a new host: `mac`, and `hostname` (and `domain`, see Names) from the PTR name; `status` `active`, or `dhcp` when `dynamic` |
| `mac_missing` | the host has no MAC, the observation has one | the MAC | `mac` |
| `mac_mismatch` | both have one, and they differ | the MAC | `mac` |
| `name_missing` | the host has no hostname, the observation has a PTR name | the PTR name | `hostname`, and `domain` when the name is outside the effective domain |
| `name_mismatch` | the PTR name differs from the host's FQDN, case-insensitively | the PTR name | the same |
| `stale` | the host is `active`, the subnet has a scan target, and the host's `last_seen` (§3.4) is more than 30 days old | that `last_seen`, as an ISO-8601 UTC time, or `never` | `status` `offline` |

- **The key.** A finding is identified by `(subnet_id, ip, type, observed)`, and every finding carries all four. `ip_int` is the sort key, `host_id` the host at the address (null for `unknown_host`).
- **The fingerprint.** The key does not say everything an accept writes (an `unknown_host`'s name and status, a hostname that depends on the domain), so each finding also carries `fingerprint`, a digest the server computes from the key, `host_id` and `proposed`. Accept needs it; a later scan, a domain or pool edit, or a new host at the address changes it.
- **No MAC finding for a randomised MAC** (`mac_randomised`, §3.11).
- **Names.** Accepting a name makes the host's FQDN the PTR name. Under the effective domain (the host's override, else the subnet's), `hostname` is the part before it, nested labels included (`a.b.home.arpa` → `a.b`). Outside it, the bare domain included, `hostname` is the first label and `domain` the rest, as an explicit override in `proposed`: `nas.example.com` in a `home.arpa` subnet becomes `nas` + `example.com`, FQDN `nas.example.com`. A single-label PTR name is a relative hostname. `name_mismatch` compares FQDNs, so a PTR name outside the effective domain always differs.
- **Stale and never seen.** `last_seen` is the rule host responses use: the later of the value set by hand and the observation's `last_seen_at`, never `alive`. A host with neither is stale (`never`) only when both the scan target and the host record are more than 30 days old, so switching scanning on does not make every host stale. A subnet without a scan target raises no `stale`.
- **Dynamic.** An `unknown_host` inside the DHCP pool, possible only when the pool is scanned, has `dynamic: true` and is left out unless `?include_dynamic=true`.
- **Accept** takes each key with its `fingerprint`, recomputes the finding under SQLite's write lock and, only if it still exists with the same `observed` and `fingerprint`, writes `proposed` through the hosts service (`create_host`/`update_host`), whose validation and audit entry apply unchanged. Otherwise the record is `skipped` with reason `changed` and nothing is written; a key without a fingerprint is an `error`. A dismissed finding can still be accepted. The audit summary names the address and the finding type, never the hostname or MAC that came from the network: `host created at 10.0.20.7 in lab from scan finding unknown_host`, `host at 10.0.20.7 updated from scan finding name_mismatch`. The snapshots carry the values, as for any host write.
- **Dismiss** recomputes the same way and stores the key only for a finding that exists as listed (a fingerprint is checked if sent, and not stored). The finding stays hidden until its observed value changes. The audit entry's summary is a count (`2 scan finding(s) dismissed in lab`); the keys are in its `after`.
- **Observed values are data.** A MAC comes only from a job issued `arp`; a PTR name is whatever a device calls itself, kept only if it is a valid hostname (§3.11), which says nothing about what it means.
- **Audit summaries.** What reaches a change summary depends on the path a value takes. Scan ingest writes no change entry (§4.6). Accepting a finding (here, and in the web UI's findings panel) names the address and the finding type, never the network's hostname or MAC. An MCP host write (`create_host` or `update_host`, the tools an assistant resolves a finding with) is an ordinary owner write even when the assistant copied its values from a finding's `proposed`: once written, a hostname is owner data, and the summary names the host by it as every host summary does (`host nas created at 10.0.20.7 in lab`), which `list_changes` returns. The owner chose this (#79); the `create_host` and `update_host` docstrings say so to the assistant.
- Over MCP, `list_scan_findings` lists findings a page at a time, read-only (§5.2). Not yet: `fdns_mismatch` arrives with the forward-DNS check (#20); the findings panel is #19.

### 4.8 Agent enrolment

`POST /api/v1/agent/enrol` (v0.2 spec §4.2, §5.6, §5.7; `services/enrolment`) exchanges an enrolment token, presented as `Authorization: Bearer tlge_…`, for the agent's long-lived agent token. It is the only agent route reachable without a long-lived token, so it is not behind `require_agent` (§4.1). The body is `{version?, hostname?, platform?}`, each optional and checked as check-in checks it (trimmed, printable, at most 40, 253 and 120 characters; unknown fields ignored); `{}` is a valid body. The answer is `200 {"data": {"agent_id", "agent_token"}}`, the only response that carries the agent token.

- **What the exchange writes**, in one transaction: the enrolment token's `used_at` (a compare-and-set that requires it unused, unrevoked and unexpired at a time read once the exchange holds the write lock, after its body has arrived and its wait for the writer is over, and its agent not disabled, so two exchanges of one token cannot both pass and an owner's revoke, re-enrol or disable that commits first refuses it); the revocation of any agent token the agent held (re-enrol); the new agent token; the agent's `state` → `active`; and each report field sent (a field omitted or sent as null leaves the stored value as it is). It writes no `last_used_at` on either token: the new agent token's first is its first check-in's. It touches no scan target and no lease: the agent keeps its id, name and scan targets, and a job leased under the old token runs out with its lease (15 minutes), since the old token can no longer report it.
- **Audit**: three entries, or four on a re-enrol, all `source="agent"`, `actor` `agent:<name>`: the enrolment token `update` ("enrolment token tlge_… of agent X exchanged"), the old agent token's `revoke` ("… revoked (re-enrolled)") when there was one, the new token's `create` ("token tlga_… issued to agent X (enrolment)"), and the agent `update` ("agent X enrolled"). Summaries name the agent by its owner-given name and never a reported value; over MCP the reported values are withheld from the agent snapshots, as for a check-in's. The agent entry counts towards the cap on the agent's own entries (§4.5) and the exchange prunes to it in its own transaction; the token entries are not capped.
- **Refusals**, in the REST error shape, in this order: `429 RATE_LIMITED` while the throttle is full; `401 UNAUTHORIZED` with no bearer; `403 FORBIDDEN`, with one message, for any bearer that is not an enrolment token that can be exchanged now (unknown, used, expired, revoked, an agent token, the owner key, or one whose agent is disabled or deleted); `422 VALIDATION_ERROR` for a body that is not JSON or not that shape, naming the field and never the value, which leaves the token usable; `503 UNAVAILABLE` for a write the database refused, rolled back whole. The body is read only once the token is found usable. Nothing is written on any refusal.
- **Throttle**: at most 10 attempts that did not enrol in any 60 seconds, across every caller (`enrolment.THROTTLE_ATTEMPTS`, `THROTTLE_WINDOW_S`). Every refusal but the throttle's own counts; an attempt that enrols does not. While the window is full every attempt is `429` before its token is looked up or its body read, a good token included, and a throttled attempt does not lengthen the wait. Global because the route sits behind whatever proxy the owner runs, where a client's address means nothing; in memory, since the server is one process, so a restart empties it. A token has 256 random bits, so the throttle does not protect it from guessing: it bounds the work and the log noise of the one unauthenticated agent route. Anyone who can reach the route can keep the window full and so hold enrolment off; an agent already enrolled is not affected.
- **Until the agent enrols by itself** (#22, the container's first start and `tlg-ipam-agent install`), the exchange is made by hand: the Add agent dialog shows it as a `curl` command, and the container takes the agent token it prints as `IPAM_AGENT_TOKEN` (§6.2).

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

A request refused for its credentials gets FastMCP's RFC 6750 response, not the REST error envelope; one refused for its size gets the REST envelope, before its credentials are looked at:

| Request | Status | Response |
|---|---|---|
| No `Authorization: Bearer` header (none, `X-API-Key` only, or another scheme) | `401` | Empty body; `WWW-Authenticate: Bearer` |
| A bearer that is not the owner key | `401` | `WWW-Authenticate: Bearer error="invalid_token", error_description="…"`; JSON body `{"error": "invalid_token", "error_description": "…"}` |
| A body over 2 MiB, with or without a bearer | `413` | `{"error": {"code": "PAYLOAD_TOO_LARGE", "message": "…"}}`; `Connection: close` (§4.3) |
| A body that would take the bodies being received past 16 MiB, with or without a bearer | `503` | `{"error": {"code": "UNAVAILABLE", "message": "…"}}`; `Connection: close` (§4.3) |
| A body not received whole within 60 s (a chunked one before the bearer is looked at; one with a declared length once it is accepted) | `408` | `{"error": {"code": "REQUEST_TIMEOUT", "message": "…"}}`; `Connection: close` (§4.3) |

Both `/mcp` and `/mcp/` behave the same.

**`/mcp` is under the request-body cap (§4.3), not exempt from it.** A tool call carries its arguments in the body. The largest is `bulk_create_hosts` with 1,000 rows, the same batch as the REST import plus the JSON-RPC envelope, and the measured worst case fits (`test_the_largest_mcp_bulk_call_is_served`); every other tool takes one host, one range or a few ids. The cap is checked before the bearer, so an oversized body is `413` even without one, and so are the budget and the deadline (§4.3) for a body without a declared length; a refused bearer's `401` closes the connection, so a body sent after it is never read. The MCP SDK has a 4 MiB limit of its own, but it sits behind FastMCP's authentication and so bounds only clients that already hold a credential; `/mcp` is the route that becomes internet-facing with remote MCP (v0.2 M3), where a limit that holds before authentication matters most.

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
| `get_host` | Full detail for a specific host record including MAC, device type, owner, and last seen: `last_seen` is the later of the manual value and the scan's, and `last_seen_source` (`manual`, `scan` or null) says which (§3.4). `list_hosts` and every tool that returns a host return the same shape. |
| `create_host` | Register a new host at a specific IP address. Validates that it is assignable within the target subnet and not already allocated. Manual placement at the gateway or inside DHCP/reserved ranges is allowed. The docstring says that values copied from a finding are what a device reported, to be written only when the user asks, and that once written they are the user's host record, named in the change summary like any other (§4.7, Audit summaries). |
| `bulk_create_hosts` | Create many hosts in one subnet in a single call (e.g. importing devices from a UniFi export). Per-record results; duplicates skipped, bad records errored, batch never aborts. One audit entry. |
| `allocate_next_ip` | Allocate the next available IP in a subnet and create a host record in one step. Use when the caller does not need a specific IP address. |
| `update_host` | Update hostname, domain override, MAC address, status, device type, owner, or notes for an existing host record. Pass nullable field names in `clear_fields` to clear them; clearing a domain override restores subnet-domain inheritance. Clearing `last_seen` removes only the manual value: a host whose address a scan has seen still shows the scan time, with `last_seen_source: scan`. Its docstring says what `create_host`'s does about values copied from a finding. |
| `delete_host` | Remove a host record, freeing the IP address for future allocation. |
| `bulk_delete_hosts` | Delete many hosts by ID in one call (cleanup workflows). Returns deleted count + not-found IDs. |
| `add_reserved_range` | Reserve a block of IPs in a subnet so automatic allocation skips it (manual creation inside it stays allowed). Always creates a scanned range: the tool has no `scan_excluded` argument. |
| `delete_reserved_range` | Remove a reserved range, returning its addresses to the allocation pool. Refuses a range with `scan_excluded`, because removing it would widen what is scanned; the error says to remove it in the web UI. |
| `list_changes` | Recent changes to the IPAM (audit log), newest first, each with its `source` (`ui`, `api`, `mcp` or `agent`) and `actor` (null for the owner's own writes, `agent:<name>` for an agent). In `agent` snapshots the values the agent reports about itself are withheld (§4.5). Use to answer "what changed recently?" or to review previous MCP-driven edits. |
| `list_scan_findings` | Scan findings (§4.7), for one subnet or, with `subnet_id` omitted, every subnet (by subnet name, then address), optionally of one `type`, a page at a time: `list_scan_findings(subnet_id?, type?, cursor?)` returns `{next_cursor, returned, note, findings}`, at most 25 findings (`findings.PAGE_SIZE`) and at most 45,000 bytes of them as compact UTF-8 JSON (`findings.PAGE_BUDGET`), because a domain suffix has no length limit of its own. A finding larger than the budget comes alone; the cursor and note come first so that a client that cuts a long result keeps them. `next_cursor` is null on the last page; otherwise the assistant calls again with the same `subnet_id` and `type` and `cursor` set to it, and the docstring says not to call the list complete until then. The cursor is the position of the page's last finding (subnet name, CIDR and id, address, type) as an opaque string, so findings resolved between calls move nothing later in the order. A cursor that is not well-formed is refused (`cursor: malformed; …`); the check is of its shape only, because the cursor is unsigned and a well-formed position a client made up is read as a position. Each call reads the data as it is then: following the cursor sees each finding once while the data and the filters stay the same, but a finding that appears before the cursor after paging began, or a subnet renamed meanwhile, can be missed or repeated, and the docstring tells the assistant to start again without a cursor to see the current state. Both bounds are set against what a client can hold: Claude Code keeps at most 50,000 characters of a text result and 25,000 tokens. An ordinary page is about 10,000 characters, and a page of 25 findings with every name and the domain at 253 characters is 44,057. `note` is on every page. Each finding is the REST one (key, `ip_int`, `host_id`, `dynamic`, `proposed`, `fingerprint`) plus `subnet_name`, `subnet_cidr` (in its canonical spelling, §3.2, as is the cursor's CIDR, whatever the subnet stores), and the recorded `host_name` and `host_fqdn` of the host at the address (null for `unknown_host`). `dynamic` findings are left out. Beside the list, `note` says that `observed` and `proposed` are what devices reported about themselves, to be read as data; the docstring says so too, and tells the assistant to write one into a host only when the user asks. Read-only. There is no accept or dismiss tool: an assistant resolves a finding with `create_host`/`update_host`, which the fingerprint does not bind, and dismissal stays in the UI. |
| `request_scan` | Ask for a scan of one subnet now (`POST /subnets/{id}/scan`, §4.2), logged with `source = mcp`. Returns `{"scan_request": {subnet_id, scan_requested_at, next_request_after}}`, not the target. Inside the 15-minute cooldown, which every caller shares, the tool error says when to ask again, rounded up to the second so that asking then succeeds (the REST `429` gives the same time): `A scan of subnet lab was requested less than 15 minutes ago; request another after 2026-11-16T12:15:00+00:00`. A subnet with no scan target, a disabled one, or one with no agent is refused, and so is an unknown subnet. A write tool: when remote grants arrive (M3), it needs `ipam:write`. |

All MCP writes are recorded in the change log with `source = mcp`. No tool creates, configures or lists agents, tokens or scan targets, and no tool can widen a subnet's scan scope (v0.2 spec §7.2, invariant 2). `request_scan` sets one column of a configured target, `scan_requested_at`, so the agent scans the scope the owner set sooner; it changes no setting and reveals none.

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
- "Is anything on the network I haven't recorded?" → `list_scan_findings` with `type=unknown_host`
- "Rescan the IoT subnet" → `list_subnets` → `request_scan`

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
| Host table | Virtualised if >200 rows. Columns: checkbox multi-select (with select-all), IP (sortable), Hostname, Device type icon, Status badge, MAC, Last seen. Selection enables "Delete N selected" with confirm. Last seen is the host's `last_seen` read as UTC, its tooltip saying whether a scan saw it or it was set by hand (`last_seen_source`). On the subnet view a host outside the subnet's scan scope reads "not scanned", after the date when one was set by hand: every host of a subnet with no scan target, and in a scanned subnet a host whose `ip_int` lies in none of the target's `scope` ranges by their `start_int` and `end_int` (one in an unscanned DHCP pool, or in a range excluded from scanning). The UI never works scope out and parses no address; it compares the integers the server sent. While the targets have not loaded, or failed to, or a range lacks its integers, the column makes no claim. |
| Status badges | `active`=green, `reserved`=amber, `dhcp`=blue, `offline`=slate. |
| Device type icons | Per-type icon chosen from a curated Lucide set (~28 icons), user-assignable in Settings. |
| Empty states | Helpful, not apologetic. e.g. "No hosts yet — add one or allocate the next free IP." |
| Inline editing | Click any host row to open a slide-over drawer. No separate edit page. |

### 6.2 Views

- **Dashboard** — summary stats: total subnets, total hosts, hosts by status, plus a **recent activity feed** driven by the change log (shows source: UI / API / MCP, and the actor when set)
- **Section view** — click section in sidebar; shows its subnets with utilisation
- **Subnet view** — click subnet; shows host table + free IP count + VLAN/gateway/domain/DHCP-pool info header, with reserved ranges shown as removable chips + a "Reserve range" action. Each chip has a **Scan** checkbox (`.tlg-check`), ticked unless the range has `scan_excluded`; unticking it `PATCH`es `scan_excluded: true` and the chip reads "not scanned". The "Reserve range" dialog has the same choice, ticked by default (v0.2 spec §5.3, §5.8)
- **Scanning section** (subnet view, v0.2 spec §5.8) — a summary of the subnet's scan target (agent, interval or "on request only", methods, whether the DHCP pool is included; "Not scanned." when it has none), the last scan's time and duration (read as UTC, marked "incomplete" when the job did not finish, with the agent's `last_scan_error` when it reported one), whether a scan was requested or a job is out, and the scanned addresses as the server reports them in the target's `scope`: the UI never works scope out. **Settings** (or **Set up scanning** for a subnet with no target) edits the agent (or unassigned), the methods, the interval in minutes (blank = manual only), enabled, and **Scan DHCP pool**, off by default and disabled when the subnet has no pool; it `PUT`s (or `POST`s) `/scan-targets` and shows the server's refusal inline. **Scan now** calls `POST /subnets/{id}/scan` and shows the server's answer, including the `429 COOLDOWN` message with the time another request is accepted. What the target's agent can run comes from the target's `runnable_methods` (§3.10), whether or not a job is out: with `arp` among the methods and missing from it, the section says "MAC collection unavailable — agent is not on this subnet"; when it is empty, that the target cannot be scanned by this agent. With no agent, or one that has never checked in, it says neither. Beside either message it says when the agent reported the interfaces it is judged from, from the same answer (`agent_checkin_at`, read through `parseServerTime`): "Based on the interfaces the agent reported 5m ago", or, when that last check-in was refused, that they are from before it. With neither message it says the same only when the report is not recent (the last check-in refused, older than the Agents page's three-minute online window, or dated ahead), since the absence of a warning rests on the same evidence. A time more than one check-in interval (60 s) ahead of the browser's clock (a browser clock behind the server's, or a server clock set back after the check-in) is not read as an age, by the Agents page's rule (`agents.serverAge`): the line stays and gives the time itself, "at <local time>, ahead of this browser’s clock", never "0s ago", and the report is not recent. A time less far ahead is ordinary drift and reads as now. The error the agent reported with its last result set (`last_scan_error`) shows beside the last scan time, as text only: it is agent-written. After any of these writes the view reloads the subnet and calls `refreshTree()`, since subnets in the tree carry `scan_dhcp_pool` and each range's `scan_excluded`; the view reloads even when the tree reload fails, which is reported as such and not as a failed write. The target reloads every 30 seconds while the view is open, so a job an agent picks up shows without a page reload. Moving to another subnet closes the settings form and discards its unsaved changes
- **Findings** (subnet view, v0.2 spec §5.5, §5.8) — a count badge on the subnet's title row (shown when there is at least one finding; it scrolls to the panel), and a **Findings** panel, shown when the subnet has a scan target or findings. The panel lists `GET /subnets/{id}/findings` grouped by type in a fixed order (unknown hosts, MAC missing, MAC mismatch, name missing, name mismatch, stale), each row with the address, what the record has, what the scan saw, and what accepting writes (`proposed`). A proposal with a `domain` says it sets a domain override, the FQDN that results, and the domain the host follows now. Each row has an accept button named for what it does (Create host, Set MAC, Replace MAC, Set hostname, Replace hostname, Mark offline) and **Dismiss**; a type with more than one finding has **Accept all N**, with a confirmation that counts the domain overrides among them. Accept and dismiss send each finding's key with its `fingerprint`, at most 1,000 to a request, and the panel then shows the per-record answer until it is closed: how many were done, and each record skipped or in error with the server's reason (for `changed`: nothing was written, and the reloaded list shows the finding as it is now). A request refused with a 4xx wrote nothing, and its findings read "not sent"; one that failed otherwise (a 5xx, or no answer) reads "unknown", because accept commits each record on its own, and the view reloads as it does after a write. After an accept the view reloads the hosts, the subnet and the tree; after a dismiss, the findings. **Show dynamic** (`.tlg-check`) appears only while pool scanning is on, and asks for `include_dynamic`; dynamic findings are marked as such. A finished scan (a new `last_scan_at` on the 30-second target reload) reloads the hosts and the findings. A reload that fails keeps the last list and says it may be out of date, for the findings, the host list and the scan target alike. Once shown, the panel stays for that subnet, so its last answer stays on screen when the last finding of a subnet with no scan target goes. What a finding shows came from the network and is rendered as text only
- **Host drawer** — slide-over on row click; all fields editable (domain field shows the inherited subnet default as placeholder; blank = inherit); delete button with confirmation; the last-seen time read as UTC, with whether a scan saw it or it was set by hand
- **Allocate modal** — triggered by "Allocate next free IP" button; prefills next IP; hostname required
- **CSV import modal** — on the subnet view; file picker or paste, header-mapped columns, per-row results shown after import (same bulk endpoint as MCP)
- **Agents** (`/agents`, v0.2 spec §5.8) — one row per scan agent: a status dot from `last_checkin_at` (online within three check-in intervals, late within 15 minutes, then offline; "never checked in" and "disabled" show as such; a check-in dated more than one interval, 60 s, ahead of the browser's clock is "clocks disagree", with the time itself in place of an age, since how fresh it is is not known: the same rule, `agents.serverAge`, as the Scanning section's), the reported version, hostname, platform and interfaces, the subnets its scan targets cover ("unknown" while the scan targets have not loaded or failed to load, never "none"), and its token by display prefix ("not enrolled" for an agent that has never held an agent token, "revoked" for one that has lost it). A refused check-in shows the agent's `last_error`. Agent-reported text is rendered as text only. The list refreshes every 30 seconds. **Add agent** names the agent and then shows its `tlge_…` enrolment token once, with when it expires and two commands: the exchange by hand (`curl -fsS -X POST <this origin>/api/v1/agent/enrol -H 'Authorization: Bearer tlge_…' -H 'Content-Type: application/json' -d '{}'`, §4.8), whose answer carries the agent token, and the container command to run the agent with it (`docker run … --network host --cap-add NET_RAW -e IPAM_URL=<this origin> -e IPAM_AGENT_TOKEN=<agent token> ghcr.io/deusmaximus/tlg-ipam-agent:<server version>`, spec §5.6), with `<agent token>` left for the operator to fill in. Both commands use the page's origin only when it is `https`, or plain `http` on a loopback host (the agent's own rule for `IPAM_URL`, `agents.agentServerUrl`); a page served over plain `http` from any other host gets `https://<server>` in its place and a warning, so no command it shows sends a token across the network in clear. This is the interim install until the agent exchanges the token itself (#22) and the dialog becomes the guided install (#23). The token lives only in that dialog: nothing closes the dialog while the create request is in flight, a backdrop click does not close the token step, the list reloads from `GET /agents` (which never carries a token), and once the dialog closes nothing on the page can show the token again. Row actions: Disable / Enable (`PUT` `enabled`), Revoke (enabled while the agent holds a live token, its agent token or an enrolment token it has not exchanged; with a confirmation that says a revoked token cannot be reissued, so the agent must be deleted and added again), Delete (with a confirmation naming how many subnets are left unassigned)
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
│   ├── tests/                # pytest — API, MCP, validation, allocation, backup, check-in and results coverage
│   └── app/
│       ├── main.py           # FastAPI app + static SPA serving + MCP mount
│       ├── config.py         # env vars (pydantic-settings)
│       ├── database.py       # SQLAlchemy async engine + session
│       ├── models.py         # ORM models (Section, Subnet, ReservedRange, Host, DeviceType, Change, Agent, Token, ScanTarget, Observation, FindingDismissal)
│       ├── schemas.py        # Pydantic request/response schemas
│       ├── agent_protocol.py # the agent wire contract: enrolment, check-in, job and result-set shapes, token prefixes, PROTOCOL_VERSION, sanitising agent text
│       ├── auth.py           # owner-key check (REST dependency + /mcp bearer verifier); agent-token dependency
│       ├── body_limit.py     # the request-body limits (cap, budget, deadline), outermost middleware, every route and /mcp
│       ├── mcp_server.py     # FastMCP tools (thin wrappers over services)
│       ├── services/         # business logic — single source of truth
│       │   ├── backups.py    # consistent SQLite online-backup snapshots
│       │   ├── sections.py
│       │   ├── subnets.py    # incl. free-IP + next-free allocation logic
│       │   ├── hosts.py
│       │   ├── device_types.py
│       │   ├── agents.py     # agents, their tokens (hashed), enrolment tokens and re-enrol, agent-token authentication
│       │   ├── enrolment.py  # the enrol exchange and its throttle
│       │   ├── scans.py      # scan targets; scan_scope, the one place scan scope is computed; request-scan and its cooldown; deleting observations that leave scope
│       │   ├── jobs.py       # agent check-in, due targets, job leases
│       │   ├── results.py    # scan results ingest: observations, clearing the lease
│       │   ├── findings.py   # scan findings computed on read; accept (through hosts) and dismiss
│       │   └── changes.py    # audit log writes + reads
│       └── routers/
│           ├── sections.py
│           ├── subnets.py
│           ├── hosts.py
│           ├── device_types.py
│           ├── agents.py     # owner CRUD on /api/v1/agents
│           ├── agent.py      # agent-facing /api/v1/agent/* (agent token only; enrol takes an enrolment token)
│           ├── scan_targets.py # owner CRUD on /api/v1/scan-targets
│           ├── findings.py   # /api/v1/subnets/{id}/findings, /api/v1/findings/accept and /dismiss
│           └── changes.py
├── agent/                   # the scan agent (v0.2 spec §5.6): its own package, image and tests
│   ├── pyproject.toml       # package `tlg-ipam-agent`, entry point `tlg-ipam-agent`
│   ├── Dockerfile           # the agent image: host networking, CAP_NET_RAW
│   ├── requirements.in / requirements-dev.in    # httpx, icmplib, dnspython; tests add the server's set
│   ├── requirements.txt / requirements-dev.txt  # generated locks, constrained to api/requirements-dev.txt
│   ├── tests/               # fake server and prober; end-to-end against the real routes; contract checks against agent_protocol
│   └── src/tlg_ipam_agent/
│       ├── cli.py           # entry point: configuration, signals, the loop
│       ├── config.py        # IPAM_URL (https), IPAM_AGENT_TOKEN, ALLOWED_CIDRS, MAX_PPS
│       ├── protocol.py      # the agent's copy of the wire contract; parse_job
│       ├── client.py        # HTTP to /api/v1/agent/*, TLS always verified; answers sorted OK / RETRY / REFUSED
│       ├── backoff.py       # the refusal rule of spec §5.7
│       ├── loop.py          # check-in loop, the one-at-a-time job worker, reporting results
│       ├── guard.py         # ALLOWED_CIDRS and range checks, before any packet
│       ├── scan.py          # one job: ICMP, ARP and PTR, paced by one budget (Pacer); the timeout
│       ├── probe.py         # the only code that sends packets: ICMP echo (icmplib), PTR (dnspython)
│       └── interfaces.py    # the host's addresses from rtnetlink, for the check-in
└── web/
    ├── package.json
    ├── eslint.config.js
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── api/             # fetch wrappers per resource
        ├── components/      # Sidebar, HostTable, HostDrawer, AllocateModal, AddAgentModal, ScanSettings, FindingsPanel, etc.
        ├── utils/           # csv.js; agents.js (status dot, server-time parsing, agent container command); scans.js (scanning settings form, last-scan display); findings.js (findings grouping and per-record outcomes, last-seen and scan-scope display)
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
- An agent's `tlga_…` token appears in exactly one response, the enrol exchange's, and an enrolment token (`tlge_…`) in exactly one, the create or re-enrol response that issued it; each is stored only as its SHA-256 and display prefix. Each token's create, use and revoke has a change-log entry.
- A used or expired enrolment token is refused; after a re-enrol exchange the agent's old agent token is refused and its scan targets are unchanged; the enrol throttle engages after repeated bad tokens (`api/tests/test_enrolment.py`).
- The owner key is refused on agent routes; an agent token is refused on owner routes and on `/mcp`. A revoked token, and the token of a disabled agent, are refused.
- A subnet's scan scope leaves out network and broadcast, the DHCP pool unless pool scanning is on, and every excluded reserved range; an address in both an excluded and a scanned range is excluded. Ranges from a database made before scan exclusion existed read as scanned.
- Scan targets are refused for prefixes shorter than `/20` and for method sets without `icmp` or `arp`. Deleting a subnet deletes its scan target; deleting an agent unassigns its targets. Every scan-target and scan-exclusion change is in the change log.
- A scan target is issued to its agent at check-in when its interval has passed or a scan was requested, and is not issued again while its lease is live; an expired lease makes it due again. A job's ranges are the scan scope, and `arp` is dropped for a subnet that is not on the agent's link; a target with no liveness method left gets no job.
- A check-in on another protocol gets no jobs and a `409 PROTOCOL_MISMATCH`, recorded in the agent's `last_error`. Agent-reported fields are length-capped and printable-only; a check-in that breaks either is refused and recorded the same way.
- A subnet's `dns_servers` over 8 entries is refused on write, naming the limit, and a longer list stored before the bound survives an unrelated edit. A job carries at most three servers and its CIDR in its canonical spelling, whatever the subnet stores, and the largest check-in answer the server can build is inside the agent's 1 MiB answer limit.
- A subnet's CIDR is stored in its canonical spelling whatever was typed; another spelling of a network already in the section is refused as a duplicate; a subnet stored with another spelling before #97 is read as stored, can be edited, and is scanned.
- A second scan request for a subnet within 15 minutes is refused, whoever asks.
- An agent checks in at most once every 10 seconds, and keeps at most 100 audit entries of its own; text it reports about itself never reaches an assistant through `list_changes`. A target whose jobs keep timing out does not starve the agent's other due targets.
- Scan results: an out-of-scope address, a wrong job id and a second result set are each rejected without affecting valid records; an incomplete job adds what it saw, marks nothing dead and clears no stored name; a hostile `rdns_name` (spaces, markup, instructions) is dropped; deleting a subnet with observations succeeds. Ingest writes no host and no change-log entry, and observations that leave scan scope are deleted with the change that narrows it.
- Findings: each type is raised from hosts and observations inside scan scope only, with no MAC finding for a randomised MAC. Accepting one whose observed value has changed writes nothing and reports `skipped`; an accepted one is written through the host paths and appears in the change log with no network-reported value in its summary. A dismissed finding stays hidden until its observed value changes, and dismissals go when their address leaves scope or the subnet is deleted.

### Testing
- pytest covers CIDR/IP validation, CIDR child invariants, explicit-null updates, the overlap rule, interval maths, consistent backups, MCP clearing, and next-free allocation edge cases.
- ESLint, Vitest component tests, and the production frontend build run in CI alongside pytest, with a check that the dependency locks match their `.in` files.
- The scan agent's tests run in CI with no packet sent: a fake server and a fake prober drive the loop (refusals, back-off bounds, the allowlist, the timeout), a fake ARP link reads the agent's real request frames and answers with real reply frames, malformed and unsolicited ones among them, its client runs against the real check-in and results routes in process, a loopback TLS server checks that verification cannot be skipped, loopback DNS responders check the arrival times of real queries against `MAX_PPS`, fake clocks that wake late or early check that no second holds more than `MAX_PPS` sends across the three methods and from one job into the next, a send that never waits checks that the rest of the agent still runs, a loopback HTTP server that drips its answer checks the request deadline, and the image is built and started with no network.

### MCP integration
- Claude Desktop connects via `mcp-remote` with the owner key as a bearer and lists all available tools.
- `/mcp` answers `401` to a missing or wrong bearer, and to `X-API-Key` alone.
- All 18 MCP tools are callable from Claude Desktop.
- Bulk import reports per-record outcomes; a batch with some bad rows still imports the good ones.
- No MCP tool configures agents, scan targets or scan scope. `add_reserved_range` creates a scanned range; `delete_reserved_range` on an excluded range fails with a message pointing at the web UI; `get_subnet` shows `scan_excluded` and `scan_dhcp_pool`.
- `list_scan_findings` and `request_scan` are in `EXPECTED_TOOLS` and the tool table. `request_scan` inside the cooldown returns a tool error that says when it can run again.
- Natural-language queries resolve to correct tool calls without ambiguity.

### Operations
- `docker compose up --build` produces a running stack with no manual steps.
- All data persists across container restarts (named volume).
- API key changeable via env var without rebuild. The server refuses to start with `API_KEY` unset, empty, or `change-me` (§4.1).
- Settings exposes a SQLite backup download created through the online backup API rather than streaming the active database file.
- `docs/backup-and-restore.md` describes a scheduled pull of `GET /api/v1/backup` from another host (dated files, retention) and a restore procedure; following it against a running instance produces a dated snapshot and a working restore from it. Its examples use placeholder domains only.
- The container runs one uvicorn worker, pinned by `--workers 1` in the image's `CMD` (§2.2).
- A request body over 2 MiB is `413 PAYLOAD_TOO_LARGE` on every route, `/mcp` included, before authentication or parsing, with a declared `Content-Length` and without one (§4.3).
- The bodies of the requests in progress hold at most 16 MiB together (`503` past it), a body not received whole within 60 s is `408`, and both close the connection, on every route (§4.3).

### UI quality
- Dark mode correct — no hardcoded colours bleeding through.
- Works at 1280px and 1920px wide.
- Empty states present on all list views.
- Host IP sorting is numeric and keyboard accessible; inventories above 200 rows use windowed rendering.
- No console errors in production build.

---

*TLG-IPAM Project Brief · Built for Claude Fable*
