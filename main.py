"""
Multi-session DM Guard Userbot (Telethon)

- Ek ya zyada STRING_SESSIONS (comma se alag) ek saath chalte hain.
- Koi unknown user DM kare -> block + poori chat delete (dono side).
- Har action LOGGER_GROUP_ID me log hota hai.
- OWNER_IDS (aur session accounts khud) `.free` / `.unfree` / `.list` use kar sakte hain.
- WHITELIST_IDS wale users ko bot kabhi delete/block nahi karega.
- Render ke liye /ping web server + self-ping (sleep se bachne ke liye).
"""

import asyncio
import json
import logging
import os
import re

from aiohttp import ClientSession, ClientTimeout, web
from telethon import TelegramClient, events, functions
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("dmguard")


# ---------------- config ----------------
def parse_ids(name: str) -> set:
    raw = os.getenv(name, "")
    return {int(x) for x in re.split(r"[,\s]+", raw) if x.lstrip("-").isdigit()}


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSIONS = [s.strip() for s in re.split(r"[,\s]+", os.getenv("STRING_SESSIONS", "")) if s.strip()]
OWNER_IDS = parse_ids("OWNER_IDS")
WHITELIST_IDS = parse_ids("WHITELIST_IDS")  # permanent whitelist (restart-safe)
LOGGER_GROUP_ID = int(os.getenv("LOGGER_GROUP_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
PING_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PING_URL", "")

ALLOW_CONTACTS = os.getenv("ALLOW_CONTACTS", "true").lower() == "true"
AUTO_ALLOW_OUTGOING = os.getenv("AUTO_ALLOW_OUTGOING", "true").lower() == "true"
DB_FILE = os.getenv("DB_FILE", "approved.json")

# ---------------- state ----------------
session_ids: set = set()  # sab session accounts ki IDs (auto trusted)
handled_cmds: set = set()  # ek command ka reply sirf ek baar


def load_approved() -> set:
    try:
        with open(DB_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


approved: set = load_approved()


def save_approved():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(sorted(approved), f)
    except Exception as e:
        log.warning("save failed: %s", e)


def trusted() -> set:
    return OWNER_IDS | session_ids


def is_safe(uid: int) -> bool:
    return uid == 777000 or uid in WHITELIST_IDS or uid in approved or uid in trusted()


def fmt_user(u) -> str:
    name = " ".join(filter(None, [getattr(u, "first_name", None), getattr(u, "last_name", None)])) or "Unknown"
    uname = f"@{u.username}" if getattr(u, "username", None) else "no-username"
    return f"{name} ({uname}) `{u.id}`"


# ---------------- per-account logic ----------------
async def get_logger_entity(client):
    if not LOGGER_GROUP_ID:
        return None
    try:
        return await client.get_entity(LOGGER_GROUP_ID)
    except Exception:
        try:
            await client.get_dialogs()  # cache refresh
            return await client.get_entity(LOGGER_GROUP_ID)
        except Exception as e:
            log.warning("Logger group nahi mila: %s", e)
            return None


def attach(client: TelegramClient, label: str, logger_ref: dict):
    async def send_log(text: str):
        ent = logger_ref.get("ent")
        if not ent:
            return
        try:
            await client.send_message(ent, text, link_preview=False)
        except Exception as e:
            log.warning("[%s] log fail: %s", label, e)

    # ---- guard ----
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def guard(event):
        try:
            sender = await event.get_sender()
            if not sender or getattr(sender, "bot", False) or is_safe(sender.id):
                return
            if ALLOW_CONTACTS and getattr(sender, "contact", False):
                return

            preview = (event.raw_text or "[media]")[:100]
            await client(functions.contacts.BlockRequest(id=sender.id))
            await client.delete_dialog(sender.id, revoke=True)
            log.info("[%s] blocked %s", label, sender.id)
            await send_log(
                f"🛡 **Blocked & Chat Deleted**\n"
                f"**Account:** {label}\n"
                f"**User:** {fmt_user(sender)}\n"
                f"**Msg:** {preview}"
            )
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            log.warning("[%s] guard error: %s", label, e)

    # ---- aap khud kisi ko msg karo -> auto approve ----
    @client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
    async def auto_allow(event):
        if not AUTO_ALLOW_OUTGOING or (event.raw_text or "").startswith("."):
            return
        if event.chat_id not in approved:
            approved.add(event.chat_id)
            save_approved()

    # ---- commands ----
    @client.on(events.NewMessage(pattern=r"^\.(free|unfree|list)(?:\s+(.+))?$"))
    async def commands(event):
        if event.sender_id not in trusted():
            return

        cmd = event.pattern_match.group(1)
        arg = (event.pattern_match.group(2) or "").strip()

        # target nikalo
        target = None
        if cmd != "list":
            try:
                if arg:
                    target = (await client.get_entity(int(arg) if arg.lstrip("-").isdigit() else arg)).id
                elif event.is_reply:
                    r = await event.get_reply_message()
                    target = r.sender_id if r else None
                elif event.is_private:
                    target = event.chat_id
            except Exception:
                target = None

        # reply sirf ek baar (multi-session me duplicate na ho)
        key = (event.chat_id, event.id)
        first = key not in handled_cmds
        handled_cmds.add(key)
        if len(handled_cmds) > 500:
            handled_cmds.clear()

        if cmd == "free":
            if not target:
                if first:
                    await event.reply("❌ User nahi mila. `.free <id/@username>` ya reply karke use karo.")
                return
            approved.add(target)
            save_approved()
            try:
                await client(functions.contacts.UnblockRequest(id=target))
            except Exception:
                pass
            if first:
                await event.reply(f"✅ `{target}` approved & unblocked. Ab wo DM kar sakta hai.")
                await send_log(f"✅ **Approved** `{target}` by `{event.sender_id}`")

        elif cmd == "unfree":
            if first:
                if target and target in approved:
                    approved.discard(target)
                    save_approved()
                    await event.reply(f"🚫 `{target}` approved list se hata diya.")
                    await send_log(f"🚫 **Unfree** `{target}` by `{event.sender_id}`")
                else:
                    await event.reply("❌ User approved list me nahi hai.")

        elif cmd == "list" and first:
            ap = "\n".join(f"`{u}`" for u in sorted(approved)) or "—"
            wl = "\n".join(f"`{u}`" for u in sorted(WHITELIST_IDS)) or "—"
            await event.reply(f"**Approved (.free):**\n{ap}\n\n**Whitelist (env):**\n{wl}")


# ---------------- web server (ping) ----------------
async def start_web():
    async def alive(_):
        return web.Response(text="alive")

    app = web.Application()
    app.router.add_get("/", alive)
    app.router.add_get("/ping", alive)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server port %s par chal raha hai", PORT)


async def self_ping():
    if not PING_URL:
        return
    url = PING_URL.rstrip("/") + "/ping"
    while True:
        await asyncio.sleep(600)  # har 10 min
        try:
            async with ClientSession(timeout=ClientTimeout(total=15)) as s:
                async with s.get(url) as r:
                    log.info("self-ping %s", r.status)
        except Exception as e:
            log.warning("self-ping fail: %s", e)


# ---------------- main ----------------
async def start_account(i: int, session: str):
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    try:
        await client.start()
        me = await client.get_me()
    except Exception as e:
        log.error("Session #%s start nahi hua: %s", i, e)
        return None

    session_ids.add(me.id)
    label = f"{me.first_name or 'Account'} ({me.id})"
    logger_ref = {"ent": await get_logger_entity(client)}
    attach(client, label, logger_ref)
    log.info("Session #%s chal raha hai: %s", i, label)

    if logger_ref["ent"]:
        try:
            await client.send_message(logger_ref["ent"], f"🟢 **Online:** {label}")
        except Exception:
            pass
    return client


async def main():
    await start_web()  # pehle port khol do (Render ke liye)
    asyncio.create_task(self_ping())

    if not (API_ID and API_HASH and SESSIONS):
        log.error("API_ID, API_HASH, STRING_SESSIONS set karo. Web server chalu rahega.")
        await asyncio.Event().wait()

    clients = [c for c in await asyncio.gather(*(start_account(i + 1, s) for i, s in enumerate(SESSIONS))) if c]
    if not clients:
        log.error("Koi session start nahi hua.")
        await asyncio.Event().wait()

    await asyncio.gather(*(c.run_until_disconnected() for c in clients))


if __name__ == "__main__":
    asyncio.run(main())
