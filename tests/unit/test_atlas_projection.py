from ops.export_atlas_projection import (
    build_summary,
    derive_transcript_facets,
    normalize_resource_url,
    normalize_values,
    public_lesson_key,
)
from ops.sync_atlas_projection import crawl_lock_path, projection_equivalent


def test_normalize_resource_url_handles_aliases():
    assert (
        normalize_resource_url("https://www.example.com/lesson/")
        == "example.com/lesson"
    )
    assert normalize_resource_url("https://youtu.be/abc123") == "youtube:abc123"
    assert (
        normalize_resource_url("https://www.youtube.com/watch?v=abc123")
        == "youtube:abc123"
    )


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


def test_transcript_facets_are_coarse_and_do_not_copy_source_text():
    transcript = (
        "Lie on your back and notice your breathing. Roll the pelvis and turn "
        "your head. After a pause, compare how the shoulders feel."
    )

    facets = derive_transcript_facets(transcript)

    assert facets["positions"] == ["lying"]
    assert facets["body_regions"] == ["shoulders", "pelvis", "head"]
    assert facets["movement_themes"] == ["breathing", "rolling", "rotation"]
    assert facets["teaching_strategies"] == [
        "comparison",
        "sensing",
        "rest and integration",
    ]
    assert "Lie on your back" not in str(facets)


def test_projection_equivalence_ignores_only_generation_time():
    first = b'{"generated_at":"one","entries":[{"lesson_key":"a"}]}'
    later = b'{"generated_at":"two","entries":[{"lesson_key":"a"}]}'
    changed = b'{"generated_at":"two","entries":[{"lesson_key":"b"}]}'
    assert projection_equivalent(first, later)
    assert not projection_equivalent(first, changed)


def test_projection_uses_the_atlas_crawl_publish_lock(tmp_path):
    assert crawl_lock_path(tmp_path) == tmp_path / "var/crawl-cycle.lock"
