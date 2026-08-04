# Developer Setup Guide — EC2, Network, TLS, Backend ⇄ Frontend

**Companion to:** `SYSTEM_DESIGN_AND_INTERFACES.md` (the contract) and
`FIRSTTICK_SYSTEM_IMPLEMENTATION_PLAN.md` (the plan).

This guide is **operational**: provision the box, configure the network, run the service, expose it
publicly with valid HTTPS **without buying a domain**, and connect the Vercel frontend.

Target OS: **Ubuntu 22.04 / 24.04 LTS**, region **ap-south-1 (Mumbai)**.

---

## 0. TL;DR — the path I recommend

```
EC2 (ap-south-1, Elastic IP)
  └─ uvicorn on 127.0.0.1:8080          ← not exposed directly
       └─ Caddy on :80/:443             ← auto Let's Encrypt cert
            └─ https://203-0-113-10.sslip.io      ← FREE hostname, NO domain to buy
                 ├─ REST  https://…/api/v1/...
                 └─ WS    wss://…/api/v1/ws
                      ▲
                      └── Vercel SPA (https://your-app.vercel.app)
```

**Why:** `sslip.io` is a free public DNS service that resolves any IP encoded in the hostname
(`203-0-113-10.sslip.io` → `203.0.113.10`). Because it is a *real* DNS name, **Let's Encrypt will
issue a genuine certificate for it** — so you get valid `https://` **and** `wss://` in every browser,
with **no domain purchase, no DNS panel, and no browser security overrides.**

---

## 1. Prerequisites

| Item | Notes |
|---|---|
| AWS account | Region **ap-south-1** (closest to NSE) |
| SSH keypair | `.pem`, `chmod 600` |
| Zerodha Kite Connect | `api_key`, `api_secret`, TOTP seed. Kite Connect is a **paid** add-on (₹500/mo/app). |
| Vercel account | For the frontend |
| Local | `ssh`, `scp`, `git` |

---

## 2. Provision the EC2

### 2.1 Instance

| Setting | Value | Why |
|---|---|---|
| Region | **ap-south-1** | Lowest RTT to the broker |
| AMI | Ubuntu 24.04 LTS (x86_64) | |
| Type | **`c6i.large`** (2 vCPU, 4 GB) for live trading | Non-burstable — no CPU-credit stalls in the hot path |
| | `t3.medium` acceptable for P2 recording-only | Cheaper; set **Unlimited** credits |
| Storage | **50 GB gp3** | OS + ~7 days of compressed ticks (~1 GB) + headroom |
| Elastic IP | **REQUIRED — allocate & associate** | ⚠️ Without it the public IP changes on stop/start, which **breaks the sslip.io hostname and the cert** |

### 2.2 Security group

| Rule | Port | Source | Purpose |
|---|---|---|---|
| Inbound | 22 | **Your IP /32** (preferred) or `0.0.0.0/0` | SSH |
| Inbound | 80 | `0.0.0.0/0` | Let's Encrypt HTTP-01 challenge + HTTP→HTTPS redirect |
| Inbound | 443 | `0.0.0.0/0` | **The API + WebSocket (any place, any browser)** |
| Outbound | all | `0.0.0.0/0` | Broker API + WS |

> **Do not open 8080.** uvicorn binds to `127.0.0.1`; Caddy is the only public listener. This is both
> safer and required for TLS to be meaningful. If you open 8080 you have handed the internet an
> unauthenticated-by-default trading API.
>
> If you must allow SSH from anywhere, disable password auth (`PasswordAuthentication no`) and
> consider `fail2ban` — an open 22 with keys only is acceptable; with passwords it is not.

---

## 3. Base OS setup

```bash
ssh -i key.pem ubuntu@<ELASTIC_IP>

sudo apt update && sudo apt -y upgrade
sudo apt -y install python3.12 python3.12-venv python3-pip git curl unzip \
                    chrony zstd jq ufw fail2ban

# ---- Timezone: IST (every schedule field is IST) ----
sudo timedatectl set-timezone Asia/Kolkata
timedatectl                      # verify

# ---- Time sync: REQUIRED for honest latency numbers ----
sudo systemctl enable --now chrony
chronyc tracking                 # 'System time' offset should be < 50 ms
chronyc sources -v

# ---- File descriptors (1 WS + many files + HTTP pool) ----
echo -e "ubuntu soft nofile 65535\nubuntu hard nofile 65535" | sudo tee -a /etc/security/limits.conf

# ---- Swap (protects against OOM during tick bursts) ----
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Host firewall** (defence in depth behind the SG):
```bash
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable && sudo ufw status verbose
```

---

## 4. Deploy the application

```bash
sudo mkdir -p /opt/firsttick && sudo chown ubuntu:ubuntu /opt/firsttick
cd /opt/firsttick

