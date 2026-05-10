import hashlib
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.models import Property
from app.services.inventory_service import refresh_inventory_batch
from app.services.web_property_service import fetch_property_photo


SEARCH_URL = "https://html.duckduckgo.com/html/"
SOURCE_DOMAINS = {
    "magicbricks": "www.magicbricks.com",
    "housing": "housing.com",
    "nobroker": "www.nobroker.in",
}


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._current: dict | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        classes = attrs_map.get("class", "") or ""
        if tag == "a" and "result__a" in classes:
            self._current = {"href": attrs_map.get("href", "") or ""}
            self._capture_title = True
            self._title_parts = []
        elif self._current and tag in {"a", "div"} and "result__snippet" in classes:
            self._capture_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = normalize_text("".join(self._title_parts))
            self._capture_title = False
        elif self._capture_snippet and tag in {"a", "div"} and self._current is not None:
            self._current["snippet"] = normalize_text("".join(self._snippet_parts))
            if self._current.get("href") and self._current.get("title"):
                self.results.append(self._current)
            self._current = None
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)
        elif self._capture_snippet:
            self._snippet_parts.append(data)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def decode_ddg_link(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return href


def parse_price(text: str, fallback: float = 0) -> float:
    lowered = text.lower()
    crore_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*crore", lowered)
    if crore_match:
        return float(crore_match.group(1)) * 10000000
    lakh_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*lakh", lowered)
    if lakh_match:
        return float(lakh_match.group(1)) * 100000
    rupee_match = re.search(r"(?:rs\.?|inr|₹)?\s*([0-9][0-9,]{3,})", lowered)
    if rupee_match:
        return float(rupee_match.group(1).replace(",", ""))
    return fallback


def parse_bhk(text: str, fallback: int = 1) -> int:
    bhk_match = re.search(r"(\d+)\s*bhk", text.lower())
    if bhk_match:
        return int(bhk_match.group(1))
    return fallback


def infer_listing_type(text: str, fallback: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ["sale", "buy", "resale"]):
        return "buy"
    if any(token in lowered for token in ["rent", "lease"]):
        return "rent"
    return fallback


def fetch_html(url: str) -> str:
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def search_site_results(location: str, *, source_key: str, listing_type: str, limit: int = 10) -> list[dict]:
    domain = SOURCE_DOMAINS[source_key]
    query = f"site:{domain} {location} property {listing_type}"
    html = fetch_html(f"{SEARCH_URL}?q={quote_plus(query)}")
    parser = DuckDuckGoParser()
    parser.feed(html)

    rows: list[dict] = []
    for item in parser.results[:limit]:
        link = decode_ddg_link(item["href"])
        title = item.get("title") or "Property listing"
        snippet = item.get("snippet") or ""
        rows.append(
            {
                "listing_id": hashlib.md5(link.encode("utf-8")).hexdigest()[:12],
                "title": title,
                "location": location,
                "price": parse_price(f"{title} {snippet}", 0),
                "bhk": parse_bhk(f"{title} {snippet}", 1),
                "type": infer_listing_type(f"{title} {snippet}", listing_type),
                "source": domain,
                "source_type": "search_fallback",
                "url": link,
                "owner_contact": link,
                "verification_status": "verified" if snippet else "unverified",
                "metadata": {
                    "snippet": snippet,
                    "url": link,
                    "search_query": query,
                    "image_url": fetch_property_photo(link) if source_key != "nobroker" else None,
                },
            }
        )
    return rows


def _extract_card_value(block: str, label: str) -> str:
    match = re.search(
        rf'{re.escape(label)}</div><div class="mb-srp__card__summary--value">(.*?)</div>',
        block,
        re.IGNORECASE | re.DOTALL,
    )
    return normalize_text(re.sub(r"<.*?>", " ", match.group(1))) if match else ""


def scrape_magicbricks_location(location: str) -> list[dict]:
    if location.lower().strip() != "airoli":
        return []

    url = "https://www.magicbricks.com/property-for-rent-in-airoli-navi-mumbai-pppfr"
    html = fetch_html(url)
    cards = re.findall(
        r'<div class="mb-srp__card__container.*?<h2 class="mb-srp__card--title" title="(.*?)">(.*?)</h2>(.*?)</div></div></div></div>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    rows: list[dict] = []
    for title_attr, _, block in cards[:12]:
        title = normalize_text(title_attr)
        if "airoli" not in title.lower():
            continue
        society_match = re.search(r'mb-srp__card__society--name"[^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        society = normalize_text(re.sub(r"<.*?>", " ", society_match.group(1))) if society_match else ""
        location_match = re.search(r'mb-srp__card--title">\((.*?)\)</span>', block, re.IGNORECASE | re.DOTALL)
        sublocation = normalize_text(re.sub(r"<.*?>", " ", location_match.group(1))) if location_match else location
        price_match = re.search(r'mb-srp__card__price--amount[^>]*>\s*₹?\s*([0-9,]+)', block, re.IGNORECASE)
        area_sqft = parse_price(_extract_card_value(block, "Super Area") or _extract_card_value(block, "Carpet Area"), 0)
        updated_match = re.search(r'mb-srp__card__ads--post[^>]*>(.*?)</div>', block, re.IGNORECASE | re.DOTALL)
        updated_text = normalize_text(re.sub(r"<.*?>", " ", updated_match.group(1))) if updated_match else ""
        listing_url_match = re.search(r'href="(https://www\.magicbricks\.com/[^"]+)"', block)
        listing_url = listing_url_match.group(1) if listing_url_match else url
        rows.append(
            {
                "listing_id": hashlib.md5(f"{title}|{listing_url}".encode("utf-8")).hexdigest()[:12],
                "title": f"{title} - {society}" if society and society.lower() not in title.lower() else title,
                "location": sublocation or location,
                "price": float(price_match.group(1).replace(",", "")) if price_match else 0,
                "bhk": parse_bhk(title, 1),
                "type": "rent",
                "source": SOURCE_DOMAINS["magicbricks"],
                "source_type": "site_scrape",
                "url": listing_url,
                "owner_contact": listing_url,
                "verification_status": "verified" if updated_text else "unverified",
                "metadata": {
                    "society": society,
                    "updated_text": updated_text,
                    "area_sqft": area_sqft or None,
                    "source_url": url,
                },
            }
        )
    return rows


def gather_inventory(location: str) -> list[dict]:
    listings: list[dict] = []
    listings.extend(scrape_magicbricks_location(location))
    listings.extend(search_site_results(location, source_key="magicbricks", listing_type="rent", limit=8))
    listings.extend(search_site_results(location, source_key="housing", listing_type="buy", limit=8))
    listings.extend(search_site_results(location, source_key="nobroker", listing_type="rent", limit=8))
    return listings


def refresh_location_inventory(db: Session, location: str, *, mark_missing: bool = True) -> list[Property]:
    listings = gather_inventory(location)
    return refresh_inventory_batch(db, listings, location=location, mark_missing=mark_missing)
