import asyncio
import json
import os
from datetime import datetime

import httpx
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")
TIERS = ("pro", "basic", "trial", "free")


# ── config helpers ────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_cfg(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── inline keyboard helper ────────────────────────────────────────────────────

def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])


# ── server helpers ────────────────────────────────────────────────────────────

async def api_health(cfg: dict) -> dict | None:
    url = cfg.get("bot", {}).get("server_url", "http://localhost:8080")
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{url}/health")
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def api_history(cfg: dict, channel: str = "listings", limit: int = 5) -> list:
    url = cfg.get("bot", {}).get("server_url", "http://localhost:8080")
    key = cfg.get("bot", {}).get("admin_key", "")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{url}/history", params={"key": key, "channel": channel, "limit": limit})
            if r.status_code == 200:
                return r.json().get("events", [])
    except Exception:
        pass
    return []


# ── auth middleware ───────────────────────────────────────────────────────────

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            cfg = load_cfg()
            allowed = cfg.get("bot", {}).get("allowed_ids", [])
            if allowed and user.id not in allowed:
                obj = event if isinstance(event, Message) else getattr(event, "message", None)
                if obj:
                    await obj.answer("⛔ Access denied")
                return
        return await handler(event, data)


# ── FSM states ────────────────────────────────────────────────────────────────

class AddKey(StatesGroup):
    key = State()
    username = State()
    tier = State()
    expiry = State()


class EditExpiry(StatesGroup):
    waiting = State()


# ── dispatcher ────────────────────────────────────────────────────────────────

dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    await msg.answer(
        "🔧 <b>CoinListing API Manager</b>\n\n"
        "<b>Сервер</b>\n"
        "/status — статус и аптайм\n"
        "/subs — активные подписчики\n"
        "/history [n] — последние n событий\n\n"
        "<b>Ключи</b>\n"
        "/keys — список всех ключей\n"
        "/key &lt;key&gt; — инфо + управление\n"
        "/addkey — добавить ключ (диалог)\n"
        "/delkey &lt;key&gt; — удалить ключ\n"
        "/settier &lt;key&gt; &lt;tier&gt; — изменить тир\n"
        "/setexpiry &lt;key&gt; &lt;date|never&gt; — expiry\n\n"
        "<b>Тиры:</b> pro · basic · trial · free",
        parse_mode="HTML",
    )


# ── /status ───────────────────────────────────────────────────────────────────

@dp.message(Command("status"))
async def cmd_status(msg: Message):
    cfg = load_cfg()
    health = await api_health(cfg)
    keys_count = len(cfg.get("keys", {}))
    upstream = "✅ задан" if cfg.get("upstream_key") else "❌ не задан"

    if not health:
        await msg.answer(
            f"❌ <b>Сервер недоступен</b>\n\n"
            f"🔑 Ключей: <b>{keys_count}</b>\n"
            f"🌐 Upstream key: <b>{upstream}</b>",
            parse_mode="HTML",
        )
        return

    subs = health.get("subscribers", {})
    await msg.answer(
        f"✅ <b>Сервер онлайн</b>\n\n"
        f"🕐 {health.get('time', '—')[:19].replace('T', ' ')} UTC\n"
        f"📡 Feed subscribers: <b>{subs.get('feed', 0)}</b>\n"
        f"📋 Listings subscribers: <b>{subs.get('listings', 0)}</b>\n"
        f"🔑 Ключей: <b>{keys_count}</b>\n"
        f"🌐 Upstream: <b>{upstream}</b>",
        parse_mode="HTML",
    )


# ── /subs ─────────────────────────────────────────────────────────────────────

@dp.message(Command("subs"))
async def cmd_subs(msg: Message):
    health = await api_health(load_cfg())
    if not health:
        await msg.answer("❌ Сервер недоступен")
        return
    subs = health.get("subscribers", {})
    await msg.answer(
        f"📡 <b>Подписчики</b>\n\n"
        f"Feed:     <b>{subs.get('feed', 0)}</b>\n"
        f"Listings: <b>{subs.get('listings', 0)}</b>",
        parse_mode="HTML",
    )


# ── /history ──────────────────────────────────────────────────────────────────

