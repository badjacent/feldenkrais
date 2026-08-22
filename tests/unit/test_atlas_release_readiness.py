import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "ops/mini/report_atlas_release_readiness.py"
SPEC = importlib.util.spec_from_file_location("atlas_release_readiness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_counts_reports_positive_delta_and_unmatched_projection():
    assert MODULE.release_counts(
        live_lessons=500,
        publishable_lessons=603,
        projection_lessons=639,
        excluded_unrelated=7,
    ) == {
        "live_lessons": 500,
        "publishable_lessons": 603,
        "lessons_added_if_released": 103,
        "projection_lessons": 639,
        "projection_not_publishable": 36,
        "excluded_unrelated": 7,
    }


def test_release_counts_never_reports_negative_additions():
    result = MODULE.release_counts(
        live_lessons=603,
        publishable_lessons=600,
        projection_lessons=639,
        excluded_unrelated=7,
    )
    assert result["lessons_added_if_released"] == 0
