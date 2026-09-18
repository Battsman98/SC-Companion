import io

from PIL import Image, ImageDraw

from src.reputation_verifier import verify_reputation_screenshot


def _ocr_item(text: str, left: int, top: int, right: int, bottom: int, confidence: float = 0.95):
    return [[[left, top], [right, top], [right, bottom], [left, bottom]], text, confidence]


def _intersec_screenshot(*, sr_progress: bool = True) -> tuple[bytes, list[list]]:
    image = Image.new("RGB", (2560, 1440), "#101820")
    draw = ImageDraw.Draw(image)
    items = [
        _ocr_item("INTERSEC DEFENSE SOLUTIONS", 950, 260, 1740, 302),
        _ocr_item("CONTRACTOR", 1615, 660, 1755, 680),
        _ocr_item("SR", 1930, 660, 1965, 680),
        _ocr_item("CONTRACTOR", 1930, 680, 2075, 700),
        _ocr_item("VETERAN", 975, 985, 1080, 1008),
        _ocr_item("CONTRACTOR", 975, 1008, 1120, 1030),
        _ocr_item("HEAD", 1300, 985, 1365, 1008),
        _ocr_item("CONTRACTOR", 1300, 1008, 1450, 1030),
    ]
    draw.rectangle((1610, 620, 1755, 628), fill="#72f5d2")
    if sr_progress:
        draw.rectangle((1925, 620, 1955, 628), fill="#72f5d2")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue(), items


def test_current_partially_filled_tier_is_automatically_verified() -> None:
    image, items = _intersec_screenshot()
    result = verify_reputation_screenshot(
        image, "InterSec Defense Solutions", "Sr. Contractor", ocr_items=items
    )
    assert result.verified is True
    assert result.detected_level == "Sr. Contractor"


def test_submission_below_highest_visible_tier_requires_manual_review() -> None:
    image, items = _intersec_screenshot()
    result = verify_reputation_screenshot(
        image, "InterSec Defense Solutions", "Contractor", ocr_items=items
    )
    assert result.verified is False
    assert result.detected_level == "Sr. Contractor"
    assert "higher achieved level" in result.reason


def test_mismatched_giver_requires_manual_review() -> None:
    image, items = _intersec_screenshot()
    result = verify_reputation_screenshot(image, "Covalex", "Master", ocr_items=items)
    assert result.verified is False
    assert result.detected_giver is None

