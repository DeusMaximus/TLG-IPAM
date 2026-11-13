"""Curated MCP tools for TLG-IPAM.

Tools are thin wrappers over the services layer — the same code paths as the REST
API — so behaviour never drifts. Descriptions are what Claude reads at runtime to
pick tools: keep them precise about when to use each one.
"""

import functools

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from .auth import OwnerKeyVerifier
from .database import SessionLocal
from .errors import ApiError
from .schemas import (
    AllocateRequest,
    ChangeOut,
    HostClearField,
    HostCreate,
    HostUpdate,
    ReservedRangeCreate,
    SectionOut,
)
from .services import changes as changes_svc
from .services import device_types as device_types_svc
from .services import hosts as hosts_svc
from .services import sections as sections_svc
from .services import subnets as subnets_svc

mcp = FastMCP(
    "TLG-IPAM",
    instructions=(
        "TLG-IPAM is the user's standalone homelab IP address manager — the "
        "authoritative record of their sections (network segments), subnets, and "
        "host/IP assignments. It is not phpIPAM, NetBox, or a UniFi controller; use "
        "these tools directly for any query or change involving the user's IP "
        "addresses, subnets, VLANs, hostnames, or MAC addresses. Writes are audited; "
        "use list_changes to review what was done."
    ),
    # Bearer auth on the HTTP app (`/mcp`). In-process clients, as in the tests,
    # skip it: FastMCP applies it only at the HTTP layer.
    auth=OwnerKeyVerifier(),
)


