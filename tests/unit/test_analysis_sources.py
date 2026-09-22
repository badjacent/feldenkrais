from unittest.mock import MagicMock

import pytest

from kb.ingest.analysis_sources import (
    analysis_source_name,
    ensure_document_analysis_source,
    get_or_create_analysis_source,
)


@pytest.mark.parametrize(
    ("base", "provider", "expected"),
    [
        ("Anat Baniel TML", None, "Anat Baniel TML"),
        ("Anat Baniel TML", "gemini", "Anat Baniel TML"),
        ("Anat Baniel TML", "codex-astra", "Anat Baniel TML-Astra"),
        ("Anat Baniel TML-Astra", "codex-astra", "Anat Baniel TML-Astra"),
        ("Anat Baniel TML-Astra", "gemini", "Anat Baniel TML"),
    ],
)
def test_analysis_source_name(base, provider, expected):
    assert analysis_source_name(base, provider) == expected


def test_analysis_source_name_rejects_blank():
    with pytest.raises(ValueError, match="must not be empty"):
        analysis_source_name("  ", "gemini")


def test_get_or_create_astra_source_keeps_document_naming_out_of_scope():
    db = MagicMock()
    cursor = db.get_cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"id": 42}

    source_id = get_or_create_analysis_source(
        db,
        "Anat Baniel Transformational Movement Lessons",
        "codex-astra",
    )

    assert source_id == 42
    params = cursor.execute.call_args.args[1]
    assert params == (
        "Anat Baniel Transformational Movement Lessons-Astra",
        "Anat Baniel Transformational Movement Lessons-Astra",
        True,
        False,
    )


def test_ensure_document_astra_source_clones_source_and_moves_document():
    db = MagicMock()
    cursor = db.get_cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        {
            "id": 7,
            "name": "Anat Baniel TML",
            "category_name": "Anat Baniel TML",
            "visible": True,
            "public": False,
        },
        {"id": 42},
    ]

    source_id = ensure_document_analysis_source(db, "doc-1", "codex-astra")

    assert source_id == 42
    assert cursor.execute.call_args_list[-1].args[1] == (42, "doc-1")


def test_ensure_document_source_is_noop_when_already_correct():
    db = MagicMock()
    cursor = db.get_cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {
        "id": 42,
        "name": "Anat Baniel TML-Astra",
        "category_name": "Anat Baniel TML-Astra",
        "visible": True,
        "public": False,
    }

    assert ensure_document_analysis_source(db, "doc-1", "codex-astra") == 42
    assert cursor.execute.call_count == 1
