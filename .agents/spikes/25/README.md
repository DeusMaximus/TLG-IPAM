# #25: web clients against a dynamic-registration-only server

The harness for spec §6.8's spike. The findings are in the comment on #25. The spec changes they force are in the PR that closes #25.

| File | What it is |
|------|------------|
| `spike.py` | FastMCP 4.1.8's `InMemoryOAuthProvider` (an `OAuthProvider`, so no CIMD) with three tools and a JSON-lines request log. Every token is granted `ipam:read` alone, so `spike_write` (`ipam:write`) is always out of scope. It approves every authorise request without asking, so run it only while linking. |
| `Dockerfile`, `compose.yml` | Build from the server's own lock, so the spike runs the `fastmcp` and `mcp` the app runs. Published on port 8090. |
| `selftest.py` | One OAuth and MCP pass with `httpx` against a running harness: discovery, registration, PKCE authorise, token, list, a call to the out-of-scope tool, and a call in scope. `python selftest.py https://mcp-spike.example.com` |
| `spike.jsonl` | The raw log of the run on 2026-11-25: the self-test, then Claude.ai, ChatGPT, Mistral and Gemini linking and calling, then each one refreshing its token about 3.5 hours later. |

```sh
# from the repository root
# The log directory is bind-mounted over the image's /logs, so the image's ownership of it does not
# apply, and Docker creates a missing one owned by root. The container's user must be able to write it:
mkdir -p .agents/spikes/25/logs && chmod a+rwx .agents/spikes/25/logs
PUBLIC_BASE_URL=https://mcp-spike.example.com docker compose -f .agents/spikes/25/compose.yml up -d --build
```

The log holds no authorisation code, PKCE value, state, token or client secret: the harness never writes them. Before it was committed, the run's public hostname was replaced by `mcp-spike.example.com`, and the per-connector IDs in ChatGPT's and Gemini's redirect URIs by `CONNECTOR-ID`. Nothing else was changed. Client IDs are the harness's own random UUIDs, from a server that no longer runs.
