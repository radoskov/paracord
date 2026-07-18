# Encrypting browser ↔ server traffic (HTTPS/TLS)

Reasoning + recommendation for the owner's question: *"the browser session is plain HTTP — what's a
reasonably good/robust/safe/simple way to encrypt it?"* (2026-07-18).

## The problem, precisely

- Auth is a **bearer token** sent in the `Authorization: Bearer …` header on every API call (see
  `backend/app/api/deps.py`); the SPA holds it in the browser. The agent uses a similar token.
- The **dev stack** serves the SPA (Vite, :5173) and API (:8000) over **plain HTTP**. On `localhost`
  that traffic never leaves the machine, so it's fine for a single user. **The moment another machine
  on the LAN connects over HTTP, the bearer token — and everything else — crosses the network in the
  clear** and can be read by anyone sniffing the LAN (the exact risk you flagged).
- Scale here is "mostly single-user, a few LAN users" — so the realistic threat is a LAN eavesdropper,
  not the public internet.

## The good news: TLS is already built — it just isn't turned on in dev

The **production overlay** (`docker-compose.prod.yml`, AUDIT D3) already ships a **Caddy reverse
proxy** that terminates HTTPS on :443 and proxies to the SPA + API on the internal Docker network.
Bring it up with `make prod-up`. So this is a *configure-and-enable* task, not a build-from-scratch.

## Options (most-recommended first)

### A. Caddy + local CA (`tls internal`) — **recommended for a LAN**
Caddy mints certificates from its own local certificate authority. One command, no public domain, no
internet dependency.

- **Enable:** `cp config/Caddyfile.example config/Caddyfile`, set your server's LAN name/IP in it
  (e.g. `https://paracord.lan`), then `make prod-up`. Reach the app at `https://paracord.lan`.
- **Trust the CA once per client** (otherwise the browser warns):
  `docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt` → import as a trusted
  root in each browser/OS, and set the agent's `ca_cert` to it. (Or just click through the warning —
  the connection is still encrypted; the warning is only about trust, not secrecy.)
- **Pros:** simple, robust, offline, already implemented. **Cons:** the one-time CA-trust step per
  client.

### B. Caddy + real domain + Let's Encrypt
If the server has a DNS name reachable for the ACME challenge, drop the `tls internal` line and use the
real hostname — Caddy fetches and renews public certificates automatically (no client-trust step).
Best when you have a domain; overkill/unavailable on an isolated LAN.

### C. Caddy + your own certificate files
Have a corporate/internal CA already? Point Caddy at the cert + key
(`tls /path/cert.pem /path/key.pem`). Clients that already trust your CA get no warning.

### D. Network-layer encryption (WireGuard / Tailscale)
Encrypt the whole link instead of the app. Good when users are **remote**, not just on the LAN.
Tailscale in particular can also hand out real HTTPS certs (`tailscale serve`) so you get trusted TLS
with almost no config. The app itself can stay HTTP inside the tunnel. Complementary to A for remote
access.

### E. Do nothing — loopback only
Perfectly fine **if and only if** the app is only ever used from the same machine (`localhost`).
Traffic never touches the network. This is the current dev default and the safe fallback.

## Recommendation

- **Single user on the same machine:** keep HTTP (option E). No action needed.
- **A few users on the LAN (your case):** enable **option A** — `make prod-up` with the Caddyfile set
  to your LAN name, and trust the local CA on each client. Simplest robust encryption, already wired.
- **Any remote access:** layer **option D** (Tailscale/WireGuard) on top.

## Owner configuration (option A, step by step)

1. `cp config/Caddyfile.example config/Caddyfile` and set the site address to your LAN name/IP.
2. In `.env`, set `VITE_API_BASE_URL=https://<that-same-host>` — **no `:8000`**. The SPA then calls the
   API at the same HTTPS origin and Caddy routes `/api/*` to the API internally, so API calls are also
   encrypted. (Pointing this at `https://host:8000` would bypass Caddy and hit the API's plain port —
   see the fixed comment in `docker-compose.prod.yml`.)
3. `make prod-up` (rebuilds the SPA with the baked API URL and starts Caddy).
4. Trust the local CA on each client (command above). Set the agent's `ca_cert`.
5. Reach the app at `https://<host>`. Caddy also sends HSTS and redirects HTTP→HTTPS automatically.

## Fallback

Everything above is **opt-in**. The default dev stack stays HTTP (loopback-safe). If you remove the
`caddy` service you're back to plain HTTP — no code path depends on TLS being present.

## Notes / non-goals

- Because auth is a header bearer token (not a cookie), there's no `Secure`-cookie flag to toggle;
  TLS on the transport is the whole fix. Tokens live in browser storage as today.
- This document is guidance; the mechanism (Caddy overlay) already exists and is unchanged here beyond
  a corrected `VITE_API_BASE_URL` comment.
