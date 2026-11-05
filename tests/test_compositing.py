import cv2
import numpy as np
import pytest

from photolab import compositing as c
from photolab.compositing import CompositeOptions, Placement


def full_canvas_matte(shape=(100, 100), box=(30, 70, 40, 60)) -> np.ndarray:
    matte = np.zeros(shape, dtype=np.float32)
    top, bottom, left, right = box
    matte[top:bottom, left:right] = 1.0
    return matte


def test_srgb_linear_round_trip():
    values = np.linspace(0, 1, 50)

    assert np.allclose(c.linear_to_srgb(c.srgb_to_linear(values)), values, atol=1e-6)


def test_crop_to_alpha_trims_to_the_subject():
    matte = full_canvas_matte()
    rgb = np.zeros((100, 100, 3), dtype=np.float32)

    cropped_rgb, cropped_matte = c.crop_to_alpha(rgb, matte)

    assert cropped_rgb.shape[:2] == cropped_matte.shape == (40, 20)


def test_crop_to_alpha_rejects_an_empty_matte():
    with pytest.raises(ValueError, match="no subject"):
        c.crop_to_alpha(np.zeros((4, 4, 3)), np.zeros((4, 4)))


def test_refine_matte_leaves_solid_and_empty_areas_alone():
    rng = np.random.default_rng(0)
    rgb = rng.random((120, 120, 3)).astype(np.float32)
    matte = full_canvas_matte(shape=(120, 120), box=(20, 100, 20, 100))

    refined = c.refine_matte(rgb, matte)

    assert refined[60, 60] == 1.0
    assert refined[2, 2] == 0.0


def test_refine_matte_snaps_a_vague_edge_to_the_real_one():
    # A dark figure on a light ground, whose edge is at column 60, and a matte that only
    # roughly knows that: this is what a model upscaled from 1024px hands over.
    rgb = np.full((120, 120, 3), 0.9, dtype=np.float32)
    rgb[:, :60] = 0.1
    vague = cv2.GaussianBlur(full_canvas_matte(shape=(120, 120), box=(0, 120, 0, 60)), (0, 0), 6)

    refined = c.refine_matte(rgb, vague)

    def edge_width(matte: np.ndarray) -> int:
        return int(((matte[60] > 0.05) & (matte[60] < 0.95)).sum())

    assert edge_width(refined) <= edge_width(vague) / 2
    assert refined[60, 54] > 0.9
    assert refined[60, 66] < 0.1


def test_decontaminate_replaces_background_colour_in_soft_edges(person):
    rgb, matte = person
    values = rgb.astype(np.float32) / 255
    fringe = (matte > 0.1) & (matte < 0.6)
    assert values[fringe][:, 1].mean() > 0.5  # the green background is in the fringe

    cleaned = c.decontaminate_edges(values, matte)

    assert cleaned[fringe][:, 1].mean() < 0.4
    solid = matte >= 0.95
    assert (cleaned[solid] == values[solid]).all()


def test_decontaminate_leaves_a_matte_with_no_solid_area_alone():
    rgb = np.full((8, 8, 3), 0.5, dtype=np.float32)

    assert (c.decontaminate_edges(rgb, np.full((8, 8), 0.5, np.float32)) == rgb).all()


def test_scale_subject_keeps_aspect_and_range(person):
    rgb, matte = person
    scaled_rgb, scaled_matte = c.scale_subject(rgb.astype(np.float32) / 255, matte, 100)

    assert scaled_matte.shape == (100, 60)
    assert scaled_rgb.min() >= 0
    assert scaled_rgb.max() <= 1


def test_paste_layer_clips_at_the_canvas_edge():
    rgb = np.ones((10, 10, 3), dtype=np.float32)
    matte = np.ones((10, 10), dtype=np.float32)

    layer, full = c.paste_layer(rgb, matte, (20, 20), -5, 15)

    assert layer.shape == (20, 20, 3)
    assert full.sum() == 5 * 5


def test_paste_layer_rejects_a_subject_fully_off_canvas():
    with pytest.raises(ValueError, match="outside"):
        c.paste_layer(np.ones((4, 4, 3)), np.ones((4, 4)), (10, 10), 50, 50)


def test_match_color_moves_a_cast_towards_the_scene():
    matte = full_canvas_matte()
    layer = np.full((100, 100, 3), 0.5, dtype=np.float32)
    warm = np.dstack([np.full((100, 100), v, np.float32) for v in (0.8, 0.55, 0.3)])

    matched = c.match_color(layer, matte, warm, 1.0)

    # Red minus blue is the cast; the scene's brightness lifts every channel as well.
    assert matched[50, 50, 0] - matched[50, 50, 2] > 0.1


def test_match_color_at_zero_strength_changes_nothing():
    layer = np.full((100, 100, 3), 0.5, dtype=np.float32)
    scene = np.full((100, 100, 3), 0.9, dtype=np.float32)

    assert (c.match_color(layer, full_canvas_matte(), scene, 0.0) == layer).all()


