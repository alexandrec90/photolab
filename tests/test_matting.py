from types import SimpleNamespace

import numpy as np
from PIL import Image

from photolab import matting


def test_matte_path_is_keyed_by_content_and_model(tmp_path):
    assert matting.matte_path(tmp_path, "abc", "m").name == "abc-m.png"


def test_subject_matte_runs_the_model_once_and_caches(tmp_path, monkeypatch):
    calls: list[str] = []

    def model(image: Image.Image, name: str) -> Image.Image:
        calls.append(name)
        return Image.new("L", image.size, 128)

    monkeypatch.setattr(matting, "run_model", model)
    image = Image.new("RGB", (20, 10))
    cache = tmp_path / "mattes" / "x.png"

    first = matting.subject_matte(image, cache, "m")
    second = matting.subject_matte(image, cache, "m")

    assert calls == ["m"]
    assert first.shape == (10, 20)
    assert first.max() <= 1.0
    assert np.allclose(first, second)


def test_subject_matte_resizes_a_cached_matte_to_the_image(tmp_path):
    cache = tmp_path / "x.png"
    Image.new("L", (5, 5), 255).save(cache)

    matte = matting.subject_matte(Image.new("RGB", (20, 10)), cache, "m")

    assert matte.shape == (10, 20)


def test_run_model_asks_rembg_for_a_mask(monkeypatch):
    seen = {}

    def remove(image, session, only_mask):
        seen.update(session=session, only_mask=only_mask)
        return image

    monkeypatch.setitem(
        __import__("sys").modules,
        "rembg",
        SimpleNamespace(new_session=lambda name: f"session:{name}", remove=remove),
    )
    matting._session.cache_clear()

    matting.run_model(Image.new("L", (2, 2)), "some-model")
    matting._session.cache_clear()

    assert seen == {"session": "session:some-model", "only_mask": True}
