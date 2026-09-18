"""Conservative screenshot verification for Star Citizen reputation submissions."""

from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable

from PIL import Image

from src.reputation import REPUTATION_LADDERS


_OCR_ENGINE: Any | None = None
_OCR_LOCK = threading.Lock()


@dataclass(frozen=True)
class ReputationVerification:
    verified: bool
    detected_giver: str | None
    detected_level: str | None
    confidence: float
    reason: str


def _normalized(value: str) -> str:
    value = value.upper().replace("SENIOR", "SR").replace("JUNIOR", "JR")
    return " ".join(re.findall(r"[A-Z0-9]+", value))


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalized(left), _normalized(right)).ratio()


def _box(item: list[Any]) -> tuple[float, float, float, float]:
    points = item[0]
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _ocr_candidates(items: list[list[Any]], width: int, height: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    usable = [item for item in items if len(item) > 2 and str(item[1]).strip()]
    for item in usable:
        left, top, right, bottom = _box(item)
        candidates.append({
            "text": str(item[1]).strip(), "confidence": float(item[2]),
            "box": (left, top, right, bottom),
        })
    for first in usable:
        first_box = _box(first)
        first_center = (first_box[0] + first_box[2]) / 2
        for second in usable:
            second_box = _box(second)
            if second_box[1] <= first_box[1]:
                continue
            second_center = (second_box[0] + second_box[2]) / 2
            if second_box[1] - first_box[3] > height * 0.035:
                continue
            if abs(first_center - second_center) > width * 0.055:
                continue
            candidates.append({
                "text": f"{first[1]} {second[1]}",
                "confidence": (float(first[2]) + float(second[2])) / 2,
                "box": (
                    min(first_box[0], second_box[0]), min(first_box[1], second_box[1]),
                    max(first_box[2], second_box[2]), max(first_box[3], second_box[3]),
                ),
            })
    return candidates


def _best_match(
    candidates: list[dict[str, Any]], expected: str, predicate: Callable[[dict[str, Any]], bool]
) -> tuple[dict[str, Any] | None, float]:
    ranked = [(_similarity(candidate["text"], expected), candidate) for candidate in candidates if predicate(candidate)]
    if not ranked:
        return None, 0.0
    similarity, candidate = max(ranked, key=lambda item: (item[0] * item[1]["confidence"], item[0]))
    return candidate, similarity * candidate["confidence"]


def _read_with_rapidocr(image_bytes: bytes) -> list[list[Any]]:
    global _OCR_ENGINE
    from rapidocr_onnxruntime import RapidOCR

    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            _OCR_ENGINE = RapidOCR()
        result, _elapsed = _OCR_ENGINE(image_bytes)
    return list(result or [])


def _cyan_progress_pixels(image: Image.Image, box: tuple[float, float, float, float]) -> int:
    width, height = image.size
    left, top, right, _bottom = box
    sample_left = max(0, int(left - width * 0.012))
    sample_right = min(width, int(right + width * 0.012))
    sample_top = max(0, int(top - height * 0.06))
    sample_bottom = max(sample_top + 1, int(top - height * 0.006))
    pixels = image.crop((sample_left, sample_top, sample_right, sample_bottom)).convert("RGB").getdata()
    return sum(
        1 for red, green, blue in pixels
        if green >= 155 and blue >= 120 and green >= red * 1.18 and blue >= red * 1.05
    )


def verify_reputation_screenshot(
    image_bytes: bytes,
    requested_giver: str,
    requested_level: str,
    *,
    ocr_items: list[list[Any]] | None = None,
) -> ReputationVerification:
    """Approve only an exact giver/current-tier match; ambiguous images require manual review."""
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return ReputationVerification(False, None, None, 0.0, "The uploaded file could not be read as an image.")
    width, height = image.size
    if width < 1000 or height < 600:
        return ReputationVerification(False, None, None, 0.0, "The screenshot is too small for reliable verification.")
    if width > 8000 or height > 8000:
        return ReputationVerification(False, None, None, 0.0, "The screenshot dimensions are too large for automatic verification.")
    if requested_giver not in REPUTATION_LADDERS or requested_level not in REPUTATION_LADDERS[requested_giver]:
        return ReputationVerification(False, None, None, 0.0, "The submitted giver or level is not supported.")

    if ocr_items is None:
        try:
            ocr_items = _read_with_rapidocr(image_bytes)
        except Exception:
            return ReputationVerification(False, None, None, 0.0, "Automatic image reading is unavailable.")
    items = list(ocr_items or [])
    candidates = _ocr_candidates(items, width, height)
    giver_match, giver_score = _best_match(
        candidates,
        requested_giver,
        lambda candidate: (
            candidate["box"][0] >= width * 0.32
            and candidate["box"][1] <= height * 0.35
            and candidate["confidence"] >= 0.68
        ),
    )
    if giver_match is None or giver_score < 0.72:
        return ReputationVerification(False, None, None, giver_score, "The selected reputation giver was not clear in the screenshot header.")

    ladder = REPUTATION_LADDERS[requested_giver]
    matches: dict[str, tuple[dict[str, Any], float]] = {}
    for level in ladder:
        match, score = _best_match(
            candidates,
            level,
            lambda candidate: (
                candidate["box"][0] >= width * 0.32
                and height * 0.30 <= candidate["box"][1] <= height * 0.88
                and candidate["confidence"] >= 0.58
            ),
        )
        if match is not None and score >= 0.64:
            matches[level] = (match, score)

    requested_index = ladder.index(requested_level)
    requested_match = matches.get(requested_level)
    if requested_match is None:
        return ReputationVerification(False, requested_giver, None, giver_score, "The requested reputation level was not readable.")
    if _cyan_progress_pixels(image, requested_match[0]["box"]) < 12:
        return ReputationVerification(False, requested_giver, None, min(giver_score, requested_match[1]), "The requested level does not show achieved progress.")

    next_level = ladder[requested_index + 1] if requested_index + 1 < len(ladder) else None
    if next_level is not None:
        next_match = matches.get(next_level)
        if next_match is None:
            return ReputationVerification(False, requested_giver, requested_level, min(giver_score, requested_match[1]), "The next ladder level was not visible enough to confirm the current highest level.")
        if _cyan_progress_pixels(image, next_match[0]["box"]) >= 12:
            return ReputationVerification(False, requested_giver, next_level, min(giver_score, next_match[1]), "A higher achieved level is visible than the level requested.")

    for higher_level in ladder[requested_index + 2:]:
        higher_match = matches.get(higher_level)
        if higher_match is not None and _cyan_progress_pixels(image, higher_match[0]["box"]) >= 12:
            return ReputationVerification(False, requested_giver, higher_level, min(giver_score, higher_match[1]), "A higher achieved level is visible than the level requested.")

    confidence = min(giver_score, requested_match[1], matches[next_level][1] if next_level else 1.0)
    return ReputationVerification(
        True, requested_giver, requested_level, confidence,
        "The screenshot giver and highest achieved reputation level match the application.",
    )
