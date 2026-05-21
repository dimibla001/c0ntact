"""
Пример: подключение к API, извлечение монет, определение направления сделки.
Запуск: python trader_example.py
"""

import asyncio
import json
import re
import time
import websockets
from datetime import datetime, timezone

API_KEY = "sk-pro-changeme"
WS_URL  = f"ws://localhost:8080/listings?key={API_KEY}"

# ─────────────────────────────────────────────────────────────────────────────
# 1. ПАРСИНГ МОНЕТ
# ─────────────────────────────────────────────────────────────────────────────
#
# В сообщениях с /listings поле `coins` уже заполнено сервером coinlisting.pro
# Например:
#   {"source": "BINANCE", "coins": ["OPG", "GENIUS"], "title": "..."}
#
# Но иногда coins пустой или нам нужно вытащить тикер из title самим.

# Паттерны для ручного извлечения тикеров из title (fallback)
TICKER_PATTERN = re.compile(
    r'\b([A-Z]{2,10})'          # 2-10 заглавных букв
    r'(?=\s*[\(\),/]|\s+will|\s+token|\s+perpetual|\s+usdt)',
    re.IGNORECASE
)

# Стоп-слова — не тикеры
STOP_WORDS = {
    "USDT", "USDC", "BTC", "ETH", "BNB", "BUSD", "USD", "KRW", "EUR",
    "WILL", "LIST", "SPOT", "FUTURES", "MARKET", "TRADING", "LAUNCH",
    "BINANCE", "COINBASE", "UPBIT", "BITHUMB", "ALPHA", "NEW", "ADDING",
    "CONTRACT", "PERPETUAL", "MARGINED", "LEVERAGE", "MULTIPLE",
}

def extract_coins(event: dict) -> list[str]:
    """Возвращает список тикеров из события."""
    # Приоритет: поле coins от сервера
    coins = event.get("coins") or []
    if coins:
        return list(dict.fromkeys(c for c in coins if c not in STOP_WORDS))

    # Fallback: regex по title
    title = event.get("title", "")
    found = TICKER_PATTERN.findall(title)
    return list(dict.fromkeys(c.upper() for c in found if c.upper() not in STOP_WORDS))


# ─────────────────────────────────────────────────────────────────────────────
# 2. ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ СДЕЛКИ
# ─────────────────────────────────────────────────────────────────────────────
#
# Логика:
#   LONG  — когда биржа ДОБАВЛЯЕТ монету (листинг = рост спроса)
#   SHORT — когда биржа УБИРАЕТ монету (делистинг = паника, слив)
#   SKIP  — непонятное объявление, не торгуем

LONG_KEYWORDS = [
    "will list", "adds", "add ", "listing", "launch", "will launch",
    "new token", "will go live", "spot trading", "opens trading",
    "will open", "goes live", "introducing", " lists ", "lists ",
]

SHORT_KEYWORDS = [
    "delist", "will delist", "remove", "suspend trading",
    "discontinue", "halt trading", "trading suspension",
]

# Сила сигнала по источнику (от 1 до 10)
SOURCE_STRENGTH = {
    "BINANCE":       10,   # самый мощный эффект, спот
    "COINBASE":       8,   # "coinbase effect" — сильный памп
    "UPBIT":          6,   # корейский рынок, заметный эффект
    "BITHUMB":        5,   # меньше upbit
    "BINANCE_ALPHA":  4,   # альфа-листинги, менее надёжно
}

