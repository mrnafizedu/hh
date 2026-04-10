#!/usr/bin/env python3
"""
AriesxHit API Probe — bot token tracker
Run: python3 probe_token.py
"""

import requests
import json
import re

API = "https://aries.mikeyyfrr.me"
TOKEN_RE = re.compile(r'\b(\d{8,12}:[A-Za-z0-9_-]{35,})\b')

# Extension-এর মতো same headers পাঠাচ্ছি
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "chrome-extension://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def req(method, path, body=None):
    url = API + path
    print(f"\n{'='*55}")
    print(f"  {method} {url}")
    print(f"{'='*55}")
    if body:
        print(f"  PAYLOAD: {json.dumps(body, indent=2)}")
    try:
        if method == "GET":
            r = requests.get(url, headers=HEADERS, timeout=12)
        else:
            r = requests.post(url, headers=HEADERS, json=body, timeout=12)

        print(f"  STATUS : {r.status_code}")
        try:
            data = r.json()
            print(f"  RESPONSE:\n{json.dumps(data, indent=4)}")
        except Exception:
            data = {}
            print(f"  RESPONSE (raw): {r.text[:800]}")

        # bot token pattern খুঁজি
        hits = TOKEN_RE.findall(r.text)
        if hits:
            print(f"\n  🔑🔑  BOT TOKEN FOUND: {hits}")
        return r.status_code, r.text
    except Exception as e:
        print(f"  ERROR: {e}")
        return 0, ""

print("""
╔══════════════════════════════════════════════════╗
║         AriesxHit API Token Probe                ║
╚══════════════════════════════════════════════════╝
""")

# ── Step 1: checkout-info (extension Tab open করলে এটা call হয়) ────────────
print("\n[STEP 1] checkout-info — tab open হলে এই call হয়")
req("POST", "/api/tg/checkout-info", {
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_test123"
})

# ── Step 2: /api/tg/hit — checkout detect হলে call হয় ──────────────────────
print("\n[STEP 2] /api/tg/hit — checkout analyze")
req("POST", "/api/tg/hit", {
    "tg_id":        "123456789",
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_test123",
    "chat_id":      "123456789"
})

# ── Step 3: notify-hit — hit হলে এই payload পাঠায় extension ─────────────────
print("\n[STEP 3] /api/tg/notify-hit — hit notification (bot_token খালি পাঠাচ্ছি)")
req("POST", "/api/tg/notify-hit", {
    "tg_id":        "123456789",
    "name":         "TestUser",
    "card":         "4242424242424242|12|26|123",
    "amount":       "1.00",
    "email":        "test@example.com",
    "attempts":     1,
    "time_sec":     10,
    "hit_mode":     "cc_list",
    "current_url":  "https://checkout.stripe.com/c/pay/cs_live_test123",
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_test123",
    "business_url": "",
    "business_name":"",
    "merchant_url": "",
    "bot_token":    "",   # ← খালি — server কি নিজে bot token জানে?
    "chat_id":      ""
})

# ── Step 4: extension-status ────────────────────────────────────────────────
print("\n[STEP 4] /api/extension-status")
req("GET", "/api/extension-status")

# ── Step 5: login / register — এখানেই bot token assign হতে পারে ────────────
print("\n[STEP 5] login endpoints — যেখান থেকে token আসে")
for ep in ["/api/login", "/api/register", "/api/tg/login",
           "/api/tg/register", "/api/tg/link", "/api/tg/connect",
           "/api/user/token", "/api/tg/bot"]:
    req("POST", ep, {"tg_id": "123456789"})

# ── Step 6: settings fetch — login-এর পর settings pull করা হয় ──────────────
print("\n[STEP 6] settings / config endpoints")
for ep in ["/api/settings", "/api/config", "/api/tg/settings",
           "/api/tg/config", "/api/user/settings",
           "/api/user/config", "/api/bot-config"]:
    req("GET", ep)

# ── Step 7: validate-license — কিছু টুলে license validate-এ bot token আসে ──
print("\n[STEP 7] /api/validate-license")
req("POST", "/api/validate-license", {"key": "TEST-LICENSE-KEY"})

print("""
╔══════════════════════════════════════════════════╗
║  RESULT SUMMARY                                  ║
║                                                  ║
║  Bot token NOT in JS source.                     ║
║  It lives in chrome.storage → ax_settings        ║
║  key: telegramBotToken                           ║
║                                                  ║
║  To read it manually:                            ║
║  1. Chrome → Extensions → AriesxHit             ║
║  2. Background service worker → Inspect          ║
║  3. Console:                                     ║
║     chrome.storage.local.get('ax_settings',      ║
║       d => console.log(d))                       ║
╚══════════════════════════════════════════════════╝
""")
