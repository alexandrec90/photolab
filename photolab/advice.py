"""Whether two photos can believably be one, decided from their annotations.

No colour trick survives light coming from two directions. This reads what the
annotations already record, so the answer costs no LLM call and no pixels.
"""

from dataclasses import dataclass, field
from typing import Any

from photolab.compositing import Light

_OPPOSITE = {"left": "right", "right": "left"}
_LIGHTS: frozenset[str] = frozenset({"left", "right", "front", "back", "above", "diffuse"})
_CUT_OFF_SHOTS = frozenset({"close_up", "portrait", "half_body"})


@dataclass
class CompositeAdvice:
    light: Light = "diffuse"
    flip: bool = False
    warnings: list[str] = field(default_factory=list)


def _known(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if value not in (None, "unknown", "mixed", "unclear") else None


def _compare_light(subject: dict[str, Any], background: dict[str, Any]) -> CompositeAdvice:
    """Settings from the two light directions: the one mismatch a flip can actually fix."""
    advice = CompositeAdvice()
    subject_light = _known(subject, "light_direction")
    scene_light = _known(background, "light_direction")
    if scene_light in _LIGHTS:
        advice.light = scene_light  # type: ignore[assignment] # checked against _LIGHTS above
    if subject_light in _OPPOSITE and scene_light == _OPPOSITE[subject_light]:
        advice.flip = True
        advice.warnings.append(
            f"The subject is lit from the {subject_light} and the scene from the "
            f"{scene_light}, so the subject is flipped. Check any text or parting."
        )
    elif subject_light and scene_light and subject_light != scene_light:
        advice.warnings.append(
            f"Light mismatch: the subject's light is {subject_light}, the scene's is {scene_light}."
        )
    return advice


def _capture_warnings(subject: dict[str, Any], background: dict[str, Any]) -> list[str]:
    """Differences between the two shots that nothing done afterwards can correct."""
    warnings = []
    subject_quality = _known(subject, "light_quality")
    scene_quality = _known(background, "light_quality")
    if {subject_quality, scene_quality} == {"hard", "soft"}:
        warnings.append(
            f"The subject's light is {subject_quality} and the scene's is {scene_quality}. "
            "Shadow edges on the face will not match the scene."
        )
    subject_height = _known(subject, "camera_height")
    scene_height = _known(background, "camera_height")
    if subject_height and scene_height and subject_height != scene_height:
        warnings.append(
            f"Camera height differs ({subject_height} vs {scene_height}); perspective "
            "cannot be corrected and the subject may look pasted on."
        )
    return warnings


def _suitability_warnings(subject: dict[str, Any], background: dict[str, Any]) -> list[str]:
    warnings = []
    if subject.get("shot_type") in _CUT_OFF_SHOTS:
        warnings.append(
            "The subject is cut off below the waist, so the baseline must sit at or below "
            "the bottom edge of the frame."
        )
    if subject.get("usable_as_subject") is False:
        warnings.append("The annotation marks this photo as a poor subject to cut out.")
    if background.get("usable_as_background") is False:
        warnings.append("The annotation marks this photo as a poor background.")
    return warnings


def advise(subject: dict[str, Any], background: dict[str, Any]) -> CompositeAdvice:
    """Suggested settings and honest warnings for a subject and background annotation."""
    advice = _compare_light(subject, background)
    advice.warnings += _capture_warnings(subject, background)
    advice.warnings += _suitability_warnings(subject, background)
    return advice
