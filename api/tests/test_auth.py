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


async def test_mcp_mount_requires_key(anon_client):
    res = await anon_client.post("/mcp/", json={})
    assert res.status_code == 401


async def test_mcp_reachable_without_trailing_slash(anon_client):
    # mcp-remote and Claude Desktop request the bare /mcp path; a 401 here proves the
    # request reached the MCP auth layer instead of falling through to the SPA
    res = await anon_client.post("/mcp", json={})
    assert res.status_code == 401