def ipam_tool(fn):
    """Register an MCP tool, translating domain/validation errors into ToolErrors
    so Claude sees the actual message instead of a masked internal error."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ApiError as exc:
            raise ToolError(exc.message) from None
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            raise ToolError(f"{field}: {first['msg']}") from None

    return mcp.tool(wrapper)


# shared serialisers: same shapes as the REST API
_host = hosts_svc.host_out
_subnet = subnets_svc.subnet_out


@ipam_tool
async def list_sections() -> dict:
    """List all network sections/segments. Use to get an overview of how the network
    is organised before querying subnets."""
    async with SessionLocal() as session:
        rows = await sections_svc.list_sections(session)
        return {
            "sections": [
                SectionOut.model_validate(s).model_copy(update={"subnet_count": c}).model_dump()
                for s, c in rows
            ]
        }


@ipam_tool
async def list_device_types() -> dict:
    """List the device types defined in this IPAM, with usage counts. The
    device_type value passed to create_host, update_host, allocate_next_ip, and
    bulk_create_hosts must match one of these names (matched case-insensitively).
    The default set is server, vm, lxc, network, iot, desktop, laptop, phone, other — the
    user can add or rename types in Settings, so check here when unsure."""
    async with SessionLocal() as session:
        rows = await device_types_svc.list_with_counts(session)
        return {
            "device_types": [
                {"name": dt.name, "icon": dt.icon, "hosts": count} for dt, count in rows
            ]
        }


@ipam_tool
async def add_reserved_range(
    subnet_id: str, start_ip: str, end_ip: str, description: str | None = None
) -> dict:
    """Reserve a block of IPs in a subnet so automatic allocation skips it — e.g. a
    range held for network gear that is not installed yet. Hosts can still be created
    inside the range manually with create_host."""
    payload = ReservedRangeCreate(start_ip=start_ip, end_ip=end_ip, description=description)
    async with SessionLocal() as session:
        block = await subnets_svc.create_reserved_range(session, subnet_id, payload, source="mcp")
        return {
            "reserved_range": {
                "id": block.id,
                "start_ip": block.start_ip,
                "end_ip": block.end_ip,
                "description": block.description,
            }
        }


@ipam_tool
async def delete_reserved_range(subnet_id: str, range_id: str) -> dict:
    """Remove a reserved range from a subnet, returning its addresses to the automatic
    allocation pool. Find range IDs via get_subnet (reserved_ranges field)."""
    async with SessionLocal() as session:
        await subnets_svc.delete_reserved_range(session, subnet_id, range_id, source="mcp")
        return {"deleted": True}


@ipam_tool
async def list_subnets(section_id: str | None = None) -> dict:
    """List subnets, optionally filtered by section. Returns CIDR, VLAN ID, name and
    utilisation (used/usable/free) for every subnet in one call."""
    async with SessionLocal() as session:
        subnets = await subnets_svc.list_subnets(session, section_id)
        return {"subnets": await subnets_svc.subnets_out(session, subnets)}


@ipam_tool
async def get_subnet(subnet_id: str) -> dict:
    """Get full detail for a subnet: VLAN tag, gateway, DNS servers, DHCP pool,
    default domain suffix, reserved ranges, notes, and utilisation including the
    count of free (allocatable) IPs — no follow-up calls needed for an overview."""
    async with SessionLocal() as session:
        subnet = await subnets_svc.get_subnet(session, subnet_id)
        return {"subnet": await _subnet(session, subnet)}


@ipam_tool
async def get_free_ips(subnet_id: str, limit: int = 20) -> dict:
    """Return unallocated IP addresses in a subnet, in ascending order. Respects
    reservations: the network/broadcast addresses, the gateway, the DHCP pool, and
    reserved ranges are never returned. Use before allocating to check availability
    or pick a specific address."""
    async with SessionLocal() as session:
        ips = await subnets_svc.free_ips(session, subnet_id, limit=limit)
        return {"free_ips": ips, "returned": len(ips)}


@ipam_tool
async def list_hosts(
    q: str | None = None, subnet_id: str | None = None, status: str | None = None
) -> dict:
    """Search and filter host records across the whole network. `q` is a free-text
    match against hostname, IP, and MAC. `status` is one of: active, reserved, dhcp,
    offline. Use to answer "what device is at X?" or "show me all offline hosts"."""
    async with SessionLocal() as session:
        hosts = await hosts_svc.list_hosts(session, subnet_id=subnet_id, status=status, q=q)
        return {"hosts": await hosts_svc.hosts_out(session, hosts), "total": len(hosts)}


@ipam_tool
async def get_host(host_id: str) -> dict:
    """Get full detail for a specific host record, including MAC address, device type,
    owner, notes, and last-seen timestamp."""
    async with SessionLocal() as session:
        return {"host": await _host(session, await hosts_svc.get_host(session, host_id))}


@ipam_tool
async def create_host(
    subnet_id: str,
    ip: str,
    hostname: str | None = None,
    domain: str | None = None,
    mac: str | None = None,
    device_type: str = "other",
    status: str = "active",
    owner: str | None = None,
    notes: str | None = None,
) -> dict:
    """Register a new host at a specific IP address. Validates that the IP falls within
    the target subnet, is not the network/broadcast address, and is not already
    allocated. The IP may be inside a reserved range or the DHCP pool (manual
    registration is allowed there). domain overrides the subnet's default domain
    suffix; leave unset to inherit it.
    device_type: any name from list_device_types (defaults: server|vm|lxc|network|iot|desktop|laptop|phone|other).
    status: active|reserved|dhcp|offline. Use allocate_next_ip instead if any free
    address will do."""
    payload = HostCreate(
        subnet_id=subnet_id,
        ip=ip,
        hostname=hostname,
        domain=domain,
        mac=mac,
        device_type=device_type,
        status=status,
        owner=owner,
        notes=notes,
    )
    async with SessionLocal() as session:
        host = await hosts_svc.create_host(session, payload, source="mcp")
        return {"host": await _host(session, host)}


@ipam_tool
async def allocate_next_ip(
    subnet_id: str,
    hostname: str,
    domain: str | None = None,
    mac: str | None = None,
    device_type: str = "other",
    status: str = "active",
    owner: str | None = None,
    notes: str | None = None,
) -> dict:
    """Allocate the next available IP in a subnet and create a host record in one step.
    Skips the gateway, DHCP pool, reserved ranges, and network/broadcast addresses
    automatically. Use this when the caller does not need a specific IP address.
    domain overrides the subnet's default domain suffix; leave unset to inherit it."""
    payload = AllocateRequest(
        hostname=hostname,
        domain=domain,
        mac=mac,
        device_type=device_type,
        status=status,
        owner=owner,
        notes=notes,
    )
    async with SessionLocal() as session:
        host = await hosts_svc.allocate_next(session, subnet_id, payload, source="mcp")
        return {"host": await _host(session, host)}


