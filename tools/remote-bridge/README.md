# Remote Bridge — use your vault from ChatGPT (or any external AI)

Your vault normally only listens on `localhost`, reachable by Claude Code
through the local MCP shim. This bridge lets an **external** client — a ChatGPT
Custom GPT, a phone, another agent — **read and write the same vault** over an
authenticated HTTPS tunnel.

Nothing here patches the memory-vault engine. It uses the engine's built-in
Bearer auth (`API_AUTH_ENABLED=true`, already on in this kit) plus a public
tunnel. Two moving parts you add: **a dedicated token** and **a tunnel**.

```
ChatGPT Custom GPT ──HTTPS + Bearer──> tunnel ──> localhost:8000 (memory-vault) ──> Postgres
```

> **Reality check:** the vault runs on your machine. The bridge works only while
> your machine is awake and the Docker stack + tunnel are running. Laptop asleep
> → ChatGPT can't reach the memory. This is a personal bridge, not a hosted SaaS.

---

## Prerequisites

- The kit already installed and running (`docker compose up -d`, dashboard
  reachable at `http://localhost:8000`).
- One of:
  - **A domain on Cloudflare** → stable hostname (`vault.example.com`). Recommended.
  - **No domain** → a quick tunnel (`cloudflared`) or `ngrok`; you get a random
    URL that changes on restart. Fine for testing, annoying long-term.
- A ChatGPT account that can create **Custom GPTs** (Plus/Team/Enterprise).

---

## Step 1 — Create a dedicated token

Do **not** reuse the `claude-shim` token. A separate token can be revoked on its
own if it leaks, without knocking Claude offline.

```bash
docker compose exec app python -m src.cli token create chatgpt-bridge
```

Copy the `mv_...` value — it is shown once. Store it in a password manager.
To revoke later, use the **exact PREFIX** shown by `token list` (the `mv_`
value in the PREFIX column, not the first N characters of the full token):

```bash
docker compose exec app python -m src.cli token list           # find the PREFIX
docker compose exec app python -m src.cli token revoke mv_xxxxxxxx
```

---

## Step 2 — Expose the vault over HTTPS

### Option A — Cloudflare Tunnel with your domain (recommended)

```bash
brew install cloudflared               # macOS; see cloudflare docs for other OS
cloudflared tunnel login               # opens browser, pick your zone
cloudflared tunnel create vault
# route a hostname to the tunnel:
cloudflared tunnel route dns vault vault.example.com
```

Create `~/.cloudflared/vault-config.yml`:

```yaml
tunnel: vault
credentials-file: /Users/YOU/.cloudflared/<TUNNEL-UUID>.json
ingress:
  - hostname: vault.example.com
    service: http://localhost:8000
  - service: http_status:404
```

Run it (foreground test):

```bash
cloudflared tunnel --config ~/.cloudflared/vault-config.yml run vault
```

Keep it alive across reboots on macOS with the launchd plist in this folder
(`com.user.vault-tunnel.plist` — edit paths, then):

```bash
cp com.user.vault-tunnel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.vault-tunnel.plist
```

Your public base URL is now `https://vault.example.com`.

> **QUIC gotcha:** on networks that block UDP 7844 (phone hotspots, some office
> firewalls) the tunnel connects but Cloudflare returns error 1033/530. Fix:
> run cloudflared with `--protocol http2` (TCP 443).

> **No browser login needed:** if you have a Cloudflare API token/Global Key,
> the whole tunnel (create → ingress → DNS → tunnel token) can be driven via
> the CF API (`/accounts/{id}/cfd_tunnel`), then `cloudflared tunnel run
> --token-file ...`. The launchd plist in this folder reads the token from
> `~/.cloudflared/` so the secret never sits inside the plist.

### Option B — No domain (quick tunnel)

```bash
cloudflared tunnel --url http://localhost:8000
```

Prints a random `https://<random>.trycloudflare.com` URL. Use that as the base
URL. It changes every restart — update the Custom GPT each time. `ngrok http
8000` works the same way.

### Verify the tunnel + auth both work

```bash
# should be 401 without a token:
curl -s -o /dev/null -w '%{http_code}\n' https://vault.example.com/api/spaces

# should be 200 with the token:
curl -s https://vault.example.com/api/search \
  -H "Authorization: Bearer mv_your_token" \
  -H "Content-Type: application/json" \
  -d '{"query":"test","limit":1}'
```

If the first returns `200`, auth is OFF — **stop** and fix
`API_AUTH_ENABLED=true` before going further.

---

## Step 3 — Build the ChatGPT Custom GPT

1. ChatGPT → **Explore GPTs → Create → Configure**.
2. Give it a name/instructions, e.g.:
   > You have access to my personal Memory Vault. Call `recallMemory` before
   > answering anything about me, my projects, or past decisions. When I say
   > "remember X", call `rememberMemory`. Only call `forgetMemory` when I
   > explicitly ask to delete something.
3. **Actions → Create new action.**
4. **Schema:** paste the contents of [`openapi.yaml`](./openapi.yaml). Then edit
   the `servers.url` line to your real hostname from Step 2.
5. **Authentication → API Key.**
   - Auth Type: **API Key**
   - Header name: `Authorization`
   - Value: `Bearer mv_your_token` (include the word `Bearer` and a space)
6. Save. Test in the preview: *"what do you remember about me?"* → it should
   call `recallMemory` and return hits.

> Custom GPT Actions are server-to-server, so the vault's `API_CORS_ORIGINS`
> setting is irrelevant here (CORS is browser-only).

---

## Security notes

- **The token is the only lock.** Anyone with the URL + token has full API
  access (read, write, delete). Keep it in a password manager; never commit it.
- Prefer a **hard-to-guess hostname** and add a **Cloudflare WAF rule** — rate
  limit, or allow only OpenAI's egress ranges — for defense in depth.
- Writes from ChatGPT land in the `default` space (per `openapi.yaml`), so they
  never overwrite curated `user`/diary memories.
- Rotate: `token revoke <prefix>` then `token create` a fresh one and update the
  Custom GPT. Do this immediately if you suspect a leak.
- Turning the bridge off entirely: stop the tunnel (`launchctl unload ...`).
  The vault keeps working for Claude locally.