@dp.message(Command("history"))
async def cmd_history(msg: Message):
    parts = msg.text.split()
    limit = min(int(parts[1]), 20) if len(parts) > 1 and parts[1].isdigit() else 5
    cfg = load_cfg()
    events = await api_history(cfg, limit=limit)
    if not events:
        await msg.answer("Нет событий (или admin_key не задан / сервер недоступен)")
        return
    lines = []
    for e in events:
        src = e.get("source", "?")
        title = (e.get("title") or "")[:100]
        coins = e.get("coins", [])
        t = (e.get("detected_at_iso") or "")[:19].replace("T", " ")
        coin_str = f"  🪙 <b>{' '.join(coins)}</b>" if coins else ""
        lines.append(f"<code>{t}</code> [{src}]{coin_str}\n{title}")
    await msg.answer("\n\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ── /keys ─────────────────────────────────────────────────────────────────────

@dp.message(Command("keys"))
async def cmd_keys(msg: Message):
    keys = load_cfg().get("keys", {})
    if not keys:
        await msg.answer("Ключей нет. Добавь через /addkey")
        return
    lines = []
    for k, v in keys.items():
        tier = v.get("tier", "free")
        exp = (v.get("expiry") or "—")[:10]
        user = v.get("username", "—")
        icon = {"pro": "🟢", "basic": "🔵", "trial": "🟡", "free": "⚪"}.get(tier, "⚫")
        lines.append(f"{icon} <code>{k}</code>\n   👤 {user} | ⏳ {exp}")
    await msg.answer(
        f"🔑 <b>API ключи ({len(keys)}):</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )


# ── /key ──────────────────────────────────────────────────────────────────────

@dp.message(Command("key"))
async def cmd_key(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /key &lt;ключ&gt;", parse_mode="HTML")
        return
    key = args[1].strip()
    cfg = load_cfg()
    info = cfg.get("keys", {}).get(key)
    if not info:
        await msg.answer(f"❌ Ключ не найден: <code>{key}</code>", parse_mode="HTML")
        return
    tier = info.get("tier", "free")
    icon = {"pro": "🟢", "basic": "🔵", "trial": "🟡", "free": "⚪"}.get(tier, "⚫")
    await msg.answer(
        f"🔑 <code>{key}</code>\n\n"
        f"👤 Username: <b>{info.get('username', '—')}</b>\n"
        f"{icon} Tier: <b>{tier}</b>\n"
        f"⏳ Expiry: <b>{info.get('expiry', '—')}</b>",
        parse_mode="HTML",
        reply_markup=kb([
            [("✏️ Tier", f"tiermenu:{key}"), ("📅 Expiry", f"expirymenu:{key}")],
            [("❌ Удалить", f"confirmdelete:{key}")],
        ]),
    )


# ── /addkey (FSM) ─────────────────────────────────────────────────────────────

@dp.message(Command("addkey"))
async def cmd_addkey(msg: Message, state: FSMContext):
    await msg.answer("Введи имя нового ключа (например: <code>sk-user-01</code>):", parse_mode="HTML")
    await state.set_state(AddKey.key)


@dp.message(AddKey.key)
async def fsm_key(msg: Message, state: FSMContext):
    k = msg.text.strip()
    cfg = load_cfg()
    if k in cfg.get("keys", {}):
        await msg.answer(f"⚠️ Ключ <code>{k}</code> уже существует. Введи другое имя:", parse_mode="HTML")
        return
    await state.update_data(key=k)
    await msg.answer("Введи username для этого ключа:")
    await state.set_state(AddKey.username)


@dp.message(AddKey.username)
async def fsm_username(msg: Message, state: FSMContext):
    await state.update_data(username=msg.text.strip())
    await msg.answer(
        "Выбери тир:",
        reply_markup=kb([
            [("🟢 pro", "newtier:pro"), ("🔵 basic", "newtier:basic")],
            [("🟡 trial", "newtier:trial"), ("⚪ free", "newtier:free")],
        ]),
    )
    await state.set_state(AddKey.tier)


@dp.callback_query(F.data.startswith("newtier:"), AddKey.tier)
async def fsm_tier(call: CallbackQuery, state: FSMContext):
    tier = call.data.split(":", 1)[1]
    await state.update_data(tier=tier)
    await call.message.edit_text(
        f"Тир: <b>{tier}</b>\n\n"
        "Введи дату окончания <code>YYYY-MM-DD</code> или <code>never</code>:",
        parse_mode="HTML",
    )
    await state.set_state(AddKey.expiry)


@dp.message(AddKey.expiry)
async def fsm_expiry(msg: Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() == "never":
        expiry = "2099-12-31 00:00:00"
    else:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            expiry = raw + " 00:00:00"
        except ValueError:
            await msg.answer("❌ Неверный формат. Введи <code>YYYY-MM-DD</code> или <code>never</code>:", parse_mode="HTML")
            return
    data = await state.get_data()
    cfg = load_cfg()
    cfg.setdefault("keys", {})[data["key"]] = {
        "username": data["username"],
        "tier": data["tier"],
        "expiry": expiry,
    }
    save_cfg(cfg)
    await state.clear()
    icon = {"pro": "🟢", "basic": "🔵", "trial": "🟡", "free": "⚪"}.get(data["tier"], "⚫")
    await msg.answer(
        f"✅ <b>Ключ добавлен</b>\n\n"
        f"<code>{data['key']}</code>\n"
        f"👤 {data['username']} | {icon} {data['tier']} | ⏳ {expiry}",
        parse_mode="HTML",
    )


# ── /delkey ───────────────────────────────────────────────────────────────────

@dp.message(Command("delkey"))
async def cmd_delkey(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /delkey &lt;ключ&gt;", parse_mode="HTML")
        return
    key = args[1].strip()
    cfg = load_cfg()
    if key not in cfg.get("keys", {}):
        await msg.answer(f"❌ Ключ не найден: <code>{key}</code>", parse_mode="HTML")
        return
    del cfg["keys"][key]
    save_cfg(cfg)
    await msg.answer(f"✅ Ключ <code>{key}</code> удалён", parse_mode="HTML")


# ── /settier ──────────────────────────────────────────────────────────────────

@dp.message(Command("settier"))
async def cmd_settier(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("Использование: /settier &lt;key&gt; &lt;pro|basic|trial|free&gt;", parse_mode="HTML")
        return
    key, tier = parts[1], parts[2]
    if tier not in TIERS:
        await msg.answer("❌ Доступные тиры: pro, basic, trial, free")
        return
    cfg = load_cfg()
    if key not in cfg.get("keys", {}):
        await msg.answer("❌ Ключ не найден")
        return
    cfg["keys"][key]["tier"] = tier
    save_cfg(cfg)
    await msg.answer(f"✅ <code>{key}</code> → тир <b>{tier}</b>", parse_mode="HTML")


# ── /setexpiry ────────────────────────────────────────────────────────────────

@dp.message(Command("setexpiry"))
async def cmd_setexpiry(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("Использование: /setexpiry &lt;key&gt; &lt;YYYY-MM-DD|never&gt;", parse_mode="HTML")
        return
    key, raw = parts[1], parts[2]
    if raw.lower() == "never":
        expiry = "2099-12-31 00:00:00"
    else:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            expiry = raw + " 00:00:00"
        except ValueError:
            await msg.answer("❌ Формат: YYYY-MM-DD")
            return
    cfg = load_cfg()
    if key not in cfg.get("keys", {}):
        await msg.answer("❌ Ключ не найден")
        return
    cfg["keys"][key]["expiry"] = expiry
    save_cfg(cfg)
    await msg.answer(f"✅ <code>{key}</code> expiry → <b>{expiry}</b>", parse_mode="HTML")


# ── inline callbacks ──────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("tiermenu:"))
async def cb_tiermenu(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Выбери тир для <code>{key}</code>:",
        parse_mode="HTML",
        reply_markup=kb([
            [("🟢 pro", f"settier_cb:{key}:pro"), ("🔵 basic", f"settier_cb:{key}:basic")],
            [("🟡 trial", f"settier_cb:{key}:trial"), ("⚪ free", f"settier_cb:{key}:free")],
            [("↩️ Назад", f"keyinfo:{key}")],
        ]),
    )


@dp.callback_query(F.data.startswith("settier_cb:"))
async def cb_settier(call: CallbackQuery):
    _, key, tier = call.data.split(":", 2)
    cfg = load_cfg()
    if key not in cfg.get("keys", {}):
        await call.answer("Ключ не найден", show_alert=True)
        return
    cfg["keys"][key]["tier"] = tier
    save_cfg(cfg)
    await call.answer(f"Тир обновлён → {tier}")
    await _show_key(call.message, key, edit=True)


@dp.callback_query(F.data.startswith("expirymenu:"))
async def cb_expirymenu(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":", 1)[1]
    await state.update_data(edit_key=key, edit_msg_id=call.message.message_id)
    await call.message.edit_text(
        f"Введи новый expiry для <code>{key}</code>\n"
        "Формат: <code>YYYY-MM-DD</code> или <code>never</code>",
        parse_mode="HTML",
    )
    await state.set_state(EditExpiry.waiting)


@dp.message(EditExpiry.waiting)
async def fsm_edit_expiry(msg: Message, state: FSMContext):
    raw = msg.text.strip()
    if raw.lower() == "never":
        expiry = "2099-12-31 00:00:00"
    else:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            expiry = raw + " 00:00:00"
        except ValueError:
            await msg.answer("❌ Формат: <code>YYYY-MM-DD</code> или <code>never</code>:", parse_mode="HTML")
            return
    data = await state.get_data()
    key = data["edit_key"]
    cfg = load_cfg()
    if key not in cfg.get("keys", {}):
        await msg.answer("❌ Ключ не найден")
        await state.clear()
        return
    cfg["keys"][key]["expiry"] = expiry
    save_cfg(cfg)
    await state.clear()
    await msg.answer(f"✅ <code>{key}</code> expiry → <b>{expiry}</b>", parse_mode="HTML")


@dp.callback_query(F.data.startswith("confirmdelete:"))
async def cb_confirmdelete(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Удалить ключ <code>{key}</code>?",
        parse_mode="HTML",
        reply_markup=kb([[
            ("✅ Да, удалить", f"dodelete:{key}"),
            ("❌ Отмена", f"keyinfo:{key}"),
        ]]),
    )


@dp.callback_query(F.data.startswith("dodelete:"))
async def cb_dodelete(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    cfg = load_cfg()
    if key in cfg.get("keys", {}):
        del cfg["keys"][key]
        save_cfg(cfg)
        await call.message.edit_text(f"✅ Ключ <code>{key}</code> удалён", parse_mode="HTML")
    else:
        await call.answer("Ключ уже удалён", show_alert=True)


@dp.callback_query(F.data.startswith("keyinfo:"))
async def cb_keyinfo(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    await _show_key(call.message, key, edit=True)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _show_key(msg: Message, key: str, edit: bool = False) -> None:
    cfg = load_cfg()
    info = cfg.get("keys", {}).get(key)
    if not info:
        text = f"❌ Ключ <code>{key}</code> не найден"
        if edit:
            await msg.edit_text(text, parse_mode="HTML")
        else:
            await msg.answer(text, parse_mode="HTML")
        return
    tier = info.get("tier", "free")
    icon = {"pro": "🟢", "basic": "🔵", "trial": "🟡", "free": "⚪"}.get(tier, "⚫")
    text = (
        f"🔑 <code>{key}</code>\n\n"
        f"👤 Username: <b>{info.get('username', '—')}</b>\n"
        f"{icon} Tier: <b>{tier}</b>\n"
        f"⏳ Expiry: <b>{info.get('expiry', '—')}</b>"
    )
    markup = kb([
        [("✏️ Tier", f"tiermenu:{key}"), ("📅 Expiry", f"expirymenu:{key}")],
        [("❌ Удалить", f"confirmdelete:{key}")],
    ])
    if edit:
        await msg.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await msg.answer(text, parse_mode="HTML", reply_markup=markup)


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    cfg = load_cfg()
    token = cfg.get("bot", {}).get("token", "")
    if not token:
        print("[bot] bot.token не задан в config.json — бот не запущен")
        return
    bot = Bot(token=token)
    print("[bot] polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
