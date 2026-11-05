"""Separating a person from their photo.

The matte is a pure function of a photo's bytes and the model, so it is cached by hash:
the first composite of a subject takes the model's whole run on CPU, and later ones reuse
it while placement and colour are tried.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def matte_path(data_dir: Path, sha256: str, model: str) -> Path:
    return data_dir / "mattes" / f"{sha256}-{model}.png"


@lru_cache(maxsize=1)
def _session(model: str) -> Any:
    # Imported here because it loads onnxruntime, which every other command can do without.
    from rembg import new_session  # type: ignore[import-untyped] # ships no stubs

    return new_session(model)


def run_model(image: Image.Image, model: str) -> Image.Image:
    from rembg import remove

    return remove(image, session=_session(model), only_mask=True)


def subject_matte(image: Image.Image, cache_file: Path, model: str) -> np.ndarray:
    """A float matte in `[0, 1]`, the size of `image`."""
    if cache_file.exists():
        with Image.open(cache_file) as cached:
            mask = cached.convert("L").copy()
    else:
        mask = run_model(image, model).convert("L")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        mask.save(cache_file)
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.BILINEAR)
    return np.asarray(mask, dtype=np.float32) / 255
