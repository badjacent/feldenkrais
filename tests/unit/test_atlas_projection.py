from ops.export_atlas_projection import (
    build_summary,
    normalize_resource_url,
    normalize_values,
    public_lesson_key,
)


def test_normalize_resource_url_handles_aliases():
    assert normalize_resource_url("https://www.example.com/lesson/") == "example.com/lesson"
    assert normalize_resource_url("https://youtu.be/abc123") == "youtube:abc123"
    assert normalize_resource_url("https://www.youtube.com/watch?v=abc123") == "youtube:abc123"


def test_public_projection_helpers_are_safe_and_deterministic():
    summary = build_summary(
        {
            "body_regions": ["pelvis", "spine"],
            "movement_themes": ["rolling"],
            "positions": ["side-lying"],
        }
    )
    assert summary == (
        "A Feldenkrais lesson with attention to pelvis and spine, "
        "exploring rolling, often in side-lying."
    )
    assert public_lesson_key("private-id") == public_lesson_key("private-id")
    assert "private-id" not in public_lesson_key("private-id")
    assert normalize_values(
        "positions", {"supine with knees standing", "side_lying_right"}
    ) == ["side-lying", "lying"]