def test_match_color_is_limited_so_a_person_stays_recognisable():
    layer = np.full((100, 100, 3), 0.5, dtype=np.float32)
    magenta = np.zeros((100, 100, 3), dtype=np.float32)
    magenta[..., 0] = magenta[..., 2] = 1.0

    matched = c.match_color(layer, full_canvas_matte(), magenta, 1.0)

    before = cv2.cvtColor(layer, cv2.COLOR_RGB2Lab)[50, 50]
    after = cv2.cvtColor(matched, cv2.COLOR_RGB2Lab)[50, 50]
    assert abs(after[1] - before[1]) <= 10.5
    assert abs(after[0] - before[0]) <= 18.5


def test_match_color_uses_the_surroundings_rather_than_the_far_corners():
    matte = full_canvas_matte()
    layer = np.full((100, 100, 3), 0.5, dtype=np.float32)
    scene = np.full((100, 100, 3), 0.5, dtype=np.float32)
    scene[:5] = (0.0, 0.0, 1.0)  # a blue strip far above the subject

    matched = c.match_color(layer, matte, scene, 1.0)

    assert np.allclose(matched[50, 50], layer[50, 50], atol=0.02)


def test_acuity_rates_detail_above_blur():
    rng = np.random.default_rng(0)
    sharp = rng.random((64, 64)).astype(np.float32)
    mask = np.ones((64, 64), dtype=bool)

    assert c.acuity(sharp, mask) > c.acuity(cv2.GaussianBlur(sharp, (0, 0), 3), mask)


def test_soften_blurs_a_subject_sharper_than_the_whole_scene():
    rng = np.random.default_rng(0)
    matte = full_canvas_matte(box=(10, 90, 10, 90))
    layer = rng.random((100, 100, 3)).astype(np.float32)
    soft_scene = cv2.GaussianBlur(rng.random((100, 100, 3)).astype(np.float32), (0, 0), 3)

    softened = c.soften_to_acuity(layer, matte, soft_scene)

    assert softened[50, :, 0].std() < layer[50, :, 0].std()


def test_soften_leaves_a_subject_no_sharper_than_the_scene():
    rng = np.random.default_rng(0)
    matte = full_canvas_matte(box=(30, 70, 30, 70))
    layer = cv2.GaussianBlur(rng.random((100, 100, 3)).astype(np.float32), (0, 0), 2)
    sharp_scene = rng.random((100, 100, 3)).astype(np.float32)

    assert (c.soften_to_acuity(layer, matte, sharp_scene) == layer).all()


def test_estimate_noise_recovers_known_sigma():
    rng = np.random.default_rng(0)
    gray = np.clip(0.5 + rng.normal(0, 0.03, (200, 200)), 0, 1).astype(np.float32)

    assert c.estimate_noise(gray, np.ones_like(gray, bool)) == pytest.approx(0.03, rel=0.15)


def test_estimate_noise_is_not_fooled_by_edges():
    gray = np.zeros((200, 200), dtype=np.float32)
    gray[:, 100:] = 1.0

    assert c.estimate_noise(gray, np.ones_like(gray, bool)) == 0


def test_match_grain_adds_what_the_subject_lacks():
    rng = np.random.default_rng(1)
    matte = full_canvas_matte(shape=(200, 200), box=(50, 150, 50, 150))
    layer = np.full((200, 200, 3), 0.5, dtype=np.float32)
    noisy = np.clip(0.5 + rng.normal(0, 0.03, (200, 200, 3)), 0, 1).astype(np.float32)

    grained = c.match_grain(layer, matte, noisy, rng)

    assert grained[100, 60:140, 0].std() == pytest.approx(0.03, abs=0.015)


def test_match_grain_never_adds_noise_to_an_already_noisier_subject():
    rng = np.random.default_rng(1)
    matte = full_canvas_matte()
    layer = np.clip(0.5 + rng.normal(0, 0.05, (100, 100, 3)), 0, 1).astype(np.float32)
    clean_scene = np.full((100, 100, 3), 0.5, dtype=np.float32)

    assert (c.match_grain(layer, matte, clean_scene, rng) == layer).all()


def test_light_wrap_brightens_edges_more_than_the_middle():
    matte = cv2.GaussianBlur(full_canvas_matte(box=(20, 80, 20, 80)), (0, 0), 1.0)
    layer = np.zeros((100, 100, 3), dtype=np.float32)
    bright = np.ones((100, 100, 3), dtype=np.float32)

    wrapped = c.apply_light_wrap(layer, matte, bright, 1.0)

    assert wrapped[18:24, 50, 0].max() > wrapped[50, 50, 0] + 0.1


def test_light_wrap_at_zero_strength_is_a_no_op():
    layer = np.zeros((10, 10, 3), dtype=np.float32)

    assert c.apply_light_wrap(layer, np.ones((10, 10)), layer + 1, 0).sum() == 0


