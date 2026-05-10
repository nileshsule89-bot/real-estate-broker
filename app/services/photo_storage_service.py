import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Property


def is_storage_configured() -> bool:
    return bool(settings.superbase_api_key and settings.superbase_bucket and _storage_base_url())


def _storage_base_url() -> str:
    if settings.superbase_storage_url:
        return settings.superbase_storage_url.rstrip("/")
    if settings.superbase_url:
        return f"{settings.superbase_url.rstrip('/')}/storage/v1"
    return ""


def public_url_for_object(object_path: str) -> str:
    storage_base = _storage_base_url()
    if not storage_base or not settings.superbase_bucket:
        return object_path
    return f"{storage_base}/object/public/{settings.superbase_bucket}/{object_path}"


def property_photo_folder(property_row: Property) -> str:
    return f"properties/{property_row.property_id}"


def _safe_extension_from_url(url: str, mime_type: str | None) -> str:
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path)
    ext = ext.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return ext
    guessed = mimetypes.guess_extension(mime_type or "") or ".jpg"
    return ".jpg" if guessed == ".jpe" else guessed


def _sanitize_filename(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:60] or "property-photo"


def _upload_bytes(object_path: str, payload: bytes, mime_type: str) -> str:
    storage_base = _storage_base_url()
    headers = {
        "apikey": settings.superbase_api_key,
        "Authorization": f"Bearer {settings.superbase_api_key}",
        "Content-Type": mime_type,
        "x-upsert": "true",
    }
    upload_url = f"{storage_base}/object/{settings.superbase_bucket}/{object_path}"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.post(upload_url, headers=headers, content=payload)
        response.raise_for_status()
    return object_path


def _persist_uploaded_photo(db: Session, property_row: Property, object_path: str, source_image_url: str | None = None) -> str:
    public_url = public_url_for_object(object_path)
    metadata = dict(property_row.metadata_json or {})
    cloud_paths = list(metadata.get("cloud_photo_paths") or [])
    cloud_urls = list(metadata.get("cloud_photo_urls") or [])
    if object_path not in cloud_paths:
        cloud_paths.append(object_path)
    if public_url not in cloud_urls:
        cloud_urls.append(public_url)
    metadata["cloud_photo_paths"] = cloud_paths
    metadata["cloud_photo_urls"] = cloud_urls
    metadata["primary_cloud_photo_path"] = object_path
    metadata["primary_cloud_photo_url"] = public_url
    if source_image_url and not metadata.get("image_url"):
        metadata["image_url"] = source_image_url
    property_row.metadata_json = metadata
    property_row.photo_storage_folder = property_photo_folder(property_row)
    db.commit()
    db.refresh(property_row)
    return public_url


def upload_property_photo_from_url(db: Session, property_row: Property, source_image_url: str) -> str | None:
    if not is_storage_configured() or not source_image_url:
        return None

    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        response = client.get(source_image_url)
        response.raise_for_status()
        payload = response.content
        mime_type = response.headers.get("content-type", "image/jpeg").split(";")[0]

    folder = property_photo_folder(property_row)
    ext = _safe_extension_from_url(source_image_url, mime_type)
    object_path = f"{folder}/{_sanitize_filename(property_row.title)}{ext}"
    _upload_bytes(object_path, payload, mime_type)
    return _persist_uploaded_photo(db, property_row, object_path, source_image_url)


def upload_property_photo_from_file(db: Session, property_row: Property, file_path: str) -> str | None:
    if not is_storage_configured() or not file_path:
        return None
    path = Path(file_path)
    if not path.exists():
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    payload = path.read_bytes()
    ext = path.suffix.lower() if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    folder = property_photo_folder(property_row)
    object_path = f"{folder}/{_sanitize_filename(path.stem or property_row.title)}{ext}"
    _upload_bytes(object_path, payload, mime_type)
    return _persist_uploaded_photo(db, property_row, object_path)
