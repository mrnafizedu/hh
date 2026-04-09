#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DLX HITTER - Telegram Bot
Commands:
  /hit <url> <cc1> [cc2 ... max 10]   - Hit checkout URL with cards (cc format: number|mm|yy|cvv)
  /addproxy <proxy>                    - Add a proxy (admin only)
  /removeproxy <proxy>                 - Remove a proxy (admin only)
  /listproxy                           - List all proxies (admin only)
  /adduser <chat_id>                   - Approve a user (admin only)
  /removeuser <chat_id>                - Remove a user (admin only)
  /addadmin <chat_id>                  - Add an admin (owner only)
  /setlogger <chat_id>                 - Set logger group/chat (admin only)
  /stats                               - Show hit statistics (approved users)
  /help                                - Show help

Configuration:
  Set BOT_TOKEN and OWNER_ID in environment variables or config.json.
  OWNER_ID is the Telegram user ID of the bot owner (always has full access).
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Local engine imports ────────────────────────────────────────────────────
from dx import (
    URLAnalyzer,
    CardGenerator,
    CardPatternLearner,
    HitterEngine,
    SmartRateLimiter,
    init_db,
    detect_provider,
    DATABASE,
    MAX_ATTEMPTS,
)

# ============= LOGGING =============
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("dlx_bot")

# ============= CONFIG =============
CONFIG_FILE = "bot_config.json"

def _default_config() -> Dict:
    return {
        "bot_token": os.environ.get("BOT_TOKEN", ""),
        "owner_id": int(os.environ.get("OWNER_ID", "0")),
        "approved_users": [],
        "admins": [],
        "proxies": [],
        "logger_chat_id": None,
    }

def load_config() -> Dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        default = _default_config()
        for k, v in default.items():
            cfg.setdefault(k, v)
        return cfg
    cfg = _default_config()
    save_config(cfg)
    return cfg

