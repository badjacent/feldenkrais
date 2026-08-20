import math

import pytest

from kb.similarity.measures import (
    EmbeddingECDF,
    LessonProfile,
    TrademarkMechanism,
    contrastive_lessons,
    diverse_lessons,
    duration_similarity,
    explain,
    language_similarity,
    ruzicka,
    score_pair,
    similar_lessons,
)

ECDF = EmbeddingECDF((0.0, 0.25, 0.5, 0.75, 1.0), "fixture-v1")


def profile(document_id, lesson_key=None, **values):
    return LessonProfile(document_id, lesson_key or f"lesson:{document_id}", **values)


def test_rendition_gate_runs_before_every_pair_measure():
    left = profile("a", "ay:23", regions={"ribs": 1.0}, embedding=[1.0, 0.0])
    rendition = profile("b", "ay:23", regions={"ribs": 1.0}, embedding=[1.0, 0.0])

    assert score_pair(left, rendition, embedding_ecdf=ECDF) is None
    assert similar_lessons(left, [rendition], embedding_ecdf=ECDF) == []


def test_ruzicka_exposes_shared_and_divergent_terms():
    score, shared, divergences = ruzicka(
        {"ribs": 0.8, "pelvis": 0.3},
        {"ribs": 0.9, "shoulders": 0.6},
    )

    assert score == pytest.approx(0.8 / 1.8)
    assert shared[0] == ("ribs", 0.8)
    assert divergences[0] == ("shoulders", 0.6)


def test_missing_structured_fields_are_dropped_and_renormalized():
    left = profile("a", regions={"ribs": 1.0})
    right = profile("b", regions={"ribs": 0.5})

    result = score_pair(left, right, embedding_ecdf=ECDF)

    assert result.structured == 0.5
    assert result.fields["regions"].weight == 1.0
    assert "themes" in result.missing_fields
    assert result.base == 0.5


def test_embedding_cosine_is_mandatorily_ecdf_calibrated():
    left = profile("a", embedding=[1.0, 0.0])
    right = profile("b", embedding=[0.5, math.sqrt(0.75)])

    result = score_pair(left, right, embedding_ecdf=ECDF)

    assert result.embedding == 0.6  # cosine .5 ranks at 3/5 in the fixture ECDF
    assert result.base == 0.6


@pytest.mark.parametrize(
    ("left_mode", "right_mode", "expected"),
    [("taught", "taught", 0.8), ("taught", "referenced", 0.24), ("referenced", "referenced", 0.0)],
)
def test_trademark_lift_respects_taught_vs_referenced_mode(left_mode, right_mode, expected):
    left = profile(
        "a",
        regions={"hands": 0.5},
        marks=(TrademarkMechanism("bell_hand", 0.8, left_mode),),
    )
    right = profile(
        "b",
        regions={"hands": 0.5},
        marks=(TrademarkMechanism("bell_hand", 0.9, right_mode),),
    )

    result = score_pair(left, right, embedding_ecdf=ECDF)

    assert result.trademark_strength == pytest.approx(expected)
    assert result.similarity == pytest.approx(result.base + 0.5 * expected * (1 - result.base))


def test_duration_uses_thirty_minute_exponential_scale():
    assert duration_similarity(1800, 3600) == pytest.approx(math.exp(-1))


def test_language_is_small_normalized_metadata_term_and_unknown_is_missing():
    assert language_similarity("en-US", "English") == 1.0
    assert language_similarity("en", "Spanish") == 0.0
    assert language_similarity("unknown", "en") is None

    same = score_pair(
        profile("a", regions={"ribs": 0.5}, language="en-US"),
        profile("b", regions={"ribs": 1.0}, language="English"),
        embedding_ecdf=ECDF,
    )
    different = score_pair(
        profile("a", regions={"ribs": 0.5}, language="en"),
        profile("c", regions={"ribs": 1.0}, language="es"),
        embedding_ecdf=ECDF,
    )
    missing = score_pair(
        profile("a", regions={"ribs": 0.5}, language="unknown"),
        profile("d", regions={"ribs": 1.0}, language="en"),
        embedding_ecdf=ECDF,
    )

    assert same.similarity > missing.similarity > different.similarity
    assert explain(same)["language"] == 1.0


def test_teacher_and_source_cannot_enter_the_profile_or_score():
    assert "teacher" not in LessonProfile.__dataclass_fields__
    assert "source" not in LessonProfile.__dataclass_fields__


def test_explanation_is_the_score_decomposition():
    result = score_pair(
        profile("a", regions={"ribs": 0.8}, themes={"rolling": 1.0}),
        profile("b", regions={"ribs": 0.9}, themes={"rotation": 1.0}),
        embedding_ecdf=ECDF,
    )

    payload = explain(result)

    assert payload["fields"]["regions"]["shared"] == [("ribs", 0.8)]
    assert payload["fields"]["themes"]["divergences"]


def test_circular_and_global_clusters_are_near_but_distinguishable_after_gate():
    common = {"breath": 1.0, "ribs": 0.7}
    circular_a = profile("ca", "circular", regions=common, themes={"rotation": 0.8})
    circular_b = profile("cb", "circular", regions=common, themes={"rotation": 0.8})
    global_a = profile("ga", "global", regions=common, themes={"whole_body": 0.8})

    assert score_pair(circular_a, circular_b, embedding_ecdf=ECDF) is None
    across = score_pair(circular_a, global_a, embedding_ecdf=ECDF)
    assert across is not None
    assert across.similarity < 0.9


def test_contrastive_operator_holds_one_field_and_varies_another():
    query = profile("q", regions={"pelvis": 1.0}, positions={"supine": 1.0})
    same_region_new_position = profile(
        "target", regions={"pelvis": 1.0}, positions={"standing": 1.0}
    )
    different_region_same_position = profile(
        "other", regions={"eyes": 1.0}, positions={"supine": 1.0}
    )

    results = contrastive_lessons(
        query,
        [different_region_same_position, same_region_new_position],
        hold_fields=["regions"],
        vary_fields=["positions"],
        embedding_ecdf=ECDF,
    )

    assert results[0][1].right_id == "target"


def test_diverse_operator_never_selects_two_renditions():
    query = profile("q", regions={"ribs": 1.0})
    first = profile("a", "family", regions={"ribs": 1.0})
    rendition = profile("b", "family", regions={"ribs": 1.0})
    alternative = profile("c", regions={"ribs": 0.7, "pelvis": 0.3})

    results = diverse_lessons(query, [first, rendition, alternative], embedding_ecdf=ECDF)

    assert len({result.right_id for result in results} & {"a", "b"}) == 1
