"""Placing a real person into a real scene, using only their real pixels.

Fully generated images look generated because every pixel is invented. Here nothing is:
the face and the scene stay photographs, and the work is removing the handful of tells
that give a cut-out away. Each function below answers one of them.

| Tell | What fixes it |
| --- | --- |
| a halo of the old background around the hair | `decontaminate_edges` |
| the person lit by a different sky | `match_color` |
| too crisp for the scene they are in | `soften_to_acuity` |
| clean skin on a noisy photo | `match_grain` |
| a hard edge against bright surroundings | `apply_light_wrap` |
| floating, with no contact with the ground | `cast_shadow` |

All of it is pure numpy and OpenCV: no network, and no model beyond the matte.
"""

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

Light = Literal["left", "right", "front", "back", "above", "diffuse"]

_MIN_ALPHA = 0.02
_SOLID_ALPHA = 0.95
# How far colour matching may move a subject. Past this it stops looking like the same
# person: a sunset should warm a face, not paint it orange.
_MAX_LIGHTNESS_SHIFT = 18.0
_MAX_CHROMA_SHIFT = 10.0
_CONTRAST_RANGE = (0.8, 1.25)
_ACUITY_TILE = 32
# Deliberately short: softening a real face is a cost, so it stops well before it shows.
_BLUR_LADDER = (0.4, 0.7, 1.0)
_MATTE_LEVELS = (0.2, 0.85)
# How much of a person, measured up from their lowest point, shades the ground: the part
# touching it, and the part blocking the sky above it.
_CONTACT_BAND = 0.1
_POOL_BAND = 0.4
# Shear and squash of the cast shadow per light direction. Squash is negative when the
# shadow falls towards the camera rather than away from it.
_SHADOW_SHAPE: dict[str, tuple[float, float]] = {
    "left": (0.7, 0.16),
    "right": (-0.7, 0.16),
    "front": (0.12, 0.4),
    "back": (0.12, -0.35),
}


@dataclass(frozen=True)
class Placement:
    """Where the subject goes, in fractions of the background."""

    center_x: float = 0.5
    baseline_y: float = 0.92
    height: float = 0.6
    flip: bool = False


@dataclass(frozen=True)
class CompositeOptions:
    color_match: float = 0.6
    shadow_strength: float = 0.6
    light_wrap: float = 0.3
    light: Light = "diffuse"
    seed: int = 0


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, 0.0, 1.0)
    return np.where(clipped <= 0.0031308, clipped * 12.92, 1.055 * clipped ** (1 / 2.4) - 0.055)


