"""Image uploads: validated with Pillow, stored on local disk (dev) or Cloudinary (production).

Free hosting tiers usually have ephemeral disks, so production should use Cloudinary.
"""

import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import get_settings
from app.services.errors import RuleViolation

logger = logging.getLogger("turfslot.storage")

_ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


@dataclass
class StoredFile:
    url: str
    key: str


def read_image(upload: UploadFile) -> tuple[bytes, str]:
    """Read an upload, enforce the size limit and check it really is an image. Returns (bytes, ext)."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise RuleViolation(f"Images must be {get_settings().max_upload_mb} MB or smaller")
    if not data:
        raise RuleViolation("The uploaded file is empty")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise RuleViolation("The file is not a valid image") from None
    if image_format not in _ALLOWED_FORMATS:
        raise RuleViolation("Only JPEG, PNG and WebP images are accepted")
    return data, _ALLOWED_FORMATS[image_format]


def save_image(data: bytes, ext: str, folder: str) -> StoredFile:
    settings = get_settings()
    name = f"{uuid.uuid4().hex}.{ext}"
    if settings.storage_backend == "cloudinary":
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(cloudinary_url=settings.cloudinary_url, secure=True)
        result = cloudinary.uploader.upload(
            data, folder=f"turfslot/{folder}", public_id=name.rsplit(".", 1)[0], resource_type="image"
        )
        return StoredFile(url=result["secure_url"], key=result["public_id"])

    path = Path(settings.media_dir) / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return StoredFile(url=f"/media/{folder}/{name}", key=f"{folder}/{name}")


def delete_image(key: str | None) -> None:
    """Best-effort delete; a storage hiccup should not fail the API call."""
    if not key:
        return
    settings = get_settings()
    try:
        if settings.storage_backend == "cloudinary":
            import cloudinary
            import cloudinary.uploader

            cloudinary.config(cloudinary_url=settings.cloudinary_url, secure=True)
            cloudinary.uploader.destroy(key, resource_type="image")
        else:
            media_root = Path(settings.media_dir).resolve()
            target = (media_root / key).resolve()
            if media_root in target.parents:
                target.unlink(missing_ok=True)
    except Exception:
        logger.exception("failed to delete stored image", extra={"key": key})
