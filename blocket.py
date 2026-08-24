"""Blocket search fetcher and parser.

Blocket has no public API for third-party search, so we fetch the regular
server-rendered search result page and parse the listing cards out of the HTML.
The page is rendered with a `<article class="... sf-search-ad ...">` card per
ad containing the ad id, title, price, location, image and a relative
"published" time ("25 min", "1 dag", ...).

If Blocket changes the markup and we stop finding cards, we raise ParseError so
the background job logs a loud, actionable error instead of silently doing
nothing.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import base64
import json
import re
import urllib.parse
import urllib.request

BASE_URL = "https://www.blocket.se"
SEARCH_PATH = "/recommerce/forsale/search"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Blocketvakten/1.0"
)

_ITEM_RE = re.compile(r"/item/(\d+)")
_PRICE_RE = re.compile(r"<span>([\d\s\u00a0\u202f]+)\s*kr\s*</span>", re.I)
_TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_IMG_RE = re.compile(r'<img[^>]*\ssrc="(https://images\.blocketcdn\.se/[^"]+)"', re.I)
_LOC_RE = re.compile(
    r'<span class="whitespace-nowrap truncate mr-8">([^<]+)</span>'
    r'\s*<span class="whitespace-nowrap">([^<]+)</span>',
    re.S,
)


class ScrapeError(Exception):
    """Base error for anything that goes wrong while fetching/parsing."""


class FetchError(ScrapeError):
    """The HTTP request to Blocket failed."""


class ParseError(ScrapeError):
    """The page did not contain the expected listing markup."""


def build_search_url(keyword: str, max_price: int | None = None) -> str:
    """Build a Blocket search URL for a single keyword variant."""
    params = {"q": keyword}
    if max_price is not None:
        params["price_to"] = str(max_price)
    return BASE_URL + SEARCH_PATH + "?" + urllib.parse.urlencode(params)


def fetch_html(url: str, timeout: float = 20.0) -> str:
    """Fetch a Blocket page and return its decoded HTML."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Network error for {url}: {exc.reason}") from exc

    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _clean(text: str) -> str:
    """Unescape entities and collapse whitespace."""
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _parse_dehydrated_state(page_html: str):
    """Extract listings from base64-encoded React Query dehydrated state.

    Looks for the largest <script> tag containing a base64-encoded JSON blob.
    Blocket no longer uses type="application/json" on this tag.
    """
    best_content = ""
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', page_html, re.S):
        content = m.group(1).strip()
        if len(content) > len(best_content):
            best_content = content
    if not best_content or len(best_content) < 5000:
        return None

    for content in (best_content,):  # single-element loop for clean flow
        if len(content) < 5000:
            continue
        decoded = None
        for pad in ("", "=", "=="):
            try:
                decoded = base64.b64decode(content + pad).decode("utf-8", "ignore")
                break
            except Exception:
                continue
        if decoded is None:
            continue
        try:
            data = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        queries = data.get("queries") or []
        for q in queries:
            qk = q.get("queryKey", [])
            if not any(isinstance(it, dict) and it.get("scope") == "search" for it in qk):
                continue
            docs = q.get("state", {}).get("data", {}).get("docs") or []
            listings = []
            for doc in docs:
                ad_id = str(doc.get("id") or "")
                if not ad_id:
                    continue
                pr = doc.get("price") or {}
                price = pr.get("amount")
                img_data = doc.get("image") or {}
                img_url = img_data.get("url") or ""
                if not img_url and doc.get("image_urls"):
                    img_url = doc["image_urls"][0]
                listings.append({
                    "ad_id": ad_id,
                    "title": (doc.get("heading") or "").strip(),
                    "price": int(price) if price else None,
                    "location": (doc.get("location") or "").strip(),
                    "image_url": img_url,
                    "url": doc.get("canonical_url") or "https://www.blocket.se/recommerce/forsale/item/" + ad_id,
                    "published_text": str(doc.get("timestamp") or ""),
                })
            if listings:
                return listings
    return None


