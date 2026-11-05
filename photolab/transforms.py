"""Named looks that stay photographs: every recipe only remaps tone, colour and grain.

Each is deliberately mild. The thing that makes an edit read as an edit is the strength,
far more than the operation.
"""

from collections.abc import Callable

import cv2
import numpy as np

Recipe = Callable[[np.ndarray, np.random.Generator], np.ndarray]


def white_balance(rgb: np.ndarray, strength: float = 0.6) -> np.ndarray:
    """Pull an overall colour cast towards neutral, leaving some of it as atmosphere."""
    lab = _to_lab(rgb)
    lab[..., 1:] -= lab[..., 1:].mean(axis=(0, 1)) * strength
    return _to_uint8(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB))


def local_contrast(rgb: np.ndarray, strength: float = 0.5) -> np.ndarray:
    """CLAHE on lightness alone, blended back so midtones gain depth without the HDR look."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab)
    equalised = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
    lab[..., 0] = np.round(lab[..., 0] + (equalised.astype(np.float32) - lab[..., 0]) * strength)
    return cv2.cvtColor(lab, cv2.COLOR_Lab2RGB)


def tone_curve(rgb: np.ndarray, contrast: float, lift: float) -> np.ndarray:
    """A gentle S-curve; `lift` raises the blacks the way film and print do."""
    values = rgb.astype(np.float32) / 255
    curved = values + contrast * np.sin(2 * np.pi * values) * -0.05
    return _to_uint8(lift + curved * (1 - lift))


def warm_shift(rgb: np.ndarray, amount: float) -> np.ndarray:
    """Warm the highlights more than the shadows, which stay cool as they do at sunset."""
    lab = _to_lab(rgb)
    highlights = (lab[..., 0] / 100) ** 1.5
    lab[..., 1] += amount * 0.35 * highlights
    lab[..., 2] += amount * highlights
    return _to_uint8(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB))


def add_grain(rgb: np.ndarray, rng: np.random.Generator, sigma: float) -> np.ndarray:
    """Luminance grain, heaviest in the midtones, where film shows it."""
    height, width = rgb.shape[:2]
    grain = cv2.GaussianBlur(rng.standard_normal((height, width)).astype(np.float32), (0, 0), 0.7)
    grain = grain / (grain.std() + 1e-6) * sigma
    values = rgb.astype(np.float32) / 255
    luma = values.mean(axis=2)
    weight = 0.4 + 0.6 * (1 - np.abs(2 * luma - 1))
    return _to_uint8(values + (grain * weight)[..., None])


def _to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab).astype(np.float32)


def _to_uint8(values: np.ndarray) -> np.ndarray:
    return np.round(np.clip(values, 0, 1) * 255).astype(np.uint8)


def natural(rgb: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return local_contrast(white_balance(rgb))


def film(rgb: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return add_grain(tone_curve(white_balance(rgb, 0.4), contrast=0.7, lift=0.025), rng, 0.012)


def golden_hour(rgb: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    warmed = warm_shift(white_balance(rgb, 0.3), amount=12.0)
    return add_grain(tone_curve(warmed, contrast=0.4, lift=0.015), rng, 0.006)


RECIPES: dict[str, Recipe] = {"natural": natural, "film": film, "golden_hour": golden_hour}


def apply_recipe(rgb: np.ndarray, name: str, seed: int = 0) -> np.ndarray:
    if name not in RECIPES:
        raise ValueError(f"unknown recipe: {name}")
    return RECIPES[name](rgb, np.random.default_rng(seed))