def save_config(cfg: Dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

config = load_config()

# ============= PERMISSION HELPERS =============
def is_owner(user_id: int) -> bool:
    return user_id == config["owner_id"]

def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or user_id in config["admins"]

def is_approved(user_id: int) -> bool:
    return is_admin(user_id) or user_id in config["approved_users"]

# ============= ACTIVE JOBS TRACKER =============
# Maps chat_id -> asyncio.Task so we can track running hits per chat
active_jobs: Dict[int, asyncio.Task] = {}

# ============= TELEGRAM HELPERS =============
async def send_log(app: Application, text: str):
    """Forward a message to the logger chat if configured."""
    logger_id = config.get("logger_chat_id")
    if logger_id:
        try:
            await app.bot.send_message(chat_id=logger_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Logger send failed: {e}")

async def edit_or_send(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: Optional[int], text: str) -> int:
    """Edit an existing message if msg_id given, else send new. Returns message id."""
    try:
        if msg_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            return msg_id
    except Exception:
        pass
    msg = await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    return msg.message_id

# ============= PROGRESS BAR =============
def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    filled = int(width * current / total) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)

# ============= /start & /help =============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        "🔥 <b>DLX HITTER BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        "Use /help to see all available commands."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = [
        "🛠 <b>DLX HITTER — COMMAND LIST</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📌 <b>User Commands</b>",
        "/hit &lt;url&gt; &lt;cc1&gt; [cc2 ... max 10]",
        "  ↳ Hit a checkout URL with cards",
        "  ↳ Card format: <code>number|mm|yy|cvv</code>",
        "",
        "/stats — Show your hit statistics",
        "/help  — Show this message",
    ]
    if is_admin(user_id):
        lines += [
            "",
            "⚙️ <b>Admin Commands</b>",
            "/adduser &lt;chat_id&gt;    — Approve a user",
            "/removeuser &lt;chat_id&gt; — Remove a user",
            "/addproxy &lt;proxy&gt;     — Add proxy (host:port or user:pass@host:port)",
            "/removeproxy &lt;proxy&gt;  — Remove proxy",
            "/listproxy             — List all proxies",
            "/setlogger &lt;chat_id&gt;  — Set logger group/chat",
        ]
    if is_owner(user_id):
        lines += [
            "",
            "👑 <b>Owner Commands</b>",
            "/addadmin &lt;chat_id&gt;  — Add an admin",
        ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ============= /hit =============
async def cmd_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not is_approved(user_id):
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\nYou are not approved to use this bot.\n"
            "Contact an admin to get access.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/hit &lt;url&gt; &lt;cc1&gt; [cc2 ... max 10]</code>\n\n"
            "<b>Card format:</b> <code>number|mm|yy|cvv</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/hit https://checkout.stripe.com/... 4111111111111111|12|26|123</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    url = args[0]
    card_strings = args[1:11]  # max 10 cards

    # Parse cards
    cards: List[Dict] = []
    bad_cards: List[str] = []
    for cs in card_strings:
        card = CardGenerator.parse_card_string(cs)
        if card:
            cards.append(card)
        else:
            bad_cards.append(cs)

    if not cards:
        await update.message.reply_text(
            "❌ No valid cards found.\n"
            "Card format: <code>number|mm|yy|cvv</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if a job is already running for this chat
    if chat_id in active_jobs and not active_jobs[chat_id].done():
        await update.message.reply_text(
            "⚠️ A hit job is already running in this chat. Please wait for it to finish."
        )
        return

    # Send initial status
    status_msg = await update.message.reply_text(
        f"🔍 <b>Analyzing URL...</b>\n<code>{url[:80]}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Run the hit job as a background task
    task = asyncio.create_task(
        _run_hit_job(update, context, url, cards, bad_cards, status_msg.message_id)
    )
    active_jobs[chat_id] = task

async def _run_hit_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    cards: List[Dict],
    bad_cards: List[str],
    status_msg_id: int,
):
    chat_id = update.effective_chat.id
    user = update.effective_user
    app = context.application

    # ── Step 1: Analyze URL ──────────────────────────────────────────────────
    try:
        url_info = await URLAnalyzer.analyze_url_with_fallback(url, use_deep=False)
        if url_info["merchant"] == "Unknown" or url_info["product"] in [None, "Stripe Checkout", "Checkout"]:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="🔍 <b>Static analysis incomplete — trying deep analysis...</b>",
                parse_mode=ParseMode.HTML,
            )
            url_info = await URLAnalyzer.analyze_url_with_fallback(url, use_deep=True)
    except Exception as e:
        url_info = {
            "url": url, "merchant": "Unknown", "product": "Unknown",
            "amount": None, "currency": "USD", "product_url": None, "success": False
        }
        logger.warning(f"URL analysis failed: {e}")

    # ── Step 2: Show URL info + starting message ─────────────────────────────
    provider = detect_provider(url)
    amount_str = url_info.get("amount") or "N/A"
    currency = url_info.get("currency", "USD")
    merchant = url_info.get("merchant", "Unknown")
    product = url_info.get("product", "Unknown")

    start_text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>DLX HITTER — JOB STARTED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>URL:</b> <code>{url[:70]}...</code>\n"
        f"🏢 <b>Merchant:</b> {merchant}\n"
        f"📦 <b>Product:</b> {product}\n"
        f"💰 <b>Amount:</b> {amount_str} {currency}\n"
        f"🔌 <b>Provider:</b> {provider}\n\n"
        f"💳 <b>Cards:</b> {len(cards)}\n"
    )
    if bad_cards:
        start_text += f"⚠️ <b>Skipped (invalid):</b> {len(bad_cards)}\n"

    # Pick a proxy if any
    proxy = None
    if config["proxies"]:
        proxy = random.choice(config["proxies"])
        start_text += f"🔒 <b>Proxy:</b> <code>{proxy}</code>\n"

    start_text += "\n⏳ <b>Processing cards...</b>"

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg_id,
        text=start_text,
        parse_mode=ParseMode.HTML,
    )

    # ── Step 3: Hit cards ────────────────────────────────────────────────────
    engine = HitterEngine(proxy=proxy)
    rate_limiter = SmartRateLimiter()
    pattern_learner = CardPatternLearner()
    total = min(len(cards), MAX_ATTEMPTS)

    hits_text_lines: List[str] = []
    live_msg_id = status_msg_id

    for i, card in enumerate(cards[:total]):
        card_display = f"{card['card']}|{card['month']}|{card['year']}|{card['cvv']}"

        # Rate limiting delay
        if i > 0:
            last = "declined"
            if engine.results and engine.results[-1].get("success"):
                last = "success"
            delay = rate_limiter.calculate_delay(last)
            await asyncio.sleep(delay)

        # Live progress update
        bar = make_progress_bar(i, total)
        progress_text = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>DLX HITTER — RUNNING</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏢 {merchant} | 💰 {amount_str}\n\n"
            f"<code>[{bar}]</code> {i}/{total}\n"
            f"✅ <b>Hit:</b> {engine.successes}  ❌ <b>Declined:</b> {engine.fails}\n\n"
            f"⏳ <b>Testing:</b> <code>{card_display}</code>"
        )
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=live_msg_id,
                text=progress_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        # Run single hit
        result = await engine.hit(
            url, card, merchant, product, amount_str, i + 1
        )

        pattern_learner.learn(card, merchant, result.get("success", False))

        # Build result line
        if result.get("success"):
            line = (
                f"✅ <b>HIT!</b> <code>{card_display}</code>\n"
                f"   🔗 Receipt: {result.get('receipt_url', 'N/A')}\n"
                f"   ⏱ {result['response_time']:.1f}s"
            )
            hits_text_lines.append(line)

            # Log to logger group
            log_text = (
                f"🟢 <b>SUCCESSFUL HIT</b>\n"
                f"👤 User: {user.mention_html()}\n"
                f"💳 Card: <code>{card_display}</code>\n"
                f"🏢 Merchant: {merchant}\n"
                f"📦 Product: {product}\n"
                f"💰 Amount: {amount_str} {currency}\n"
                f"🔗 Receipt: {result.get('receipt_url', 'N/A')}\n"
                f"⏱ Response: {result['response_time']:.2f}s"
            )
            await send_log(app, log_text)
        else:
            decline_code = result.get("decline_code") or result.get("error") or "unknown"
            line = (
                f"❌ <b>Declined:</b> <code>{card_display}</code>\n"
                f"   📉 {decline_code} | ⏱ {result['response_time']:.1f}s"
            )
            hits_text_lines.append(line)

            # Log decline to logger group
            log_text = (
                f"🔴 <b>DECLINED</b>\n"
                f"👤 User: {user.mention_html()}\n"
                f"💳 Card: <code>{card_display}</code>\n"
                f"🏢 Merchant: {merchant}\n"
                f"📉 Reason: {decline_code}\n"
                f"⏱ Response: {result['response_time']:.2f}s"
            )
            await send_log(app, log_text)

        # Save to DB
        try:
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute(
                """INSERT INTO hits (timestamp, card, merchant, product, amount, success, decline_code, receipt_url, response_time)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now().isoformat(),
                    card_display,
                    merchant,
                    product,
                    amount_str,
                    1 if result.get("success") else 0,
                    result.get("decline_code", ""),
                    result.get("receipt_url", ""),
                    result.get("response_time", 0),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"DB save error: {e}")

    # ── Step 4: Final summary ─────────────────────────────────────────────────
    total_done = engine.successes + engine.fails
    success_rate = (engine.successes / total_done * 100) if total_done > 0 else 0
    bar = make_progress_bar(total, total)

    summary = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏁 <b>DLX HITTER — COMPLETED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏢 <b>Merchant:</b> {merchant}\n"
        f"📦 <b>Product:</b> {product}\n"
        f"💰 <b>Amount:</b> {amount_str} {currency}\n\n"
        f"<code>[{bar}]</code> {total}/{total}\n\n"
        f"✅ <b>Hits:</b> {engine.successes}\n"
        f"❌ <b>Declined:</b> {engine.fails}\n"
        f"📊 <b>Success Rate:</b> {success_rate:.1f}%\n\n"
        "<b>━━━ Results ━━━</b>\n"
    )

    # Append per-card results (trim if too long)
    if hits_text_lines:
        card_block = "\n".join(hits_text_lines)
        if len(summary) + len(card_block) > 3800:
            card_block = "\n".join(hits_text_lines[:10]) + "\n…(truncated)"
        summary += card_block

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=live_msg_id,
            text=summary,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode=ParseMode.HTML)

    # Summary log to logger
    log_summary = (
        f"📋 <b>HIT JOB SUMMARY</b>\n"
        f"👤 {user.mention_html()}\n"
        f"🔗 {url[:70]}\n"
        f"✅ {engine.successes} hits / ❌ {engine.fails} declined\n"
        f"📊 {success_rate:.1f}% success rate"
    )
    await send_log(app, log_summary)

# ============= /stats =============
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_approved(user_id):
        await update.message.reply_text("⛔ Access denied.")
        return

    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM hits")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM hits WHERE success=1")
        hits = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM hits WHERE success=0")
        declined = c.fetchone()[0]
        conn.close()
    except Exception:
        total = hits = declined = 0

    rate = (hits / total * 100) if total > 0 else 0
    text = (
        "📊 <b>DLX HITTER — STATS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔢 <b>Total Attempts:</b> {total}\n"
        f"✅ <b>Successful Hits:</b> {hits}\n"
        f"❌ <b>Declined:</b> {declined}\n"
        f"📈 <b>Success Rate:</b> {rate:.1f}%"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ============= /adduser =============
async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /adduser <chat_id>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    if new_id not in config["approved_users"]:
        config["approved_users"].append(new_id)
        save_config(config)
        await update.message.reply_text(f"✅ User <code>{new_id}</code> approved.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ User <code>{new_id}</code> already approved.", parse_mode=ParseMode.HTML)

# ============= /removeuser =============
async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeuser <chat_id>")
        return
    try:
        rem_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    if rem_id in config["approved_users"]:
        config["approved_users"].remove(rem_id)
        save_config(config)
        await update.message.reply_text(f"✅ User <code>{rem_id}</code> removed.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ User <code>{rem_id}</code> not in approved list.", parse_mode=ParseMode.HTML)

# ============= /addadmin =============
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <chat_id>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    if new_id not in config["admins"]:
        config["admins"].append(new_id)
        save_config(config)
        await update.message.reply_text(f"✅ Admin <code>{new_id}</code> added.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ <code>{new_id}</code> is already an admin.", parse_mode=ParseMode.HTML)

# ============= /addproxy =============
async def cmd_addproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /addproxy <proxy>\n"
            "Formats:\n"
            "  <code>host:port</code>\n"
            "  <code>user:pass@host:port</code>\n"
            "  <code>http://host:port</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    proxy = context.args[0].strip()
    if proxy not in config["proxies"]:
        config["proxies"].append(proxy)
        save_config(config)
        await update.message.reply_text(f"✅ Proxy added: <code>{proxy}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Proxy already exists: <code>{proxy}</code>", parse_mode=ParseMode.HTML)

# ============= /removeproxy =============
async def cmd_removeproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeproxy <proxy>")
        return
    proxy = context.args[0].strip()
    if proxy in config["proxies"]:
        config["proxies"].remove(proxy)
        save_config(config)
        await update.message.reply_text(f"✅ Proxy removed: <code>{proxy}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Proxy not found: <code>{proxy}</code>", parse_mode=ParseMode.HTML)

# ============= /listproxy =============
async def cmd_listproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    proxies = config["proxies"]
    if not proxies:
        await update.message.reply_text("ℹ️ No proxies configured.")
        return
    lines = ["🔒 <b>Proxy List</b>", "━━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(proxies, 1):
        lines.append(f"{i}. <code>{p}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# ============= /setlogger =============
async def cmd_setlogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        # Use current chat as logger
        config["logger_chat_id"] = update.effective_chat.id
        save_config(config)
        await update.message.reply_text(
            f"✅ Logger set to this chat: <code>{update.effective_chat.id}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        logger_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    config["logger_chat_id"] = logger_id
    save_config(config)
    await update.message.reply_text(
        f"✅ Logger chat set to: <code>{logger_id}</code>", parse_mode=ParseMode.HTML
    )

# ============= UNKNOWN COMMAND =============
async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Unknown command. Use /help to see available commands."
    )

# ============= MAIN =============
def main():
    token = config.get("bot_token") or os.environ.get("BOT_TOKEN", "")
    if not token:
        print("ERROR: BOT_TOKEN is not set. Add it to bot_config.json or as an env variable.")
        return

    if config.get("owner_id", 0) == 0:
        print("WARNING: OWNER_ID is not set. Set it in bot_config.json or as an env variable.")

    # Ensure DB exists
    init_db()

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("hit", cmd_hit))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("adduser", cmd_adduser))
    application.add_handler(CommandHandler("removeuser", cmd_removeuser))
    application.add_handler(CommandHandler("addadmin", cmd_addadmin))
    application.add_handler(CommandHandler("addproxy", cmd_addproxy))
    application.add_handler(CommandHandler("removeproxy", cmd_removeproxy))
    application.add_handler(CommandHandler("listproxy", cmd_listproxy))
    application.add_handler(CommandHandler("setlogger", cmd_setlogger))
    application.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    print("🤖 DLX Hitter Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
