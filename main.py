"""
Multi-session DM Guard Userbot (PYROGRAM / Pyrofork)

- Ek ya zyada Pyrogram STRING_SESSIONS (comma se alag) ek saath chalte hain.
- Unknown user DM kare -> block + poori chat delete (dono side).
- Har action LOGGER_GROUP_ID me log hota hai.
- OWNER_IDS (aur session accounts) `.free` / `.unfree` / `.list` use kar sakte hain.
- WHITELIST_IDS ko bot kabhi delete/block nahi karega.
- Render ke liye /ping web server + self-ping.
"""

import asyncio
import html
import json
import logging
import os
import re

from aiohttp import ClientSession, ClientTimeout, web
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.raw import functions

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("dmguard")
logging.getLogger("pyrogram").setLevel(logging.WARNING)


# ---------------- config ----------------
def parse_ids(name: str) -> set:
    raw = os.getenv(name, "")
    return {int(x) for x in re.split(r"[,\s]+", raw) if x.lstrip("-").isdigit()}


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSIONS = [s.strip().strip("\"'") for s in re.split(r"[,\s]+", os.getenv("STRING_SESSIONS", "")) if s.strip().strip("\"'")]
OWNER_IDS = parse_ids("OWNER_IDS")
WHITELIST_IDS = parse_ids("WHITELIST_IDS")
LOGGER_GROUP_ID = int(os.getenv("LOGGER_GROUP_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
PING_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PING_URL", "")

ALLOW_CONTACTS = os.getenv("ALLOW_CONTACTS", "true").lower() == "true"
AUTO_ALLOW_OUTGOING = os.getenv("AUTO_ALLOW_OUTGOING", "true").lower() == "true"
DB_FILE = os.getenv("DB_FILE", "approved.json")

# ---------------- state ----------------
session_ids: set = set()
handled_cmds: set = set()


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
    name = " ".join(filter(None, [u.first_name, u.last_name])) or "Unknown"
    uname = f"@{u.username}" if u.username else "no-username"
    return f"{html.escape(name)} ({html.escape(uname)}) <code>{u.id}</code>"


async def wipe_chat(app: Client, uid: int):
    """Poori chat dono side se delete."""
    peer = await app.resolve_peer(uid)
    while True:
        r = await app.invoke(functions.messages.DeleteHistory(peer=peer, max_id=0, revoke=True))
        if not getattr(r, "offset", 0):
            break


# ---------------- per-account logic ----------------
def attach(app: Client, label: str):
    async def send_log(text: str):
        if not LOGGER_GROUP_ID:
            return
        try:
            await app.send_message(LOGGER_GROUP_ID, text, disable_web_page_preview=True)
        except Exception as e:
            log.warning("[%s] log fail: %s", label, e)

    # ---- guard (group 0) ----
    @app.on_message(filters.private & filters.incoming, group=0)
    async def guard(_, m):
        try:
            u = m.from_user
            if not u or u.is_bot or is_safe(u.id):
                return
            if ALLOW_CONTACTS and getattr(u, "is_contact", False):
                return

            preview = html.escape((m.text or m.caption or "[media]")[:100])
            await app.block_user(u.id)
            await wipe_chat(app, u.id)
            log.info("[%s] blocked %s", label, u.id)
            await send_log(
                f"🛡 <b>Blocked &amp; Chat Deleted</b>\n"
                f"<b>Account:</b> {html.escape(label)}\n"
                f"<b>User:</b> {fmt_user(u)}\n"
                f"<b>Msg:</b> {preview}"
            )
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            log.warning("[%s] guard error: %s", label, e)

    # ---- aap khud msg karo -> auto approve (group 1) ----
    @app.on_message(filters.private & filters.outgoing, group=1)
    async def auto_allow(_, m):
        if not AUTO_ALLOW_OUTGOING or (m.text or "").startswith("."):
            return
        if m.chat.id not in approved:
            approved.add(m.chat.id)
            save_approved()

    # ---- commands (group 2) ----
    @app.on_message(filters.text & filters.regex(r"^\.(free|unfree|list)(?:\s+(.+))?$"), group=2)
    async def commands(_, m):
        if not m.from_user or m.from_user.id not in trusted():
            return

        cmd = m.matches[0].group(1)
        arg = (m.matches[0].group(2) or "").strip()

        target = None
        if cmd != "list":
            try:
                if arg:
                    target = (await app.get_users(int(arg) if arg.lstrip("-").isdigit() else arg)).id
                elif m.reply_to_message and m.reply_to_message.from_user:
                    target = m.reply_to_message.from_user.id
                elif m.chat.type.name == "PRIVATE":
                    target = m.chat.id
            except Exception:
                target = None

        key = (m.chat.id, m.id)
        first = key not in handled_cmds
        handled_cmds.add(key)
        if len(handled_cmds) > 500:
            handled_cmds.clear()

        if cmd == "free":
            if not target:
                if first:
                    await m.reply_text("❌ User nahi mila. <code>.free &lt;id/@username&gt;</code> ya reply karke use karo.")
                return
            approved.add(target)
            save_approved()
            try:
                await app.unblock_user(target)
            except Exception:
                pass
            if first:
                await m.reply_text(f"✅ <code>{target}</code> approved &amp; unblocked. Ab wo DM kar sakta hai.")
                await send_log(f"✅ <b>Approved</b> <code>{target}</code> by <code>{m.from_user.id}</code>")

        elif cmd == "unfree" and first:
            if target and target in approved:
                approved.discard(target)
                save_approved()
                await m.reply_text(f"🚫 <code>{target}</code> approved list se hata diya.")
                await send_log(f"🚫 <b>Unfree</b> <code>{target}</code> by <code>{m.from_user.id}</code>")
            else:
                await m.reply_text("❌ User approved list me nahi hai.")

        elif cmd == "list" and first:
            ap = "\n".join(f"<code>{u}</code>" for u in sorted(approved)) or "—"
            wl = "\n".join(f"<code>{u}</code>" for u in sorted(WHITELIST_IDS)) or "—"
            await m.reply_text(f"<b>Approved (.free):</b>\n{ap}\n\n<b>Whitelist (env):</b>\n{wl}")


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
        await asyncio.sleep(600)
        try:
            async with ClientSession(timeout=ClientTimeout(total=15)) as s:
                async with s.get(url) as r:
                    log.info("self-ping %s", r.status)
        except Exception as e:
            log.warning("self-ping fail: %s", e)


# ---------------- main ----------------
async def start_account(i: int, session: str):
    try:
        app = Client(
            f"acc{i}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session,
            in_memory=True,
            no_updates=False,
        )
        await app.start()
        me = await app.get_me()
    except Exception as e:
        log.error(
            "Session #%s start nahi hua (%s). Ye Pyrogram string session honi chahiye; "
            "galat/expire ho to gen_session.py se nayi banao.", i, e
        )
        return None

    session_ids.add(me.id)
    label = f"{me.first_name or 'Account'} ({me.id})"

    # peers cache karo taaki logger group mil jaye
    try:
        async for _ in app.get_dialogs():
            pass
    except Exception as e:
        log.warning("[%s] dialogs load fail: %s", label, e)

    attach(app, label)
    log.info("Session #%s chal raha hai: %s", i, label)

    if LOGGER_GROUP_ID:
        try:
            await app.send_message(LOGGER_GROUP_ID, f"🟢 <b>Online:</b> {html.escape(label)}")
        except Exception as e:
            log.warning("[%s] Logger group nahi mila/msg nahi gaya: %s", label, e)
    return app


async def main():
    await start_web()
    asyncio.create_task(self_ping())

    if not (API_ID and API_HASH and SESSIONS):
        log.error("API_ID, API_HASH, STRING_SESSIONS set karo. Web server chalu rahega.")
        await asyncio.Event().wait()

    apps = [a for a in await asyncio.gather(*(start_account(i + 1, s) for i, s in enumerate(SESSIONS))) if a]
    if not apps:
        log.error("Koi session start nahi hua.")
        await asyncio.Event().wait()

    await asyncio.Event().wait()  # forever


if __name__ == "__main__":
    asyncio.run(main())
