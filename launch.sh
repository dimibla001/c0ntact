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
echo "║         CoinListing Proxy API + Telegram Bot                ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  WebSocket /listings  →  ws://%-31s║\n" "${HOST}:${PORT}/listings?key=YOUR_KEY"
printf  "║  WebSocket /feed      →  ws://%-31s║\n" "${HOST}:${PORT}/feed?key=YOUR_KEY"
printf  "║  History HTTP         →  http://%-29s║\n" "${HOST}:${PORT}/history?key=YOUR_KEY"
printf  "║  Health check         →  http://%-29s║\n" "${HOST}:${PORT}/health"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  Config:  %-51s║\n" "${CONFIG_FILE}"
printf  "║  Port:    %-51s║\n" "${PORT}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── graceful shutdown ─────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "[*] Shutting down..."
    kill "$SERVER_PID" "$BOT_PID" 2>/dev/null || true
    wait "$SERVER_PID" "$BOT_PID" 2>/dev/null || true
    echo "[*] Done"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── start API server ──────────────────────────────────────────────────────────
echo "[*] Starting API server on port ${PORT}..."
uvicorn server:app --host 0.0.0.0 --port "$PORT" --log-level info &
SERVER_PID=$!

# ── start Telegram bot ────────────────────────────────────────────────────────
echo "[*] Starting Telegram bot..."
python bot.py &
BOT_PID=$!

# ── wait ──────────────────────────────────────────────────────────────────────
echo "[*] Both processes running. Press Ctrl+C to stop."
echo ""
wait "$SERVER_PID" "$BOT_PID"