def test_contact_shadow_comes_from_what_touches_the_ground():
    # A figure with a wide umbrella overhead: it must not widen the shadow at the feet.
    matte = full_canvas_matte(shape=(300, 300), box=(60, 250, 140, 160))
    matte[60:100, 40:260] = 1.0

    shadow = c.contact_shadow(matte, foot=249, height=190)

    assert shadow[245, 150] > 0.8
    assert shadow[245, 50] < 0.05


def test_contact_shadow_stays_on_the_ground():
    matte = full_canvas_matte(shape=(300, 300), box=(60, 250, 140, 160))

    assert c.contact_shadow(matte, foot=249, height=190)[100, 150] == 0


def test_soften_ignores_a_scene_that_merely_has_soft_areas():
    # Mostly smooth water with a little sharp foam: the foam sets the ceiling.
    rng = np.random.default_rng(0)
    matte = full_canvas_matte(shape=(200, 200), box=(60, 180, 60, 140))
    scene = cv2.GaussianBlur(rng.random((200, 200, 3)).astype(np.float32), (0, 0), 3)
    scene[:40, :60] = rng.random((40, 60, 3)).astype(np.float32)
    layer = cv2.GaussianBlur(rng.random((200, 200, 3)).astype(np.float32), (0, 0), 1)

    assert (c.soften_to_acuity(layer, matte, scene) == layer).all()


def test_every_light_gives_a_contact_shadow_at_the_feet():
    matte = full_canvas_matte(shape=(200, 200), box=(40, 150, 90, 110))

    for light in ("left", "right", "front", "back", "above", "diffuse"):
        assert c.cast_shadow(matte, light)[150, 100] > 0.2


def test_light_from_the_left_casts_to_the_right():
    matte = full_canvas_matte(shape=(200, 300), box=(40, 150, 140, 160))

    shadow = c.cast_shadow(matte, "left")
    ground = shadow[135:150]

    assert ground[:, 165:].sum() > ground[:, :135].sum() * 2


def test_light_from_the_right_casts_to_the_left():
    matte = full_canvas_matte(shape=(200, 300), box=(40, 150, 140, 160))

    ground = c.cast_shadow(matte, "right")[135:150]

    assert ground[:, :135].sum() > ground[:, 165:].sum() * 2


def test_backlight_casts_towards_the_camera():
    matte = full_canvas_matte(shape=(200, 300), box=(40, 120, 140, 160))

    shadow = c.cast_shadow(matte, "back")

    assert shadow[140:170].sum() > c.cast_shadow(matte, "diffuse")[140:170].sum() * 2


def test_composite_leaves_the_far_scene_untouched(scene, person):
    rgb, matte = person

    result = c.composite(rgb, matte, scene, Placement(center_x=0.5, height=0.5))

    assert result.shape == scene.shape
    assert result.dtype == np.uint8
    assert np.abs(result[:20, :20].astype(int) - scene[:20, :20]).max() <= 1


def test_composite_keeps_the_subject_recognisably_itself(scene, person):
    rgb, matte = person

    result = c.composite(
        rgb, matte, scene, Placement(center_x=0.5, baseline_y=0.9, height=0.6)
    ).astype(int)

    middle = result[120, 160]
    assert middle[2] > middle[1]  # still its blue, not the scene's warm cast


def test_composite_has_no_trace_of_the_old_background(scene, person):
    rgb, matte = person

    result = c.composite(rgb, matte, scene, Placement(height=0.6), CompositeOptions()).astype(int)

    greener = result[..., 1] - (result[..., 0] + result[..., 2]) / 2
    assert greener.max() < 40


def test_composite_flip_mirrors_the_subject(scene):
    rgb = np.zeros((100, 60, 3), dtype=np.uint8)
    rgb[:, :30] = (250, 0, 0)
    rgb[:, 30:] = (0, 0, 250)
    matte = np.ones((100, 60), dtype=np.float32)
    options = CompositeOptions(color_match=0, shadow_strength=0, light_wrap=0)

    plain = c.composite(rgb, matte, scene, Placement(height=0.5, baseline_y=0.7), options)
    flipped = c.composite(
        rgb, matte, scene, Placement(height=0.5, baseline_y=0.7, flip=True), options
    )

    assert plain[100, 145, 0] > plain[100, 145, 2]
    assert flipped[100, 145, 0] < flipped[100, 145, 2]


def test_composite_is_reproducible_from_its_seed(scene, person):
    rgb, matte = person

    first = c.composite(rgb, matte, scene, options=CompositeOptions(seed=5))
    second = c.composite(rgb, matte, scene, options=CompositeOptions(seed=5))

    assert (first == second).all()


def test_shadow_strength_zero_leaves_the_ground_alone(scene, person):
    rgb, matte = person
    options = CompositeOptions(shadow_strength=0, light_wrap=0)

    result = c.composite(rgb, matte, scene, Placement(baseline_y=0.8, height=0.5), options)

    below = (scene.shape[0] * 4) // 5 + 4
    assert np.abs(result[below:].astype(int) - scene[below:]).max() <= 1
