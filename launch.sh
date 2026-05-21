#!/usr/bin/env bash
set -e

PORT="${PORT:-8080}"
export CONFIG_FILE="${CONFIG_FILE:-config.json}"

# ── deps ──────────────────────────────────────────────────────────────────────
echo "[*] Installing dependencies..."
pip install -r requirements.txt -q --disable-pip-version-check

# ── public host ───────────────────────────────────────────────────────────────
HOST=$(curl -s --max-time 4 ifconfig.me 2>/dev/null \
     || curl -s --max-time 4 api.ipify.org 2>/dev/null \
     || hostname -I 2>/dev/null | awk '{print $1}' \
     || echo "localhost")

# ── banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║            CoinListing Proxy API — ready to use             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  WebSocket /listings  →  ws://%-31s║\n" "${HOST}:${PORT}/listings?key=YOUR_KEY"
printf  "║  WebSocket /feed      →  ws://%-31s║\n" "${HOST}:${PORT}/feed?key=YOUR_KEY"
printf  "║  History HTTP         →  http://%-29s║\n" "${HOST}:${PORT}/history?key=YOUR_KEY"
printf  "║  Health check         →  http://%-29s║\n" "${HOST}:${PORT}/health"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  Config file: %-47s║\n" "${CONFIG_FILE}"
printf  "║  Port:        %-47s║\n" "${PORT}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "[*] Starting server..."
echo ""

exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --log-level info