def _parse_article_cards(page_html: str):
    """Fallback: extract listings from <article class="sf-search-ad"> cards."""
    cards = page_html.split("<article")
    listings = []
    for block in cards[1:]:
        if "sf-search-ad" not in block:
            continue
        block = block.split("</article>", 1)[0]
        item_match = _ITEM_RE.search(block)
        if not item_match:
            continue
        ad_id = item_match.group(1)
        title_match = _TITLE_RE.search(block)
        title = _clean(title_match.group(1)) if title_match else ""
        price = None
        price_match = _PRICE_RE.search(block)
        if price_match:
            digits = re.sub(r"[^\d]", "", price_match.group(1))
            if digits:
                price = int(digits)
        location = ""
        published_text = ""
        loc_match = _LOC_RE.search(block)
        if loc_match:
            location = _clean(loc_match.group(1))
            published_text = _clean(loc_match.group(2))
        image_url = ""
        img_match = _IMG_RE.search(block)
        if img_match:
            image_url = img_match.group(1)
        if not title and price is None and not image_url:
            continue
        listings.append({
            "ad_id": ad_id, "title": title, "price": price,
            "location": location, "image_url": image_url,
            "url": "https://www.blocket.se/recommerce/forsale/item/" + ad_id,
            "published_text": published_text,
        })
    return listings


def parse_listings(page_html: str):
    """Parse listing cards from a Blocket search result page.

    Tries the dehydrated React Query state first (structured JSON), then
    falls back to scraping <article class="sf-search-ad"> cards.

    Returns a list of dicts with keys: ad_id, title, price, location,
    image_url, url, published_text. Raises ParseError when nothing is found.
    """
    listings = _parse_dehydrated_state(page_html)
    if listings:
        return listings
    listings = _parse_article_cards(page_html)
    if listings:
        return listings
    raise ParseError(
        "Kunde inte hitta nagra annonser pa Blocket-sidan - HTML-strukturen har troligen andrats."
    )

def parse_published(published_text: str, now: _dt.datetime | None = None) -> _dt.datetime:
    """Approximate an absolute datetime from Blocket's relative time label."""
    now = now or _dt.datetime.now()
    text = (published_text or "").strip().lower() or ""

    if re.match(r"^\d{10,13}$", text):
        try:
            ts = int(text)
            if ts > 1e12:
                ts /= 1000
            return _dt.datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            pass

    def _subtract(**kwargs) -> _dt.datetime:
        try:
            return now - _dt.timedelta(**kwargs)
        except OverflowError:
            return now

    match = re.search(r"(\d+)\s*min", text)
    if match:
        return _subtract(minutes=int(match.group(1)))
    match = re.search(r"(\d+)\s*tim", text)
    if match:
        return _subtract(hours=int(match.group(1)))
    match = re.search(r"(\d+)\s*dag", text)
    if match:
        return _subtract(days=int(match.group(1)))
    match = re.search(r"(\d+)\s*veck", text)
    if match:
        return _subtract(weeks=int(match.group(1)))
    match = re.search(r"(\d+)\s*mån", text)
    if match:
        return _subtract(days=30 * int(match.group(1)))

    if "idag" in text or "i dag" in text:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "igår" in text or "i går" in text or "ig\u00e5r" in text:
        return (now - _dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # A bare date such as "18 aug" / "18 aug." / "2025-08-18".
    match = re.search(r"(\d{1,2})\s+([a-zåäö]{3})", text)
    if match:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
        }
        month = months.get(match.group(2)[:3])
        if month:
            year = now.year
            day = int(match.group(1))
            candidate = _dt.datetime(year, month, day, 0, 0, 0)
            if candidate > now:
                candidate = candidate.replace(year=year - 1)
            return candidate

    return now


def fetch_search_listings(
    keyword: str, max_price: int | None = None, timeout: float = 20.0
) -> list[dict]:
    """Fetch and parse the first page of results for a single keyword."""
    url = build_search_url(keyword, max_price=max_price)
    html = fetch_html(url, timeout=timeout)
    return parse_listings(html)