@ipam_tool
async def bulk_create_hosts(subnet_id: str, hosts: list[dict]) -> dict:
    """Create many host records in one subnet in a single call — use this instead of
    repeated create_host calls when importing or registering several devices at once
    (max 1000 per call). Each item is an object with: ip (required), hostname, domain,
    mac, device_type (server|vm|lxc|network|iot|desktop|laptop|phone|other), status
    (active|reserved|dhcp|offline), owner, notes. Records succeed or fail
    individually: duplicates are skipped and invalid records report an error without
    aborting the batch. Returns created/skipped/error counts and a per-record result
    list; the whole import is one audit-log entry."""
    if not hosts:
        raise ToolError("hosts must contain at least one record")
    if len(hosts) > 1000:
        raise ToolError("at most 1000 records per call")
    async with SessionLocal() as session:
        return await hosts_svc.bulk_create(session, subnet_id, hosts, source="mcp")


@ipam_tool
async def bulk_delete_hosts(host_ids: list[str]) -> dict:
    """Delete many host records in one call, freeing their IPs — use instead of
    repeated delete_host calls when cleaning up several hosts at once (max 1000).
    Get IDs from list_hosts first. Returns the deleted count plus any IDs that were
    not found. One audit-log entry per affected subnet, with full before-snapshots."""
    if not host_ids:
        raise ToolError("host_ids must contain at least one ID")
    if len(host_ids) > 1000:
        raise ToolError("at most 1000 IDs per call")
    async with SessionLocal() as session:
        return await hosts_svc.bulk_delete(session, host_ids, source="mcp")


@ipam_tool
async def update_host(
    host_id: str,
    ip: str | None = None,
    hostname: str | None = None,
    domain: str | None = None,
    mac: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    notes: str | None = None,
    clear_fields: list[HostClearField] | None = None,
) -> dict:
    """Update fields on an existing host record. Only the provided fields are changed.
    domain sets a per-host domain-suffix override (the subnet default applies when no
    override is set). device_type: any name from list_device_types (defaults: server|vm|lxc|network|iot|desktop|laptop|phone|other).
    status: active|reserved|dhcp|offline. To clear nullable values, pass their names in
    clear_fields; for example, clear_fields=["domain", "notes"] removes the domain
    override and notes. A field cannot be both set and cleared in the same call."""
    provided = {
        k: v
        for k, v in {
            "ip": ip,
            "hostname": hostname,
            "domain": domain,
            "mac": mac,
            "device_type": device_type,
            "status": status,
            "owner": owner,
            "notes": notes,
        }.items()
        if v is not None
    }
    for field in clear_fields or []:
        if field in provided:
            raise ToolError(f"{field} cannot be both set and cleared")
        provided[field] = None
    payload = HostUpdate(**provided)
    async with SessionLocal() as session:
        host = await hosts_svc.update_host(session, host_id, payload, source="mcp")
        return {"host": await _host(session, host)}


@ipam_tool
async def delete_host(host_id: str) -> dict:
    """Remove a host record, freeing its IP address for future allocation."""
    async with SessionLocal() as session:
        host = await hosts_svc.get_host(session, host_id)
        freed_ip = host.ip
        await hosts_svc.delete_host(session, host_id, source="mcp")
        return {"deleted": True, "freed_ip": freed_ip}


@ipam_tool
async def list_changes(entity_type: str | None = None, limit: int = 20) -> dict:
    """Recent changes to the IPAM (audit log), newest first. Each entry records what
    changed, before/after snapshots, and whether it came from the UI, the REST API, or
    MCP (source). actor names who acted when that is narrower than source; it is null
    for the owner's own writes. Use to answer "what changed recently?" or to review
    earlier MCP-driven edits. entity_type filters to: section|subnet|host."""
    async with SessionLocal() as session:
        rows = await changes_svc.list_changes(session, entity_type=entity_type, limit=limit)
        return {"changes": [ChangeOut.model_validate(c).model_dump(mode="json") for c in rows]}
