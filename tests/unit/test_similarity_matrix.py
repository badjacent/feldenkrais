import math

import numpy as np

from kb.similarity.matrix import SimilarityMatrix, build_similarity_matrix
from kb.similarity.measures import EmbeddingECDF, LessonProfile


ECDF = EmbeddingECDF((0.0, 0.5, 1.0), "ecdf-v1")


def profile(document_id, lesson_key=None, region="ribs"):
    return LessonProfile(
        document_id=document_id,
        lesson_key=lesson_key or f"lesson:{document_id}",
        regions={region: 1.0},
        embedding=[1.0, 0.0],
    )


def build(profiles, hashes, previous=None):
    return build_similarity_matrix(
        profiles,
        content_hashes=hashes,
        embedding_ecdf=ECDF,
        schema_version="profile-v1",
        extraction_model="fixture",
        weight_set="weights-v1",
        previous=previous,
    )


def test_matrix_keeps_diagonal_and_renditions_out_of_scoring():
    profiles = [profile("a", "same"), profile("b", "same"), profile("c")]
    matrix, recomputed = build(profiles, {item.document_id: item.document_id for item in profiles})

    assert matrix.values.dtype == np.float16
    assert all(math.isnan(float(matrix.values[index, index])) for index in range(3))
    assert math.isnan(float(matrix.values[0, 1]))
    assert not math.isnan(float(matrix.values[0, 2]))
    assert recomputed == 3


def test_incremental_matrix_only_recomputes_pairs_touching_changed_profiles():
    profiles = [profile("a"), profile("b"), profile("c")]
    original, _ = build(profiles, {"a": "1", "b": "1", "c": "1"})
    updated, recomputed = build(
        profiles,
        {"a": "1", "b": "2", "c": "1"},
        previous=original,
    )

    assert recomputed == 2
    assert updated.values[0, 2] == original.values[0, 2]


def test_matrix_round_trip_includes_version_manifest(tmp_path):
    profiles = [profile("a"), profile("b")]
    matrix, _ = build(profiles, {"a": "1", "b": "1"})

    matrix.save(tmp_path)
    loaded = SimilarityMatrix.load(tmp_path)

    assert loaded.document_ids == ("a", "b")
    assert loaded.values.dtype == np.float16
    assert loaded.ecdf_version == "ecdf-v1"
