import io
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms
from PIL.ExifTags import IFD
from PIL.TiffImagePlugin import IFDRational

from photolab import imaging


def save_jpeg(path: Path, image: Image.Image, **options: object) -> Path:
    image.save(path, format="JPEG", quality=95, **options)
    return path


def test_load_rgb_applies_exif_orientation(tmp_path):
    exif = Image.Exif()
    exif[274] = 6  # "rotate 90 CW to display"
    path = save_jpeg(tmp_path / "turned.jpg", Image.new("RGB", (40, 20)), exif=exif)

    assert imaging.load_rgb(path).size == (20, 40)


def test_load_rgb_keeps_an_srgb_tagged_image(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    path = tmp_path / "tagged.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(path, icc_profile=profile.tobytes())

    loaded = imaging.to_array(imaging.load_rgb(path))

    assert np.abs(loaded.astype(int) - np.array([200, 100, 50])).max() <= 2


def test_to_srgb_routes_a_tagged_image_through_its_profile(monkeypatch):
    # Pillow cannot build a wide-gamut profile, so this pins the wiring rather than the
    # numbers: ignoring the tag is what washes phone photos out.
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    image = Image.new("RGB", (4, 4), (10, 20, 30))
    image.info["icc_profile"] = profile.tobytes()
    converted = Image.new("RGB", (4, 4), (1, 2, 3))
    monkeypatch.setattr(ImageCms, "profileToProfile", lambda *_, **__: converted)

    assert imaging.to_srgb(image) is converted


def test_to_srgb_survives_a_corrupt_profile():
    image = Image.new("RGB", (4, 4), (10, 20, 30))
    image.info["icc_profile"] = b"not a profile"

    assert imaging.to_srgb(image).getpixel((0, 0)) == (10, 20, 30)


def test_to_srgb_converts_non_rgb_modes():
    assert imaging.to_srgb(Image.new("L", (4, 4), 128)).mode == "RGB"


def test_fit_within_shrinks_but_never_enlarges():
    assert imaging.fit_within(Image.new("RGB", (4000, 2000)), 1000).size == (1000, 500)
    assert imaging.fit_within(Image.new("RGB", (40, 20)), 1000).size == (40, 20)


def test_encode_jpeg_tags_software_and_drops_other_metadata():
    data = imaging.encode_jpeg(Image.new("RGB", (8, 8)), software="photolab")

    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "JPEG"
        exif = decoded.getexif()
    assert exif[305] == "photolab"
    assert list(exif) == [305]


def test_vision_jpeg_downsizes_and_strips_exif(tmp_path):
    exif = Image.Exif()
    exif[271] = "SecretCam"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[1] = "N"
    path = save_jpeg(tmp_path / "big.jpg", Image.new("RGB", (4000, 3000)), exif=exif)

    with Image.open(io.BytesIO(imaging.vision_jpeg(path))) as sent:
        assert max(sent.size) == imaging.VISION_EDGE
        assert not sent.getexif()


def test_reference_jpeg_keeps_more_detail_and_strips_exif(tmp_path):
    exif = Image.Exif()
    exif[271] = "SecretCam"
    path = save_jpeg(tmp_path / "big.jpg", Image.new("RGB", (4000, 3000)), exif=exif)

    with Image.open(io.BytesIO(imaging.reference_jpeg(path))) as sent:
        assert max(sent.size) == imaging.REFERENCE_EDGE
        assert not sent.getexif()


def test_write_thumbnail_creates_parent_and_fits(tmp_path):
    destination = tmp_path / "a" / "b" / "thumb.webp"

    imaging.write_thumbnail(Image.new("RGB", (2000, 1000)), destination)

    with Image.open(destination) as thumb:
        assert thumb.size == (imaging.THUMBNAIL_EDGE, imaging.THUMBNAIL_EDGE // 2)


def test_array_round_trip():
    pixels = np.full((3, 4, 3), 77, dtype=np.uint8)

    assert (imaging.to_array(imaging.from_array(pixels)) == pixels).all()


def test_exif_summary_reads_capture_settings(tmp_path):
    exif = Image.Exif()
    exif[271] = "Canon"
    exif[272] = "R5"
    sub = exif.get_ifd(IFD.Exif)
    sub[36867] = "2024:07:01 18:30:00"
    sub[34855] = 400
    sub[33437] = IFDRational(Fraction(28, 10))
    path = save_jpeg(tmp_path / "shot.jpg", Image.new("RGB", (8, 8)), exif=exif)

    with Image.open(path) as image:
        summary = imaging.exif_summary(image)

    assert summary["camera_make"] == "Canon"
    assert summary["camera_model"] == "R5"
    assert summary["taken_at"] == "2024:07:01 18:30:00"
    assert summary["iso"] == 400
    assert summary["f_number"] == 2.8


def test_exif_summary_is_empty_without_exif():
    assert imaging.exif_summary(Image.new("RGB", (4, 4))) == {}


def test_parse_taken_at():
    assert imaging.parse_taken_at("2024:07:01 18:30:00") == datetime(2024, 7, 1, 18, 30, tzinfo=UTC)
    assert imaging.parse_taken_at("0000:00:00 00:00:00") is None
    assert imaging.parse_taken_at(None) is None
