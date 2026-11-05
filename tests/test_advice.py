from photolab.advice import CompositeAdvice, advise


def test_nothing_known_gives_defaults_and_no_warnings():
    advice = advise({}, {})

    assert isinstance(advice, CompositeAdvice)
    assert advice.light == "diffuse"
    assert advice.flip is False
    assert advice.warnings == []


def test_the_scene_decides_the_shadow_direction():
    assert advise({}, {"light_direction": "right"}).light == "right"


def test_unknown_scene_light_stays_diffuse():
    assert advise({}, {"light_direction": "unknown"}).light == "diffuse"


def test_opposite_side_light_is_fixed_by_flipping():
    advice = advise({"light_direction": "left"}, {"light_direction": "right"})

    assert advice.flip is True
    assert "flipped" in advice.warnings[0]


def test_unfixable_light_mismatch_is_a_warning_not_a_flip():
    advice = advise({"light_direction": "front"}, {"light_direction": "back"})

    assert advice.flip is False
    assert "mismatch" in advice.warnings[0]


def test_matching_light_is_quiet():
    data = {"light_direction": "left", "light_quality": "hard", "camera_height": "low"}

    assert advise(data, data).warnings == []


def test_hard_light_on_a_soft_scene_is_flagged():
    advice = advise({"light_quality": "hard"}, {"light_quality": "soft"})

    assert "will not match" in advice.warnings[0]


def test_camera_height_mismatch_is_flagged():
    advice = advise({"camera_height": "low"}, {"camera_height": "high"})

    assert "perspective" in advice.warnings[0]


def test_a_cut_off_subject_is_told_about_the_baseline():
    assert "bottom edge" in advise({"shot_type": "half_body"}, {}).warnings[0]


def test_poor_candidates_are_flagged():
    assert "poor subject" in advise({"usable_as_subject": False}, {}).warnings[0]
    assert "poor background" in advise({}, {"usable_as_background": False}).warnings[0]
