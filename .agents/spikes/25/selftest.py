import base64, hashlib, json, secrets, sys, httpx
B = sys.argv[1]
c = httpx.Client(base_url=B, timeout=10)
print("prm", c.get("/.well-known/oauth-protected-resource/mcp").status_code, c.get("/.well-known/oauth-authorization-server").json().get("registration_endpoint"))
r = c.post("/mcp", json={"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}, headers={"Accept":"application/json, text/event-stream"})
print("unauth", r.status_code, r.headers.get("www-authenticate"))
reg = c.post("/register", json={"client_name":"harness self-test","redirect_uris":["http://localhost:9/cb"],"grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"}).json()
v = secrets.token_urlsafe(40); ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
a = c.get("/authorize", params={"client_id":reg["client_id"],"redirect_uri":"http://localhost:9/cb","response_type":"code","code_challenge":ch,"code_challenge_method":"S256","state":"s","scope":"ipam:read ipam:write","resource":B+"/mcp"})
code = dict(httpx.URL(a.headers["location"]).params)["code"]
tok = c.post("/token", data={"grant_type":"authorization_code","code":code,"redirect_uri":"http://localhost:9/cb","client_id":reg["client_id"],"code_verifier":v,"resource":B+"/mcp"}).json()
print("token scope", tok.get("scope"))
H = {"Authorization":"Bearer "+tok["access_token"], "Accept":"application/json, text/event-stream"}
r = c.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}, headers=H)
H["Mcp-Session-Id"] = r.headers.get("mcp-session-id",""); H["MCP-Protocol-Version"]="2025-06-18"
c.post("/mcp", json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)
def rpc(i, m, p):
    r = c.post("/mcp", json={"jsonrpc":"2.0","id":i,"method":m,"params":p}, headers=H)
    t = r.text; return r.status_code, r.headers.get("www-authenticate"), (t[t.find("{"):] if "{" in t else t)[:400]
print("list", rpc(2,"tools/list",{}))
print("write", rpc(3,"tools/call",{"name":"spike_write","arguments":{"note":"x"}}))
print("read", rpc(4,"tools/call",{"name":"spike_read","arguments":{}}))
