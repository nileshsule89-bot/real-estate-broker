import hashlib
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.models import Property
from app.services.inventory_service import record_property_verification, upsert_inventory_property


SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_SITES = ["99acres.com", "magicbricks.com", "housing.com", "nobroker.in"]


class _DuckDuckGoParser(HTMLParser):
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
            href = attrs_map.get("href", "") or ""
            self._current = {"href": href}
            self._capture_title = True
            self._title_parts = []
        elif self._current and tag in {"a", "div"} and "result__snippet" in classes:
            self._capture_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = _normalize_text("".join(self._title_parts))
            self._capture_title = False
        elif self._capture_snippet and tag in {"a", "div"} and self._current is not None:
            self._current["snippet"] = _normalize_text("".join(self._snippet_parts))
            href = self._current.get("href", "")
            if href and self._current.get("title"):
                self.results.append(self._current)
            self._current = None
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)
        elif self._capture_snippet:
            self._snippet_parts.append(data)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _decode_ddg_link(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return href


def _parse_price(text: str, fallback: float) -> float:
    lowered = text.lower()
    crore_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*crore", lowered)
    if crore_match:
        return float(crore_match.group(1)) * 10000000
    lakh_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*lakh", lowered)
    if lakh_match:
        return float(lakh_match.group(1)) * 100000
    rupee_match = re.search(r"(?:rs\.?|inr)?\s*([0-9][0-9,]{3,})", lowered)
    if rupee_match:
        return float(rupee_match.group(1).replace(",", ""))
    return fallback


def _parse_bhk(text: str, fallback: int) -> int:
    bhk_match = re.search(r"(\d+)\s*bhk", text.lower())
    if bhk_match:
        return int(bhk_match.group(1))
    return fallback


def _extract_image_url(url: str) -> str | None:
    try:
        with httpx.Client(
            timeout=2.5,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception:
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1))
    return None


def fetch_property_photo(url: str) -> str | None:
    return _extract_image_url(url)


def search_real_properties(preferences: dict, limit: int = 5) -> list[dict]:
    query_parts = [
        " OR ".join(f"site:{site}" for site in SEARCH_SITES),
        f"{preferences.get('bhk', '')} BHK",
        preferences.get("type", ""),
        "property",
        "in",
        preferences.get("location", ""),
    ]
    budget = preferences.get("budget")
    if budget:
        query_parts.append(f"under {int(float(budget))}")
    query = " ".join(part for part in query_parts if part)

    with httpx.Client(
        timeout=8,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        response = client.get(SEARCH_URL, params={"q": query})
        response.raise_for_status()
        parser = _DuckDuckGoParser()
        parser.feed(response.text)

    listings: list[dict] = []
    for item in parser.results:
        if len(listings) >= limit:
            break
        link = _decode_ddg_link(item["href"])
        title = item.get("title") or "Property listing"
        snippet = item.get("snippet") or ""
        listings.append(
            {
                "listing_id": hashlib.md5(link.encode("utf-8")).hexdigest()[:8],
                "title": title,
                "location": preferences.get("location", "unknown"),
                "price": _parse_price(f"{title} {snippet}", float(preferences.get("budget", 0) or 0)),
                "bhk": _parse_bhk(f"{title} {snippet}", int(preferences.get("bhk", 1) or 1)),
                "type": preferences.get("type", "rent"),
                "source": urlparse(link).netloc,
                "url": link,
                "snippet": snippet,
                "image_url": None,
            }
        )
    return listings


def persist_web_properties(db: Session, listings: list[dict]) -> list[Property]:
    rows: list[Property] = []
    for item in listings:
        row = upsert_inventory_property(
            db,
            title=item["title"],
            location=item["location"],
            price=item["price"],
            bhk=item["bhk"],
            listing_type=item["type"],
            source_name=item["source"],
            source_type="aggregator_feed",
            owner_contact=item["url"],
            listing_url=item["url"],
            metadata={
                "url": item["url"],
                "image_url": item["image_url"],
                "snippet": item["snippet"],
            },
            external_listing_id=item.get("listing_id", ""),
        )
        rows.append(row)
        if item.get("image_url") or item.get("snippet"):
            record_property_verification(
                db,
                row.property_id,
                status="verified",
                notes="Auto-verified from live listing metadata",
                verified_by="web_ingestion",
            )
    return rows