def crop_to_alpha(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trim to the subject, so placement is relative to the person and not their photo."""
    rows = np.flatnonzero((alpha > _MIN_ALPHA).any(axis=1))
    columns = np.flatnonzero((alpha > _MIN_ALPHA).any(axis=0))
    if rows.size == 0:
        raise ValueError("the matte is empty: no subject was found in this photo")
    top, bottom = rows[0], rows[-1] + 1
    left, right = columns[0], columns[-1] + 1
    return rgb[top:bottom, left:right], alpha[top:bottom, left:right]


def refine_matte(rgb: np.ndarray, alpha: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Snap a matte's edges to the photo's own edges, with a guided filter.

    Matting models infer at about 1024px, so on a full-size photo their edge is a smooth
    guess that ignores individual strands of hair. A guided filter transfers the image's
    real edges into the matte, and it leaves anything uniform alone: where the matte is
    flatly 0 or 1 the covariance is zero, so only the boundary changes. The levels at the
    end choke what remains, because a feathered edge shows as a glow against the new scene.
    """
    radius = max(min(alpha.shape) // 200, 4)
    size = (2 * radius + 1, 2 * radius + 1)
    guide = _gray(rgb)
    mean_guide = cv2.blur(guide, size)
    mean_alpha = cv2.blur(alpha, size)
    covariance = cv2.blur(guide * alpha, size) - mean_guide * mean_alpha
    variance = cv2.blur(guide * guide, size) - mean_guide * mean_guide
    gain = covariance / (variance + eps)
    offset = mean_alpha - gain * mean_guide
    refined = cv2.blur(gain, size) * guide + cv2.blur(offset, size)
    low, high = _MATTE_LEVELS
    return np.clip((refined - low) / (high - low), 0, 1)


def decontaminate_edges(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Replace edge colours with colour pulled outwards from solid subject.

    A soft edge pixel is a mix of hair and the old background, so compositing it keeps a
    fringe of that background. Normalised convolution from the solid interior gives each
    such pixel the subject's colour, and alpha then does the blending.
    """
    known = (alpha >= _SOLID_ALPHA).astype(np.float32)
    if not known.any():
        return rgb
    filled = rgb.copy()
    for sigma in (2.0, 6.0, 18.0):
        mass = cv2.GaussianBlur(known, (0, 0), sigma)
        spread = cv2.GaussianBlur(filled * known[..., None], (0, 0), sigma)
        reached = (known == 0) & (mass > 1e-3)
        filled[reached] = spread[reached] / mass[reached][..., None]
        known[reached] = 1
    return filled


def scale_subject(
    rgb: np.ndarray, alpha: np.ndarray, target_height: int
) -> tuple[np.ndarray, np.ndarray]:
    height, width = alpha.shape
    factor = target_height / height
    size = (max(1, round(width * factor)), max(1, target_height))
    interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_LANCZOS4
    scaled_rgb = cv2.resize(rgb, size, interpolation=interpolation)
    scaled_alpha = cv2.resize(alpha, size, interpolation=interpolation)
    return np.clip(scaled_rgb, 0, 1), np.clip(scaled_alpha, 0, 1)


def paste_layer(
    rgb: np.ndarray, alpha: np.ndarray, shape: tuple[int, int], left: int, top: int
) -> tuple[np.ndarray, np.ndarray]:
    """Lay the subject on a canvas of `shape`, clipped to its edges."""
    canvas_height, canvas_width = shape
    layer = np.zeros((canvas_height, canvas_width, 3), dtype=np.float32)
    matte = np.zeros((canvas_height, canvas_width), dtype=np.float32)
    height, width = alpha.shape
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + width, canvas_width), min(top + height, canvas_height)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("the placement puts the subject entirely outside the background")
    source = (slice(y0 - top, y1 - top), slice(x0 - left, x1 - left))
    layer[y0:y1, x0:x1] = rgb[source]
    matte[y0:y1, x0:x1] = alpha[source]
    return layer, matte


def _surroundings(matte: np.ndarray, margin: float = 0.3) -> np.ndarray:
    """The area around the subject: what actually lights them, unlike the far corners."""
    rows = np.flatnonzero(matte.max(axis=1) > _MIN_ALPHA)
    columns = np.flatnonzero(matte.max(axis=0) > _MIN_ALPHA)
    pad_y = int((rows[-1] - rows[0]) * margin)
    pad_x = int((columns[-1] - columns[0]) * margin)
    region = np.zeros(matte.shape, dtype=bool)
    region[
        max(rows[0] - pad_y, 0) : rows[-1] + pad_y + 1,
        max(columns[0] - pad_x, 0) : columns[-1] + pad_x + 1,
    ] = True
    return region & (matte < _MIN_ALPHA)


def match_color(
    layer: np.ndarray, matte: np.ndarray, background: np.ndarray, strength: float
) -> np.ndarray:
    """Move the subject's tone and white balance towards the light around them."""
    subject = matte > _SOLID_ALPHA
    around = _surroundings(matte)
    if strength <= 0 or not subject.any() or not around.any():
        return layer
    lab = cv2.cvtColor(layer, cv2.COLOR_RGB2Lab)
    reference = cv2.cvtColor(background, cv2.COLOR_RGB2Lab)
    fg_mean, fg_std = lab[subject].mean(axis=0), lab[subject].std(axis=0) + 1e-3
    bg_mean, bg_std = reference[around].mean(axis=0), reference[around].std(axis=0)

    contrast = np.clip(bg_std[0] / fg_std[0], *_CONTRAST_RANGE)
    lightness_shift = np.clip(bg_mean[0] - fg_mean[0], -_MAX_LIGHTNESS_SHIFT, _MAX_LIGHTNESS_SHIFT)
    chroma_shift = np.clip(bg_mean[1:] - fg_mean[1:], -_MAX_CHROMA_SHIFT, _MAX_CHROMA_SHIFT)

    matched = lab.copy()
    lightness = fg_mean[0] + (lab[..., 0] - fg_mean[0]) * (1 + (contrast - 1) * strength)
    matched[..., 0] = np.clip(lightness + lightness_shift * strength, 0, 100)
    matched[..., 1:] += chroma_shift * strength
    return np.clip(cv2.cvtColor(matched, cv2.COLOR_Lab2RGB), 0, 1)


def _gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def acuity(gray: np.ndarray, mask: np.ndarray, percentile: float = 90) -> float:
    """How crisp a region is, as a percentile of its tiled Laplacian energy."""
    energy = cv2.blur(cv2.Laplacian(gray, cv2.CV_32F) ** 2, (_ACUITY_TILE, _ACUITY_TILE))
    return float(np.percentile(energy[mask], percentile)) if mask.any() else 0.0


def soften_to_acuity(layer: np.ndarray, matte: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Bring a subject sharper than anything in the scene down to the scene's acuity.

    The comparison is against the scene's very sharpest detail and not the area behind
    the subject, because a sharp person on a defocused background is how portraits look.
    Only a subject whose ordinary detail beats the best the lens managed anywhere in that
    photograph is wrong, and softer than the scene's sharpest detail reads as pasted in.
    """
    subject = cv2.erode((matte > _SOLID_ALPHA).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    if not subject.any():
        return layer
    ceiling = acuity(_gray(background), matte < _MIN_ALPHA, percentile=99)
    softened = layer
    for sigma in _BLUR_LADDER:
        if acuity(_gray(softened), subject) <= ceiling:
            break
        softened = cv2.GaussianBlur(layer, (0, 0), sigma)
    return softened


def estimate_noise(gray: np.ndarray, mask: np.ndarray) -> float:
    """Immerkaer's sigma, with the median so that image detail does not read as noise."""
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    response = np.abs(cv2.filter2D(gray, cv2.CV_32F, kernel))
    return float(np.median(response[mask]) / (0.6745 * 6)) if mask.any() else 0.0


def match_grain(
    layer: np.ndarray, matte: np.ndarray, background: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Add the noise the subject is missing, so one sensor seems to have seen everything."""
    subject = matte > _SOLID_ALPHA
    scene_noise = estimate_noise(_gray(background), matte < _MIN_ALPHA)
    own_noise = estimate_noise(_gray(layer), subject)
    missing = scene_noise**2 - own_noise**2
    if missing <= 0:
        return layer
    noise = rng.standard_normal(matte.shape).astype(np.float32)
    # Real grain spans more than one pixel; white noise reads as digital.
    grain = cv2.GaussianBlur(noise, (0, 0), 0.6)
    grain = grain / (grain.std() + 1e-6) * float(np.sqrt(missing))
    return np.clip(layer + grain[..., None], 0, 1)


def apply_light_wrap(
    layer: np.ndarray, matte: np.ndarray, background: np.ndarray, strength: float
) -> np.ndarray:
    """Let the scene's light spill over the subject's edges, as it does on a real lens."""
    if strength <= 0:
        return layer
    extent = int(np.flatnonzero(matte.max(axis=1) > _MIN_ALPHA).size)
    sigma = max(extent * 0.012, 1.0)
    glow = cv2.GaussianBlur(background, (0, 0), sigma * 2)
    band = np.clip(matte * (1 - cv2.GaussianBlur(matte, (0, 0), sigma)) * 2, 0, 1)
    return layer + (glow - layer) * (band * strength)[..., None]


def _laid_on_ground(
    matte: np.ndarray, foot: int, height: int, band: float, squash: float
) -> np.ndarray:
    """The lowest `band` of a figure, pressed onto the ground plane at its feet."""
    slice_ = matte.copy()
    slice_[: max(foot - int(height * band), 0)] = 0
    # A little below the feet: a sole sits on its shadow, not beside it.
    pivot = foot + height * 0.006
    transform = np.array([[1, 0, 0], [0, squash, pivot * (1 - squash)]], np.float32)
    return cv2.warpAffine(slice_, transform, (matte.shape[1], matte.shape[0]))


def contact_shadow(matte: np.ndarray, foot: int, height: int) -> np.ndarray:
    """Where a person meets the ground, under any light.

    Only the lower figure is projected: squashing all of it would lay an umbrella or a
    raised arm on the ground too. A tight core marks what touches, inside a broad pool
    from the body blocking the sky, which is the shape shadows have even on an overcast day.
    """
    core = cv2.GaussianBlur(
        _laid_on_ground(matte, foot, height, _CONTACT_BAND, 0.25),
        (0, 0),
        max(height * 0.005, 1.0),
    )
    pool = cv2.GaussianBlur(
        _laid_on_ground(matte, foot, height, _POOL_BAND, 0.1),
        (0, 0),
        max(height * 0.025, 2.0),
    )
    return np.clip(np.maximum(core, pool * 1.5), 0, 1)


def cast_shadow(matte: np.ndarray, light: Light) -> np.ndarray:
    """Occlusion in `[0, 1]`: a contact shadow, plus a projected one if the light allows.

    The contact shadow does most of the work of grounding someone. The cast shadow softens
    with distance, as a real penumbra does.
    """
    rows = np.flatnonzero(matte.max(axis=1) > _MIN_ALPHA)
    foot, head = int(rows[-1]), int(rows[0])
    height = max(foot - head, 1)
    canvas = (matte.shape[1], matte.shape[0])
    occlusion = contact_shadow(matte, foot, height)

    if light in _SHADOW_SHAPE:
        shear, squash = _SHADOW_SHAPE[light]
        transform = np.array(
            [[1, -shear, shear * foot], [0, squash, foot * (1 - squash)]], np.float32
        )
        projected = cv2.warpAffine(matte, transform, canvas)
        near = cv2.GaussianBlur(projected, (0, 0), max(height * 0.008, 1.0))
        far = cv2.GaussianBlur(projected, (0, 0), max(height * 0.04, 2.0))
        reach = np.arange(matte.shape[0], dtype=np.float32) - foot
        fade = np.clip(1 - np.abs(reach) / (height * max(abs(squash), 0.1) + 1), 0, 1)[:, None]
        cast = (far + (near - far) * fade) * (0.25 + 0.75 * fade)
        occlusion = np.maximum(occlusion, cast * 0.8)
    return np.clip(occlusion, 0, 1)


def composite(
    subject: np.ndarray,
    alpha: np.ndarray,
    background: np.ndarray,
    placement: Placement = Placement(),  # noqa: B008 - frozen dataclass, so a safe shared default
    options: CompositeOptions = CompositeOptions(),  # noqa: B008 - frozen dataclass, as above
) -> np.ndarray:
    """Place `subject` (uint8 RGB with a float matte) into `background` (uint8 RGB)."""
    rng = np.random.default_rng(options.seed)
    scene = background.astype(np.float32) / 255
    canvas_height, canvas_width = scene.shape[:2]

    photo = subject.astype(np.float32) / 255
    rgb, matte = crop_to_alpha(photo, refine_matte(photo, alpha.astype(np.float32)))
    if placement.flip:
        rgb, matte = rgb[:, ::-1], matte[:, ::-1]
    rgb = decontaminate_edges(rgb, matte)
    target_height = max(round(canvas_height * placement.height), 8)
    rgb, matte = scale_subject(rgb, matte, target_height)

    left = round(canvas_width * placement.center_x - matte.shape[1] / 2)
    top = round(canvas_height * placement.baseline_y - matte.shape[0])
    layer, full_matte = paste_layer(rgb, matte, (canvas_height, canvas_width), left, top)

    layer = match_color(layer, full_matte, scene, options.color_match)
    layer = soften_to_acuity(layer, full_matte, scene)
    layer = match_grain(layer, full_matte, scene, rng)
    layer = apply_light_wrap(layer, full_matte, scene, options.light_wrap)
    occlusion = cast_shadow(full_matte, options.light) * options.shadow_strength

    # Blended in linear light: in gamma space a soft edge goes darker than either side.
    shaded = srgb_to_linear(scene) * (1 - occlusion[..., None])
    blended = shaded + (srgb_to_linear(np.clip(layer, 0, 1)) - shaded) * full_matte[..., None]
    return np.round(linear_to_srgb(blended) * 255).astype(np.uint8)
