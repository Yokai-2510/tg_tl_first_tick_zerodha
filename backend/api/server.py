"""
FastAPI surface: REST + WebSocket push.

Contract (docs/02 §7):
  * Every route except /health requires `Authorization: Bearer <auth_token>`.
  * Every read is served FROM MEMORY — no route calls the broker synchronously.
  * Envelope: {"ok": true, "data": ..., "ts": ...} / {"ok": false, "error": {...}}
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..core.enums import ExitTrigger, Phase
from ..core.timeutil import epoch_us, has_passed, now_ist
from .auth import find_user, issue_session, read_session, verify_password
from .broker_check import credential_view, run_test, token_cache_state
from .ws_push import WsHub

API_PREFIX = "/api/v1"


def ok(data: Any) -> dict:
    return {"ok": True, "data": data, "ts": now_ist().isoformat()}


def err(code: str, message: str, detail: Any = None) -> dict:
    return {"ok": False,
            "error": {"code": code, "message": message, "detail": detail},
            "ts": now_ist().isoformat()}


#: Fallback when nothing is configured — the Vite dev server.
DEV_ORIGIN = "http://localhost:5173"

#: Verified against when the username is unknown, so that a wrong username and a
#: wrong password take the same time and neither can be told apart from outside.
_DUMMY_HASH = (
    "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def cors_settings(origins: list[str]) -> tuple[list[str], str | None]:
    """Split configured origins into exact matches plus a regex for wildcards.

    An exact allow-list cannot keep up with preview deployments: Vercel mints a new
    hostname on every push, Cloudflare Pages and Netlify one per branch. So an entry
    whose host contains `*` — `https://*.myproject.pages.dev` — is compiled into an
    anchored regex instead.

    `*` expands to `[^/]+`, which cannot contain a slash, so
    `https://evil.com/x.pages.dev` does not match. It CAN span dots, meaning
    `https://*.pages.dev` admits any project on that shared domain — prefer a
    pattern that pins your own project name.

    A bare `"*"` keeps its ordinary allow-any meaning; the config layer already
    refuses that alongside a real auth_token.
    """
    exact: list[str] = []
    patterns: list[str] = []
    for origin in origins or []:
        o = origin.strip().rstrip("/")
        if not o:
            continue
        if o == "*" or "*" not in o:
            exact.append(o)
        else:
            patterns.append(re.escape(o).replace(r"\*", "[^/]+"))
    if not exact and not patterns:
        exact.append(DEV_ORIGIN)
    regex = "|".join(patterns) if patterns else None
    return exact, regex


def create_app(app_state) -> FastAPI:
    """Build the API. `app_state` is the live Application (see backend/main.py)."""
    api = FastAPI(title="TG/TL First-Tick", version="1.0.0", docs_url=f"{API_PREFIX}/docs")
    cfg = app_state.cfg
    hub: WsHub = app_state.hub

    allow_origins, allow_origin_regex = cors_settings(cfg.api.cors_origins)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    def auth(authorization: str = Header(default="")) -> None:
        """Two credentials are accepted, deliberately:

        * a session token from POST /auth/login -- what a person uses
        * the static `api.auth_token` -- what scripts, curl and monitoring use

        Both arrive as `Authorization: Bearer <...>`; the static token is checked
        first because it is a single constant-time compare.
        """
        cfg = app_state.cfg.api
        if not cfg.auth_token and not cfg.users:
            return                          # nothing configured = open (dev only)

        presented = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not presented:
            raise HTTPException(status_code=401, detail="AUTH_INVALID")
        if cfg.auth_token and secrets.compare_digest(presented, cfg.auth_token):
            return
        if read_session(presented, cfg.auth_token) is not None:
            return
        raise HTTPException(status_code=401, detail="AUTH_INVALID")

    @api.exception_handler(HTTPException)
    async def _http_exc(_request, exc: HTTPException):
        codes = {401: "AUTH_INVALID", 404: "NOT_FOUND", 409: "ILLEGAL_STATE",
                 422: "CONFIG_INVALID", 429: "RATE_LIMITED", 400: "VALIDATION_FAILED"}
        return JSONResponse(
            status_code=exc.status_code,
            content=err(codes.get(exc.status_code, "INTERNAL"), str(exc.detail)),
        )

    # ---------------- read ----------------

    @api.get("/health")
    @api.get(f"{API_PREFIX}/health")
    async def health():
        return ok({"status": "ok", "uptime_s": app_state.uptime_s,
                   "version": "1.0.0", "phase": str(app_state.scheduler.phase)})

    @api.get(f"{API_PREFIX}/status", dependencies=[Depends(auth)])
    async def status():
        return ok(app_state.status_payload())

    @api.get(f"{API_PREFIX}/config", dependencies=[Depends(auth)])
    async def get_config():
        return ok({"config": app_state.store.raw,
                   "schema": app_state.cfg.model_json_schema()})

    @api.get(f"{API_PREFIX}/universe", dependencies=[Depends(auth)])
    async def universe():
        return ok(app_state.universe_payload())

    @api.get(f"{API_PREFIX}/universe/ranking", dependencies=[Depends(auth)])
    async def ranking():
        return ok(app_state.ranking_payload())

    @api.get(f"{API_PREFIX}/market/snapshot", dependencies=[Depends(auth)])
    async def market_snapshot():
        return ok(app_state.market_payload())

    @api.get(f"{API_PREFIX}/market/instrument/{{token}}", dependencies=[Depends(auth)])
    async def market_instrument(token: int):
        view = app_state.feed.last(token)
        if view is None:
            raise HTTPException(404, f"no tick for token {token}")
        return ok({"token": view.token, "ltp": view.ltp, "bid": view.bid,
                   "ask": view.ask, "volume": view.volume, "oi": view.oi,
                   "exchange_ts_us": view.exchange_ts_us,
                   "feed_lag_us": view.feed_lag_us})

    @api.get(f"{API_PREFIX}/positions", dependencies=[Depends(auth)])
    async def positions():
        return ok(app_state.book.to_dicts(app_state.book.open_positions()))

    @api.get(f"{API_PREFIX}/positions/closed", dependencies=[Depends(auth)])
    async def positions_closed():
        return ok(app_state.book.to_dicts(app_state.book.closed_positions()))

    @api.get(f"{API_PREFIX}/positions/{{pos_id}}", dependencies=[Depends(auth)])
    async def position_one(pos_id: str):
        pos = app_state.book.get(pos_id)
        if pos is None:
            raise HTTPException(404, f"unknown position {pos_id}")
        return ok(app_state.book.to_dicts([pos])[0])

    @api.get(f"{API_PREFIX}/orders", dependencies=[Depends(auth)])
    async def orders():
        return ok(app_state.orders_payload())

    @api.get(f"{API_PREFIX}/signals", dependencies=[Depends(auth)])
    async def signals():
        return ok(app_state.signals_payload())

    @api.get(f"{API_PREFIX}/latency", dependencies=[Depends(auth)])
    async def latency():
        return ok(app_state.latency_payload())

    @api.get(f"{API_PREFIX}/recorder/stats", dependencies=[Depends(auth)])
    async def recorder_stats():
        return ok(app_state.recorder.stats() if app_state.recorder else {})

    @api.get(f"{API_PREFIX}/events", dependencies=[Depends(auth)])
    async def events(limit: int = Query(200, ge=1, le=2000)):
        return ok(app_state.events[-limit:])

    @api.get(f"{API_PREFIX}/logs", dependencies=[Depends(auth)])
    async def logs(limit: int = Query(200, ge=1, le=2000)):
        return ok(app_state.logs[-limit:])

    # ---------------- control ----------------

    @api.post(f"{API_PREFIX}/config", dependencies=[Depends(auth)])
    async def patch_config(patch: dict):
        from ..config.loader import ConfigError
        mid_session = app_state.scheduler.phase not in (
            Phase.BOOT, Phase.IDLE, Phase.PHASE_1)
        try:
            new_cfg, changed = app_state.store.apply_patch(
                patch, allow_structural=not mid_session)
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        app_state.on_config_changed(new_cfg, changed)
        app_state.store.save()
        return ok({"changed": changed})

    @api.post(f"{API_PREFIX}/config/validate", dependencies=[Depends(auth)])
    async def validate_config(patch: dict):
        from ..config.loader import ConfigError, merge_patch, parse
        try:
            parse(merge_patch(app_state.store.raw, patch))
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        return ok({"valid": True})

    @api.post(f"{API_PREFIX}/universe/manual", dependencies=[Depends(auth)])
    async def manual_instrument(body: dict):
        if has_passed(app_state.cfg.schedule.manual_cutoff):
            raise HTTPException(
                409, f"manual entry window closed at "
                     f"{app_state.cfg.schedule.manual_cutoff}")
        action = str(body.get("action", "add")).lower()
        symbol = str(body.get("symbol", "")).upper()
        if not symbol:
            raise HTTPException(400, "symbol is required")
        result = app_state.edit_manual(action, symbol, body)
        return ok(result)

    @api.post(f"{API_PREFIX}/control/arm", dependencies=[Depends(auth)])
    async def arm():
        app_state.feed.entries_enabled = True
        return ok({"armed": True})

    @api.post(f"{API_PREFIX}/control/disarm", dependencies=[Depends(auth)])
    async def disarm():
        app_state.feed.disarm()
        return ok({"armed": False})

    @api.post(f"{API_PREFIX}/control/phase", dependencies=[Depends(auth)])
    async def force_phase(body: dict):
        if app_state.cfg.trading_mode.is_live:
            raise HTTPException(409, "forcing a phase is refused in live mode")
        target = str(body.get("phase", "")).upper()
        if target not in {p.value for p in Phase}:
            raise HTTPException(400, f"unknown phase {target!r}")
        app_state.scheduler.transition(Phase(target), force=True)
        return ok({"phase": target})

    @api.post(f"{API_PREFIX}/positions/{{pos_id}}/exit", dependencies=[Depends(auth)])
    async def exit_one(pos_id: str):
        pos = app_state.book.get(pos_id)
        if pos is None:
            raise HTTPException(404, f"unknown position {pos_id}")
        if not pos.is_open:
            raise HTTPException(409, f"position {pos_id} is {pos.status}")
        queued = app_state.executor.request_exit(pos, ExitTrigger.MANUAL_API)
        if not queued:
            raise HTTPException(409, "already exiting")
        return ok({"pos_id": pos_id, "exiting": True})

    @api.post(f"{API_PREFIX}/control/exit_all", dependencies=[Depends(auth)])
    async def exit_all():
        n = app_state.exit_all(ExitTrigger.MANUAL_API)
        return ok({"exiting": n})

    @api.post(f"{API_PREFIX}/control/kill_switch", dependencies=[Depends(auth)])
    async def kill_switch(body: dict):
        if body.get("confirm") != "KILL":
            raise HTTPException(400, 'send {"confirm":"KILL"} to activate')
        n = app_state.kill_switch()
        return ok({"halted": True, "exiting": n})

    @api.post(f"{API_PREFIX}/control/reconcile", dependencies=[Depends(auth)])
    async def reconcile():
        return ok(app_state.reconcile_now())

    # ---------------- websocket ----------------

    @api.websocket(f"{API_PREFIX}/ws")
    async def ws_endpoint(ws: WebSocket, token: str = Query(default="")):
        expected = app_state.cfg.api.auth_token
        if expected and token != expected and not app_state.consume_ticket(token):
            await ws.close(code=4401)
            return
        await hub.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                op = msg.get("op")
                if op == "subscribe":
                    await hub.subscribe(ws, msg.get("topics") or [],
                                        app_state.snapshot_for)
                elif op == "unsubscribe":
                    hub.unsubscribe(ws, msg.get("topics") or [])
                elif op == "resync":
                    await hub.resync(ws, msg.get("topics") or [],
                                     app_state.snapshot_for)
                elif op == "ping":
                    await ws.send_text(json.dumps(
                        {"op": "pong", "ts": now_ist().isoformat()}))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.disconnect(ws)

    @api.get(f"{API_PREFIX}/broker/credentials", dependencies=[Depends(auth)])
    async def broker_credentials():
        """What credentials the server holds, masked. No secret is ever returned."""
        from ..config.loader import load_credentials
        try:
            creds = load_credentials(app_state.credentials_path)
        except Exception as exc:
            return ok({"error": f"{type(exc).__name__}: {exc}", "brokers": [],
                       "token_cache": None})
        brokers = sorted({str(cfg.broker.data_broker), str(cfg.broker.trade_broker)}
                         - {"paper"})
        return ok({
            "path": str(app_state.credentials_path),
            "data_broker": str(cfg.broker.data_broker),
            "trade_broker": str(cfg.broker.trade_broker),
            "brokers": credential_view(creds, brokers),
            "token_cache": token_cache_state(cfg.system.data_dir),
        })

    @api.get(f"{API_PREFIX}/broker/profiles", dependencies=[Depends(auth)])
    async def broker_profiles():
        """Every credential profile, masked, and which one is active."""
        from ..config.loader import profiles as load_profiles
        try:
            known, active = load_profiles(app_state.credentials_path)
        except Exception as exc:
            return ok({"error": f"{type(exc).__name__}: {exc}",
                       "profiles": [], "active": None})
        out = []
        for name, block in sorted(known.items()):
            view = credential_view({"zerodha": block}, ["zerodha"])[0]
            out.append({"name": name, "active": name == active,
                        "broker": str(block.get("broker") or "zerodha"),
                        "fields": view["fields"], "complete": view["complete"],
                        "missing": view["missing"]})
        return ok({"profiles": out, "active": active,
                   "path": str(app_state.credentials_path),
                   "token_cache": token_cache_state(cfg.system.data_dir)})

    @api.post(f"{API_PREFIX}/broker/profiles/{{name}}/activate",
              dependencies=[Depends(auth)])
    async def activate_profile(name: str):
        """Switch profiles. Takes effect on the next broker connect, not now."""
        from ..config.loader import set_active_profile
        try:
            chosen = set_active_profile(name, app_state.credentials_path)
        except Exception as exc:
            raise HTTPException(409, str(exc))
        app_state.log.info(f"active credential profile -> {chosen}")
        return ok({"active": chosen,
                   "note": "Applies at the next broker connect. Restart to use it "
                           "for the current session."})

    @api.post(f"{API_PREFIX}/broker/profiles/{{name}}/test",
              dependencies=[Depends(auth)])
    async def test_profile(name: str):
        """Authenticate one specific profile without switching to it."""
        from ..config.loader import load_credentials
        try:
            creds = load_credentials(app_state.credentials_path, profile=name)
        except Exception as exc:
            raise HTTPException(409, str(exc))
        # A non-active profile must not clobber the live token cache, so it gets a
        # scratch directory: a successful login there proves the credentials work
        # without replacing the session the engine is currently using.
        from ..config.loader import profiles as load_profiles
        _, active = load_profiles(app_state.credentials_path)
        data_dir = cfg.system.data_dir
        if name != active:
            data_dir = str(Path(cfg.system.data_dir) / "profile-probe" / name)
            Path(data_dir).mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(
            run_test, creds=creds, data_dir=data_dir,
            broker=str(creds.get("broker") or cfg.broker.data_broker),
            limiter=app_state.limiter)
        # run_test already returns the broker account under "profile"; naming the
        # credential set the same thing would overwrite the account details.
        result["profile_name"] = name
        # Only the ACTIVE profile's margin is the account we are trading, so only
        # that one may become the dashboard's live capital. Proving a second
        # account works must not repaint the figure the operator is watching.
        if name == active and result.get("capital"):
            app_state.capital = dict(result["capital"])
            app_state.capital["simulated"] = False
        return ok(result)

    @api.post(f"{API_PREFIX}/broker/test", dependencies=[Depends(auth)])
    async def broker_test():
        """Authenticate for real and exercise profile, margins and the master.

        Runs in a worker thread: a full TOTP login takes seconds and must not block
        the event loop that is also pushing ticks to this console.
        """
        from ..config.loader import load_credentials
        try:
            creds = load_credentials(app_state.credentials_path)
        except Exception as exc:
            raise HTTPException(409, f"Cannot read credentials: {exc}")

        result = await asyncio.to_thread(
            run_test, creds=creds, data_dir=cfg.system.data_dir,
            broker=str(cfg.broker.data_broker), limiter=app_state.limiter)

        # A successful margins call is the only way to know real capital before the
        # 08:45 session starts, so keep it for the dashboard.
        if result.get("capital"):
            app_state.capital = dict(result["capital"])
            app_state.capital["simulated"] = False
        app_state.log.info(
            "broker test: " + ", ".join(
                f"{c['name']}={'ok' if c['ok'] else 'FAIL'}" for c in result["checks"]))
        return ok(result)

    @api.post(f"{API_PREFIX}/broker/capital/refresh", dependencies=[Depends(auth)])
    async def capital_refresh():
        """Re-read broker margins. Paper mode returns the simulated view."""
        await asyncio.to_thread(app_state.refresh_capital)
        return ok(app_state.capital or app_state._paper_capital())

    @api.post(f"{API_PREFIX}/auth/login")
    async def login(body: dict):
        """Exchange a username and password for a session token.

        Deliberately vague on failure: a distinct "no such user" would let anyone
        enumerate valid usernames. Both branches also do the same amount of work
        where it matters -- an unknown user still costs a hash verification, so
        response time does not leak whether the account exists.
        """
        cfg = app_state.cfg.api
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            raise HTTPException(400, "username and password are required")
        if not cfg.users:
            raise HTTPException(
                409, "No accounts are configured. Add api.users in config.json, or "
                     "sign in with the api.auth_token instead.")
        if not cfg.auth_token:
            raise HTTPException(
                409, "api.auth_token must be set -- it is the session signing key.")

        user = find_user(cfg.users, username)
        # A dummy hash keeps the failure path as slow as the success path.
        stored = getattr(user, "password_hash", "") if user else _DUMMY_HASH
        if not verify_password(password, stored) or user is None:
            app_state.log.warning(f"failed sign-in for {username!r}")
            raise HTTPException(401, "Invalid username or password.")

        ttl = cfg.session_ttl_hours * 3600
        token = issue_session(user.username, cfg.auth_token, ttl_seconds=ttl)
        app_state.log.info(f"sign-in: {user.username}")
        return ok({"token": token, "username": user.username, "expires_in": ttl})

    @api.get(f"{API_PREFIX}/auth/whoami", dependencies=[Depends(auth)])
    async def whoami(authorization: str = Header(default="")):
        presented = authorization[7:] if authorization.startswith("Bearer ") else ""
        claims = read_session(presented, app_state.cfg.api.auth_token)
        return ok({"username": (claims or {}).get("u"),
                   "kind": "session" if claims else "api_token",
                   "expires_at": (claims or {}).get("exp")})

    @api.post(f"{API_PREFIX}/auth/ws-ticket", dependencies=[Depends(auth)])
    async def ws_ticket():
        return ok({"ticket": app_state.issue_ticket(), "expires_in": 60})

    return api


def serve(app_state) -> None:
    """Run uvicorn in a background thread."""
    import threading

    import uvicorn

    api = create_app(app_state)
    cfg = uvicorn.Config(api, host=app_state.cfg.api.host, port=app_state.cfg.api.port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    app_state.hub.loop_holder["server"] = server
    threading.Thread(target=server.run, name="APIServer", daemon=True).start()


__all__ = ["create_app", "serve", "cors_settings", "API_PREFIX", "ok", "err"]
