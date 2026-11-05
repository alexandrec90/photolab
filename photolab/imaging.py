"""Reading photos into one canonical form, and writing them back out.

Every pixel operation in this project assumes sRGB. A phone shoots Display P3 and a
camera may shoot Adobe RGB, and treating those numbers as sRGB washes the colour out, so
the conversion happens here, once, at the only door pixels come in by.
"""

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageCms, ImageOps
from PIL.ExifTags import IFD
from pillow_heif import register_heif_opener

register_heif_opener()

SUPPORTED_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
)
# What a browser cannot be handed directly.
TRANSCODED_FORMATS = frozenset({"HEIF", "HEIC", "TIFF"})

THUMBNAIL_EDGE = 640
# The longest edge the vision API uses before it downsamples anyway, so anything larger
# is bandwidth spent on nothing.
VISION_EDGE = 1568
# An image model keeps more of a face from a larger reference.
REFERENCE_EDGE = 2048

_SRGB = ImageCms.createProfile("sRGB")

_EXIF_TAGS = {
    "camera_make": 271,
    "camera_model": 272,
}
_EXIF_SUB_TAGS = {
    "exposure_time": 33434,
    "f_number": 33437,
    "iso": 34855,
    "taken_at": 36867,
    "focal_length": 37386,
    "lens": 42036,
}


def to_srgb(image: Image.Image) -> Image.Image:
    """An RGB copy of `image` with its embedded colour profile applied."""
    icc = image.info.get("icc_profile")
    rgb = image.convert("RGB")
    if not icc:
        return rgb
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.profileToProfile(rgb, source, _SRGB, outputMode="RGB") or rgb
    except (ImageCms.PyCMSError, OSError):
        # A corrupt or non-RGB profile. The pixels are still the best thing available.
        return rgb


def load_rgb(path: Path) -> Image.Image:
    """Open `path` upright and in sRGB."""
    with Image.open(path) as image:
        return to_srgb(ImageOps.exif_transpose(image) or image)


def fit_within(image: Image.Image, long_edge: int) -> Image.Image:
    """A copy no larger than `long_edge`, never enlarged."""
    fitted = image.copy()
    fitted.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return fitted


def encode_jpeg(image: Image.Image, quality: int = 92, software: str | None = None) -> bytes:
    """JPEG bytes with full-resolution chroma and no metadata but `software`."""
    exif = Image.Exif()
    if software:
        exif[305] = software
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=0, exif=exif)
    return buffer.getvalue()


def write_thumbnail(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fit_within(image, THUMBNAIL_EDGE).save(destination, format="WEBP", quality=80)


def vision_jpeg(path: Path) -> bytes:
    """What the LLM gets: downscaled, and re-encoded so no EXIF or GPS leaves the machine."""
    return encode_jpeg(fit_within(load_rgb(path), VISION_EDGE), quality=85)


def reference_jpeg(path: Path) -> bytes:
    """What an image model gets, stripped in the same way."""
    return encode_jpeg(fit_within(load_rgb(path), REFERENCE_EDGE), quality=90)


def to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def from_array(pixels: np.ndarray) -> Image.Image:
    return Image.fromarray(pixels.astype(np.uint8), mode="RGB")


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 ")
    if isinstance(value, str):
        return value.strip("\x00 ")
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, int):
        return value
    # EXIF's `IFDRational`. A zero denominator comes back as NaN, which JSON cannot hold.
    number = float(value)
    return None if number != number else number


def exif_summary(image: Image.Image) -> dict[str, Any]:
    """The capture settings worth keeping. Unreadable or absent EXIF is just `{}`."""
    exif = image.getexif()
    sub = exif.get_ifd(IFD.Exif)
    found = {name: exif.get(tag) for name, tag in _EXIF_TAGS.items()}
    found |= {name: sub.get(tag) for name, tag in _EXIF_SUB_TAGS.items()}
    summary: dict[str, Any] = {}
    for name, value in found.items():
        if value is None:
            continue
        try:
            safe = _json_safe(value)
        except (TypeError, ValueError):
            continue
        if safe is not None:
            summary[name] = safe
    return summary


def parse_taken_at(value: Any) -> datetime | None:
    """EXIF's `YYYY:MM:DD HH:MM:SS`, which carries no zone; recorded as if UTC."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
