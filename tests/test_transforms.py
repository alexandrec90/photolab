import numpy as np
import pytest

from photolab import transforms as t


@pytest.fixture
def photo() -> np.ndarray:
    rng = np.random.default_rng(3)
    ramp = np.tile(np.linspace(30, 220, 200, dtype=np.float32)[None, :, None], (150, 1, 3))
    return np.clip(ramp + rng.normal(0, 8, ramp.shape), 0, 255).astype(np.uint8)


def test_white_balance_reduces_a_colour_cast():
    cast = np.full((20, 20, 3), (180, 120, 90), dtype=np.uint8)

    balanced = t.white_balance(cast, 1.0)

    assert np.ptp(balanced[0, 0].astype(int)) < np.ptp(cast[0, 0].astype(int)) * 0.3


def test_local_contrast_at_zero_strength_is_near_identity(photo):
    assert np.abs(t.local_contrast(photo, 0.0).astype(int) - photo).mean() < 3


def test_tone_curve_lifts_blacks():
    black = np.zeros((4, 4, 3), dtype=np.uint8)

    assert t.tone_curve(black, 0.7, 0.05).min() > 0


def test_warm_shift_warms_highlights_more_than_shadows():
    light = np.full((4, 4, 3), 220, dtype=np.uint8)
    dark = np.full((4, 4, 3), 40, dtype=np.uint8)

    light_change = t.warm_shift(light, 12).astype(int)[0, 0]
    dark_change = t.warm_shift(dark, 12).astype(int)[0, 0]

    assert light_change[0] - light_change[2] > dark_change[0] - dark_change[2] + 5


def test_add_grain_stays_in_range_and_adds_noise():
    flat = np.full((64, 64, 3), 128, dtype=np.uint8)

    grained = t.add_grain(flat, np.random.default_rng(0), 0.02)

    assert grained.std() > 1


@pytest.mark.parametrize("name", sorted(t.RECIPES))
def test_every_recipe_keeps_the_shape_and_changes_the_photo(photo, name):
    result = t.apply_recipe(photo, name)

    assert result.shape == photo.shape
    assert result.dtype == np.uint8
    assert np.abs(result.astype(int) - photo).mean() > 0.5


@pytest.mark.parametrize("name", sorted(t.RECIPES))
def test_every_recipe_stays_mild(photo, name):
    # The whole brief is that the result still reads as a photograph.
    assert np.abs(t.apply_recipe(photo, name).astype(int) - photo).mean() < 25


def test_recipes_are_reproducible_from_a_seed(photo):
    assert (t.apply_recipe(photo, "film", 9) == t.apply_recipe(photo, "film", 9)).all()


def test_natural_and_golden_hour_and_film_are_callable_directly(photo):
    rng = np.random.default_rng(0)

    assert t.natural(photo, rng).shape == photo.shape
    assert t.film(photo, rng).shape == photo.shape
    assert t.golden_hour(photo, rng).shape == photo.shape


def test_unknown_recipe_is_refused(photo):
    with pytest.raises(ValueError, match="unknown recipe"):
        t.apply_recipe(photo, "instagram")
