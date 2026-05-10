#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "real_estate.db"
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def storage_base_url(env: dict[str, str]) -> str:
    if env.get("SUPERBASE_STORAGE_URL"):
        return env["SUPERBASE_STORAGE_URL"].rstrip("/").replace("/object/public", "/object")
    if env.get("SUPERBASE_URL"):
        return env["SUPERBASE_URL"].rstrip("/") + "/storage/v1/object"
    return ""


def public_url(env: dict[str, str], object_path: str) -> str:
    base = env.get("SUPERBASE_STORAGE_URL", "").rstrip("/") or (env["SUPERBASE_URL"].rstrip("/") + "/storage/v1/object/public" if env.get("SUPERBASE_URL") else "")
    return f"{base}/{env['SUPERBASE_BUCKET']}/{object_path}"


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "ignore")


def extract_image_url(listing_url: str) -> str | None:
    try:
        html = fetch_html(listing_url)
    except Exception:
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def sanitize_filename(title: str, source_image_url: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "property-photo"
    ext = os.path.splitext(urlparse(source_image_url).path)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    return cleaned + ext


def upload_to_storage(env: dict[str, str], object_path: str, payload: bytes, mime_type: str) -> None:
    base_url = storage_base_url(env)
    upload_url = f"{base_url}/{env['SUPERBASE_BUCKET']}/{object_path}"
    request = urllib.request.Request(
        upload_url,
        data=payload,
        method="POST",
        headers={
            "apikey": env["SUPERBASE_API_KEY"],
            "Authorization": f"Bearer {env['SUPERBASE_API_KEY']}",
            "Content-Type": mime_type,
            "x-upsert": "true",
        },
    )
    ssl_context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=30, context=ssl_context) as response:
        response.read()


def fetch_image_bytes(image_url: str, max_retries: int = 3) -> tuple[bytes, str]:
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    for attempt in range(max_retries):
        try:
            request = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30, context=ssl_context) as response:
                mime_type = response.headers.get_content_type() or "image/jpeg"
                return response.read(), mime_type
        except (urllib.error.HTTPError, urllib.error.URLError, ssl.SSLError) as e:
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))
                continue
            raise
        except Exception as e:
            raise


def ensure_photo_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(properties)")
    columns = {row[1] for row in cur.fetchall()}
    if "listing_url" not in columns:
        cur.execute("ALTER TABLE properties ADD COLUMN listing_url VARCHAR(500) NOT NULL DEFAULT ''")
    if "photo_storage_folder" not in columns:
        cur.execute("ALTER TABLE properties ADD COLUMN photo_storage_folder VARCHAR(300) NOT NULL DEFAULT ''")
    cur.execute(
        """
        UPDATE properties
        SET listing_url = CASE
            WHEN listing_url IS NULL OR listing_url = '' THEN owner_contact
            ELSE listing_url
        END
        """
    )
    if "photo_storage_folder" not in columns:
        pass
    conn.commit()


def main() -> None:
    env = load_env(ENV_PATH)
    required = ["SUPERBASE_API_KEY", "SUPERBASE_BUCKET"]
    missing = [key for key in required if not env.get(key)] + (["SUPERBASE_URL or SUPERBASE_STORAGE_URL"] if not storage_base_url(env) else [])
    if missing:
        print(json.dumps({"error": "missing_storage_config", "missing": missing}, indent=2))
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    ensure_photo_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT property_id, title, listing_url, photo_storage_folder, metadata FROM properties")
    rows = cur.fetchall()

    uploaded = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    for property_id, title, listing_url, photo_storage_folder, metadata_text in rows:
        metadata = json.loads(metadata_text) if metadata_text else {}
        image_url = metadata.get("image_url") or extract_image_url(listing_url or metadata.get("url", ""))
        if not image_url:
            skipped += 1
            continue

        folder = photo_storage_folder or f"properties/{property_id}"
        cur.execute(
            "UPDATE properties SET photo_storage_folder = ? WHERE property_id = ?",
            (folder, property_id),
        )
        object_path = f"{folder}/{sanitize_filename(title, image_url)}"
        try:
            payload, mime_type = fetch_image_bytes(image_url)
            upload_to_storage(env, object_path, payload, mime_type)
            cloud_paths = list(metadata.get("cloud_photo_paths") or [])
            if object_path not in cloud_paths:
                cloud_paths.append(object_path)
            cloud_urls = list(metadata.get("cloud_photo_urls") or [])
            object_public_url = public_url(env, object_path)
            if object_public_url not in cloud_urls:
                cloud_urls.append(object_public_url)
            metadata["image_url"] = image_url
            metadata["cloud_photo_paths"] = cloud_paths
            metadata["cloud_photo_urls"] = cloud_urls
            metadata["primary_cloud_photo_path"] = object_path
            metadata["primary_cloud_photo_url"] = object_public_url
            cur.execute(
                "UPDATE properties SET photo_storage_folder = ?, metadata = ? WHERE property_id = ?",
                (folder, json.dumps(metadata, ensure_ascii=True), property_id),
            )
            conn.commit()
            uploaded += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            failures.append({"property_id": property_id, "reason": str(exc)})

    conn.close()
    print(
        json.dumps(
            {
                "uploaded": uploaded,
                "skipped": skipped,
                "failed": len(failures),
                "failures": failures[:10],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