def determine_direction(event: dict) -> dict:
    """
    Возвращает:
      {
        "action":    "LONG" | "SHORT" | "SKIP",
        "coins":     ["BTC", ...],
        "source":    "BINANCE",
        "strength":  8,          # сила сигнала 1-10
        "reason":    "...",       # почему такое решение
        "latency_ms": 12          # сколько мс с момента детекта
      }
    """
    title  = (event.get("title") or "").lower()
    source = event.get("source", "")
    coins  = extract_coins(event)
    strength = SOURCE_STRENGTH.get(source, 3)

    # Считаем latency от момента детекта биржей до нас
    detected_iso = event.get("detected_at_iso", "")
    latency_ms = None
    if detected_iso:
        try:
            detected = datetime.fromisoformat(detected_iso)
            now = datetime.now(timezone.utc)
            latency_ms = int((now - detected).total_seconds() * 1000)
        except Exception:
            pass

    # Делистинг → SHORT
    for kw in SHORT_KEYWORDS:
        if kw in title:
            return {
                "action":     "SHORT",
                "coins":      coins,
                "source":     source,
                "strength":   strength,
                "reason":     f"delisting keyword: '{kw}'",
                "latency_ms": latency_ms,
            }

    # Листинг → LONG
    for kw in LONG_KEYWORDS:
        if kw in title:
            return {
                "action":     "LONG",
                "coins":      coins,
                "source":     source,
                "strength":   strength,
                "reason":     f"listing keyword: '{kw}'",
                "latency_ms": latency_ms,
            }

    # Непонятно — пропускаем
    return {
        "action":     "SKIP",
        "coins":      coins,
        "source":     source,
        "strength":   strength,
        "reason":     "no recognizable keywords",
        "latency_ms": latency_ms,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. ЗАГЛУШКА ДЛЯ ОТКРЫТИЯ ОРДЕРА
# ─────────────────────────────────────────────────────────────────────────────
#
# Здесь подключаешь свою биржу (ccxt, binance-connector, и т.д.)
# и открываешь реальный ордер.

async def open_trade(signal: dict) -> None:
    if signal["action"] == "SKIP":
        return
    if not signal["coins"]:
        print(f"  ⚠ Нет монет для сделки, пропускаем")
        return

    for coin in signal["coins"]:
        symbol = f"{coin}/USDT"
        side   = signal["action"]   # "LONG" или "SHORT"

        print(f"  📈 ОРДЕР → {side} {symbol}")
        print(f"     Источник:  {signal['source']} (сила {signal['strength']}/10)")
        print(f"     Причина:   {signal['reason']}")
        print(f"     Задержка:  {signal['latency_ms']} мс от детекта")

        # ── ЗДЕСЬ ПОДСТАВЬ РЕАЛЬНЫЙ ВЫЗОВ БИРЖИ ────────────────────────────
        # Пример с ccxt:
        #
        # import ccxt.async_support as ccxt
        # exchange = ccxt.binance({"apiKey": "...", "secret": "..."})
        # if side == "LONG":
        #     order = await exchange.create_market_buy_order(symbol, amount=100)  # $100
        # else:
        #     order = await exchange.create_market_sell_order(symbol, amount=100)
        # print(f"  ✅ Order ID: {order['id']}")
        # ────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 4. ОСНОВНОЙ ЦИКЛ
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    print(f"{ts} | {msg}")

async def run() -> None:
    log(f"Connecting to {WS_URL}")
    async for ws in websockets.connect(WS_URL):
        try:
            async for raw in ws:
                event = json.loads(raw)

                # Пропускаем служебные сообщения
                if event.get("type") in ("connection", "pong"):
                    log(f"Connected: tier={event.get('tier')} delay={event.get('delay_ms')}ms")
                    continue

                # Всё реальное
                source = event.get("source", "?")
                title  = event.get("title", "")[:80]
                coins  = extract_coins(event)
                signal = determine_direction(event)

                log(f"[{source}] {title}")
                log(f"  Монеты:     {coins}")
                log(f"  Действие:   {signal['action']} ({signal['reason']})")

                if signal["action"] != "SKIP":
                    await open_trade(signal)

                print()

        except websockets.ConnectionClosedError as e:
            if e.code == 1008:
                log(f"Auth error: {e.reason}")
                break
            log(f"Disconnected ({e.code}), reconnecting...")


if __name__ == "__main__":
    asyncio.run(run())