git clone <your-repo> .          # or: scp -r ./backend ubuntu@<IP>:/opt/firsttick/

python3.12 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

**`requirements.txt` baseline**
```
kiteconnect>=5.0.1
fastapi>=0.115
uvicorn[standard]>=0.34      # [standard] pulls websockets + uvloop + httptools
pydantic>=2.9
pandas>=2.2
numpy>=2.0
pyotp>=2.9
requests>=2.32
zstandard>=0.23
loguru>=0.7
python-dateutil>=2.9
```

### 4.1 Credentials — lock them down

```bash
mkdir -p /opt/firsttick/config
cat > /opt/firsttick/config/credentials.json <<'EOF'
{
  "api_key":    "xxxxxxxxxxxx",
  "api_secret": "xxxxxxxxxxxx",
  "user_id":    "AB1234",
  "password":   "xxxxxxxx",
  "totp_key":   "XXXXXXXXXXXXXXXX"
}
EOF
chmod 600 /opt/firsttick/config/credentials.json
```
Add `config/credentials.json` to `.gitignore`. **Never commit it.** (The credentials currently sitting
in plaintext inside `Vijay915/login.py` should move here too, and that file's secrets rotated.)

### 4.2 Key config values for this deployment

```jsonc
"api": {
  "host": "127.0.0.1",                       // ← behind Caddy, NOT 0.0.0.0
  "port": 8080,
  "cors_origins": ["https://your-app.vercel.app", "http://localhost:5173"],
  "auth_token": "<long random string>",      // openssl rand -hex 32
  "ws_push_interval_ms": 250
},
"system": { "data_dir": "/opt/firsttick/data", "timezone": "Asia/Kolkata" }
```

```bash
openssl rand -hex 32      # generate auth_token
```

---

## 5. Run it as a service (replaces the manual morning launch)

The app's own scheduler idles until `phase1_time` (08:45), so systemd simply keeps it alive 24/7.
No cron, no timer, nothing to remember at 09:00.

```bash
sudo tee /etc/systemd/system/firsttick.service >/dev/null <<'EOF'
[Unit]
Description=First-Tick Open-Drive Trading System
After=network-online.target chrony.service
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/firsttick
Environment="PYTHONUNBUFFERED=1"
Environment="TZ=Asia/Kolkata"
ExecStart=/opt/firsttick/.venv/bin/python -u main.py
Restart=always
RestartSec=10
LimitNOFILE=65535
StandardOutput=append:/opt/firsttick/logs/service.log
StandardError=append:/opt/firsttick/logs/service.err

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/firsttick

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /opt/firsttick/logs
sudo systemctl daemon-reload
sudo systemctl enable --now firsttick
systemctl status firsttick
journalctl -u firsttick -f          # live logs
```

> ✅ This also fixes a **real production problem you've already hit twice**: the current algo is
> launched by hand in an SSH session, so closing the terminal kills it (23 Jul — PnL tracking died at
> 09:16 and the whole session's P&L was never recorded). Under systemd it survives logout, reboots,
> and crashes.

**Log rotation**
```bash
sudo tee /etc/logrotate.d/firsttick >/dev/null <<'EOF'
/opt/firsttick/logs/*.log /opt/firsttick/logs/*.err {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

---

## 6. Public HTTPS **without buying a domain**

### 6.1 Choose your option

| # | Option | Domain needed? | Valid TLS | WebSocket | Stable URL | Verdict |
|---|---|---|---|---|---|---|
| **1** | **sslip.io + Caddy + Let's Encrypt** | **No** | **✓ real cert** | **✓ wss** | ✓ (with Elastic IP) | ⭐ **Recommended** |
| 2 | Cloudflare Tunnel (named) | Yes | ✓ | ✓ | ✓ | Best if you already own a domain / want to hide the IP |
| 3 | Cloudflare **quick** tunnel | No | ✓ | ✓ | ✗ URL changes each restart | Fine for a demo, not for daily use |
| 4 | Tailscale Funnel (`*.ts.net`) | No | ✓ | ✓ | ✓ | Good; max 3 funnels, extra dependency |
| 5 | Serve the frontend **from the EC2 over plain HTTP** | No | ✗ | ✓ (`ws://`) | ✓ | Simplest of all — but unencrypted (§6.5) |
| 6 | Plain HTTP API + browser "allow insecure content" | No | ✗ | ⚠️ often still blocked | ✓ | ❌ Not recommended (§6.6) |

### 6.2 Option 1 — the recommended setup

`sslip.io` resolves an IP embedded in the hostname. For Elastic IP `203.0.113.10`:

```
203-0-113-10.sslip.io   →   203.0.113.10      (dashes)
203.0.113.10.sslip.io   →   203.0.113.10      (dots — also works)
```

Verify first:
```bash
dig +short 203-0-113-10.sslip.io       # must print your Elastic IP
```

Install Caddy (auto-provisions and auto-renews Let's Encrypt certs):
```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```

**Caddyfile** — replace the hostname with yours:
```bash
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
203-0-113-10.sslip.io {
    encode zstd gzip

    # WebSocket + REST both proxy cleanly; Caddy handles the Upgrade automatically.
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
        flush_interval -1          # no buffering — required for WS/streaming
    }

    log {
        output file /var/log/caddy/firsttick.log
        format json
    }
}
EOF

sudo systemctl reload caddy
sudo journalctl -u caddy -n 50 --no-pager     # watch the cert get issued
```

Verify:
```bash
curl -sS https://203-0-113-10.sslip.io/api/v1/health | jq
# → {"status":"ok","uptime_s":…,"version":"…"}
```

**Your endpoints are now:**
```
REST : https://203-0-113-10.sslip.io/api/v1
WS   : wss://203-0-113-10.sslip.io/api/v1/ws
```
Reachable from **any browser, anywhere**, with a valid certificate and **no security overrides**.

### 6.3 sslip.io caveats (know these)

| Caveat | Mitigation |
|---|---|
| **IP change breaks hostname + cert** | Use an **Elastic IP** (mandatory) |
| Shared Let's Encrypt rate limit across all sslip.io users (raised to 250k certs, but shared) | Caddy caches and renews at 2/3 lifetime; don't destroy `/var/lib/caddy` needlessly. Keep option 2/4 as a fallback. |
| No wildcard certs | Not needed — we use one hostname |
| Depends on a third-party DNS service | If sslip.io is ever down, new cert issuance fails (existing cert keeps working for ~60 days). Buying a ₹700/yr domain removes this entirely — worth it once this is real money. |

### 6.4 Option 2 — Cloudflare Tunnel (if you get a domain later)

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cf.deb
sudo dpkg -i cf.deb
cloudflared tunnel login
cloudflared tunnel create firsttick
cloudflared tunnel route dns firsttick api.yourdomain.com
sudo tee /etc/cloudflared/config.yml >/dev/null <<'EOF'
tunnel: firsttick
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: api.yourdomain.com
    service: http://127.0.0.1:8080
  - service: http_status:404
EOF
sudo cloudflared service install
```
Bonus: **no inbound ports needed at all** — you can close 80/443 entirely.

### 6.5 Option 5 — no TLS at all (simplest, least secure)

Serve the built frontend from the same EC2 over HTTP. Because the page is `http://`, calling
`http://<ip>:8080` is **same-scheme** — no mixed content, no CORS, no certificates.

```bash
# Caddyfile
:80 {
    root * /opt/firsttick/frontend/dist
    file_server
    handle /api/* { reverse_proxy 127.0.0.1:8080 }
}
```
Then browse `http://203.0.113.10/`.

⚠️ **Everything — including your `auth_token` — travels in clear text.** Acceptable only on a trusted
network for short-lived testing. Do not use this for a live trading account over the public internet.

### 6.6 Why **not** to rely on "turn off browser security"

You mentioned you could disable security for the site. Chrome does have
*Site settings → Insecure content → Allow*, which permits `http://` calls from an `https://` page.
But:
- It **does not reliably un-block `ws://` from an `https://` page** — WebSocket mixed content is
  treated more strictly, so `/ws` push would still fail and you'd fall back to polling.
- It is **per-browser, per-profile, per-device** — every machine and every fresh profile needs it again.
- It is silently forgotten after profile resets, and it weakens the browser for that origin generally.

Option 1 takes ten minutes and removes the problem permanently. Use it.

---

## 7. Connect the frontend (Vercel)

### 7.1 Environment variables (Vercel → Settings → Environment Variables)

```
VITE_API_BASE = https://203-0-113-10.sslip.io/api/v1
VITE_WS_URL   = wss://203-0-113-10.sslip.io/api/v1/ws
```
Redeploy after changing these — Vite inlines them at build time.

### 7.2 Backend CORS must list the exact Vercel origin

```jsonc
"api": { "cors_origins": [
    "https://your-app.vercel.app",
    "https://your-app-git-main-you.vercel.app",   // Vercel preview deploys differ!
    "http://localhost:5173"
]}
```
Apply without restart: `POST /api/v1/config` — or `sudo systemctl restart firsttick`.

> ⚠️ **Vercel preview deployments get a different hostname on every push.** Either add them
> explicitly, or (dev only) allow the pattern. **Never ship `"*"`** while a real `auth_token` guards a
> live trading API.

### 7.3 Connection sequence the client should follow

```
GET  /api/v1/health                     → reachable?
GET  /api/v1/status    (Bearer token)   → phase, feed, counts
GET  /api/v1/config                     → render config forms from the returned JSON-schema
GET  /api/v1/universe                   → instrument set
POST /api/v1/auth/ws-ticket             → { ticket, expires_in: 60 }
WS   wss://…/api/v1/ws?token=<ticket>
     → { "op":"subscribe", "topics":["status","market","positions","orders","events"] }
     → receive snapshot per topic, then diffs
on close → exponential backoff 1s→30s; after 3 failures fall back to polling /status every 1s
```

### 7.4 Verify end-to-end

```bash
TOKEN=<your auth_token>
BASE=https://203-0-113-10.sslip.io/api/v1

curl -sS $BASE/health | jq                                    # 1. unauth health
curl -sS -H "Authorization: Bearer $TOKEN" $BASE/status | jq   # 2. auth works
curl -sS -o /dev/null -w '%{http_code}\n' $BASE/status         # 3. → 401 without token

# 4. CORS preflight from the Vercel origin
curl -sS -i -X OPTIONS $BASE/status \
  -H "Origin: https://your-app.vercel.app" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: authorization" | grep -i access-control

# 5. WebSocket upgrade
npx wscat -c "wss://203-0-113-10.sslip.io/api/v1/ws?token=<ticket>"
```
Then from the browser console on the deployed site:
```js
await (await fetch(`${import.meta.env.VITE_API_BASE}/health`)).json()
```
No mixed-content or CORS errors ⇒ done.

---

## 8. Operations

### 8.1 Daily
```bash
systemctl status firsttick
journalctl -u firsttick --since "08:40" --no-pager | head -50
curl -sS -H "Authorization: Bearer $TOKEN" $BASE/status | jq '.data.phase, .data.feed'
```

### 8.2 Disk — the recorder is the only thing that grows
```bash
df -h /                                  # keep > 20% free
du -sh /opt/firsttick/data/*             # per-session size
```
`recorder.retention_days` (default 7) prunes automatically. `on_disk_full` defaults to
`stop_recording` — recording stops, **trading continues**. Set `halt_trading` if you would rather stop
trading than lose the audit trail.

> ⚠️ Watch this specifically: on a sibling system, a free-space guard in a cleanup script blocked the
> DB `VACUUM` once the file grew large, so it could never shrink again. Verify pruning actually runs
> in week one rather than assuming it does.

### 8.3 Update / rollback
```bash
cd /opt/firsttick
git pull && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m py_compile $(git ls-files '*.py')     # syntax gate
sudo systemctl restart firsttick && journalctl -u firsttick -n 30 --no-pager

# rollback
git reset --hard <previous-sha> && sudo systemctl restart firsttick
```
**Never deploy between 08:45 and 15:30 on a trading day.** The service persists its position book, but
a restart mid-session forces a reconciliation cycle and disarms entries until it completes.

### 8.4 Back up what matters
```bash
tar -czf ~/firsttick-config-$(date +%F).tgz -C /opt/firsttick config
aws s3 sync /opt/firsttick/data s3://<bucket>/firsttick/   # optional tick archive
```

---

## 9. Verification checklist

**Infrastructure**
- [ ] `timedatectl` shows IST · `chronyc tracking` offset < 50 ms
- [ ] Elastic IP allocated **and associated**
- [ ] SG: 22/80/443 only — **8080 closed**
- [ ] `ss -tlnp | grep 8080` shows `127.0.0.1:8080`, not `0.0.0.0`
- [ ] `ufw status` active

**Service**
- [ ] `systemctl is-enabled firsttick` → enabled
- [ ] Survives `sudo reboot`
- [ ] Survives closing the SSH session ← *the failure you hit on 23 Jul*
- [ ] `credentials.json` is `600` and gitignored

**Network / TLS**
- [ ] `dig +short <host>.sslip.io` → Elastic IP
- [ ] `curl https://<host>.sslip.io/api/v1/health` → 200, no cert warning
- [ ] `wscat` connects over `wss://`
- [ ] `/status` returns 401 without a token

**Frontend**
- [ ] Vercel env vars set and redeployed
- [ ] Production **and** preview origins in `cors_origins`
- [ ] Browser console: no mixed-content, no CORS errors
- [ ] WS connects and streams; killing it falls back to polling

**Trading readiness**
- [ ] `trading_mode.mode = "paper"` for the first sessions
- [ ] Phase 1 passes at 08:45 (check `/status` at 08:50)
- [ ] Feed connects 08:55, recorder writes files
- [ ] Ticks recorded through the 09:00–09:08 pre-open
- [ ] `GET /latency` populated after the first paper entry

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser: *blocked mixed content* | Frontend `https://` → API `http://` | Do §6.2 (sslip.io + Caddy) |
| WS fails, REST fine | Proxying WS through Vercel serverless (unsupported) | Connect `wss://` **directly** to Caddy |
| Cert issuance fails | Port 80 blocked, or DNS not resolving | Open 80; `dig` the hostname; `journalctl -u caddy` |
| Cert broke after reboot | Public IP changed | Associate an **Elastic IP**, update Caddyfile |
| CORS error only on previews | Preview origin not allow-listed | Add the preview hostname |
| 401 on every call | Token mismatch / not sent | Compare with `config.api.auth_token` |
| Service dies at logout | Not running under systemd | §5 |
| Latency numbers look impossible | Clock drift | `chronyc tracking`; check manifest `ntp_offset_ms` |
| Ticks missing / gaps | WS reconnect without re-subscribe | Check `FEED_GAP` events; the feed layer must re-subscribe **and** re-apply modes |
| Orders rejected `LPP` | Limit outside broker band | Expected on gaps — the retry path re-prices; check `/orders` |
| Orders rejected on stock options with MARKET | Zerodha blocks MARKET there | Keep `order_type.stock_options = "LIMIT"` |
| Disk filling | Recorder retention not pruning | Check `recorder.retention_days`; verify prune actually ran |

---

## Appendix — Ports & endpoints

| Port | Bind | Public | Purpose |
|---|---|---|---|
| 22 | 0.0.0.0 | your IP (preferred) | SSH |
| 80 | 0.0.0.0 | ✓ | ACME challenge + redirect |
| 443 | 0.0.0.0 | ✓ | **API + WebSocket** |
| 8080 | **127.0.0.1** | ✗ | uvicorn (Caddy upstream only) |

| Endpoint | URL |
|---|---|
| Health (unauth) | `https://<host>.sslip.io/api/v1/health` |
| REST base | `https://<host>.sslip.io/api/v1` |
| WebSocket | `wss://<host>.sslip.io/api/v1/ws` |

## Appendix — Sources

- [sslip.io / nip.io](https://sslip.io/) — wildcard DNS for any IP; Let's Encrypt limit raised to 250k certs (shared); no wildcard certs
- [Caddy](https://caddyserver.com/docs/) — automatic HTTPS, WebSocket-transparent `reverse_proxy`
- [Cloudflare Tunnel vs Tailscale Funnel comparison](https://onidel.com/blog/tailscale-cloudflare-nginx-vps-2025) — named tunnels need a domain; Funnel gives `*.ts.net`, max 3
- [Kite Connect docs](https://kite.trade/docs/connect/v3/) — API surface and limits
