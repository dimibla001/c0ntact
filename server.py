import asyncio
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import JSONResponse

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)


app = FastAPI(title="CoinListing API")

TIER_DELAY_MS: dict[str, int] = {"pro": 0, "basic": 10, "trial": 0, "free": 250}
SOURCES = ["BINANCE", "UPBIT", "BITHUMB", "COINBASE", "BINANCE_ALPHA"]

_history: dict[str, deque] = {
    "feed": deque(maxlen=500),
    "listings": deque(maxlen=500),
}
_subscribers: dict[str, list] = {"feed": [], "listings": []}
_active_connections: dict[str, list] = defaultdict(list)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def _broadcast(msg: dict, channel: str) -> None:
    _history[channel].append(msg)
    for sub in list(_subscribers[channel]):
        delay = TIER_DELAY_MS.get(sub["tier"], 250) / 1000.0
        asyncio.create_task(_send_after(sub["ws"], msg, delay))


async def _send_after(ws: WebSocket, msg: dict, delay: float) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await ws.send_json(msg)
    except Exception:
        pass


async def _upstream_loop(url: str, channel: str) -> None:
    backoff = 1
    while True:
        try:
            async for ws in websockets.connect(url, ping_interval=20, ping_timeout=30):
                print(f"[upstream/{channel}] connected")
                backoff = 1
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") in ("connection", "pong"):
                            continue
                        await _broadcast(msg, channel)
                except websockets.ConnectionClosedError as e:
                    if e.code == 1008:
                        print(f"[upstream/{channel}] auth error: {e.reason} — stopping")
                        return
                    # re-raise so __aiter__ treats it as fatal and exits the
                    # async-for loop — the outer while-True then applies backoff
                    raise
        except websockets.ConnectionClosedError as e:
            if e.code == 1008:
                return
            print(f"[upstream/{channel}] closed ({e.code}), retry in {backoff}s")
        except Exception as exc:
            print(f"[upstream/{channel}] connect error: {exc}, retry in {backoff}s")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


@app.on_event("startup")
async def _startup() -> None:
    try:
        cfg = load_config()
    except FileNotFoundError:
        print(f"[startup] {CONFIG_FILE} not found — create it, then restart")
        return
    key = cfg.get("upstream_key", "")
    if not key:
        print("[startup] upstream_key not set — no upstream feed (standalone mode)")
        return
    base = cfg.get("upstream_host", "wss://tokyo.coinlisting.pro")
    asyncio.create_task(_upstream_loop(f"{base}/feed?key={key}", "feed"))
    asyncio.create_task(_upstream_loop(f"{base}/listings?key={key}", "listings"))


def _check_auth(ws: WebSocket, channel: str) -> tuple[dict, str]:
    try:
        cfg = load_config()
    except Exception as exc:
        raise ValueError((1011, "Server config error")) from exc

    api_key = ws.query_params.get("key")
    if not api_key:
        raise ValueError((1008, "Missing key"))

    if channel not in ("feed", "listings"):
        raise ValueError((1008, "Unknown channel"))

    info: Optional[dict] = cfg.get("keys", {}).get(api_key)
    if not info:
        raise ValueError((1008, "License rejected"))

    expiry = info.get("expiry", "")
    if expiry and expiry < datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        raise ValueError((1008, "License expired"))

    tier = info.get("tier", "free")
    if tier == "trial" and channel == "feed":
        raise ValueError((1008, "Trial: /feed not available"))

    if len(_active_connections[api_key]) >= 10:
        raise ValueError((1008, "Too many connections"))

    return info, api_key


async def _handle_ws(ws: WebSocket, channel: str) -> None:
    await ws.accept()

    try:
        key_info, api_key = _check_auth(ws, channel)
    except ValueError as exc:
        code, reason = exc.args[0]
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass
        return

    tier = key_info.get("tier", "free")
    delay_ms = TIER_DELAY_MS.get(tier, 250)

    await ws.send_json({
        "type": "connection",
        "status": "connected",
        "username": key_info.get("username", api_key[:8]),
        "expiry": key_info.get("expiry", "2099-12-31 00:00:00"),
        "tier": tier,
        "delay_ms": delay_ms,
        "sources": SOURCES,
        "sent_time": _now_ms(),
        "sent_time_iso": _now_iso(),
    })

    sub = {"ws": ws, "tier": tier}
    _subscribers[channel].append(sub)
    _active_connections[api_key].append(ws)

    try:
        while True:
            raw = await ws.receive_text()
            if raw.strip() == "ping":
                await ws.send_text("pong")
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await ws.send_json({
                    "type": "pong",
                    "sent_time": _now_ms(),
                    "sent_time_iso": _now_iso(),
                })
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _subscribers[channel][:] = [
            s for s in _subscribers[channel] if s["ws"] is not ws
        ]
        _active_connections[api_key] = [
            w for w in _active_connections[api_key] if w is not ws
        ]
        if not _active_connections[api_key]:
            del _active_connections[api_key]


@app.websocket("/feed")
async def ws_feed(websocket: WebSocket) -> None:
    await _handle_ws(websocket, "feed")


@app.websocket("/listings")
async def ws_listings(websocket: WebSocket) -> None:
    await _handle_ws(websocket, "listings")


@app.get("/history")
async def get_history(
    key: str = Query(...),
    channel: str = Query("listings"),
    limit: int = Query(100, ge=1, le=500),
) -> JSONResponse:
    try:
        cfg = load_config()
    except Exception:
        raise HTTPException(status_code=500, detail="Config error")

    info = cfg.get("keys", {}).get(key)
    if not info:
        raise HTTPException(status_code=403, detail="License rejected")

    expiry = info.get("expiry", "")
    if expiry and expiry < datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        raise HTTPException(status_code=403, detail="License expired")

    if channel not in ("feed", "listings"):
        raise HTTPException(status_code=400, detail="Unknown channel")

    events = list(_history[channel])[-limit:]
    return JSONResponse({"events": events, "count": len(events)})


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": _now_iso(),
        "subscribers": {ch: len(subs) for ch, subs in _subscribers.items()},
    }
