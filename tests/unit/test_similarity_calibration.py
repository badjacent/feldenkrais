from kb.similarity.calibration import CalibrationArtifact, build_embedding_calibration
from kb.similarity.measures import LessonProfile


def embedded(document_id, lesson_key, vector):
    return LessonProfile(document_id, lesson_key, embedding=vector)


def test_calibration_excludes_rendition_pairs_and_is_deterministic():
    profiles = [
        embedded("a", "same", [1.0, 0.0]),
        embedded("b", "same", [1.0, 0.0]),
        embedded("c", "other", [0.0, 1.0]),
    ]

    first = build_embedding_calibration(
        profiles,
        schema_version="profile-v1",
        extraction_model="pass0b-v4",
        sample_size=10_000,
    )
    second = build_embedding_calibration(
        reversed(profiles),
        schema_version="profile-v1",
        extraction_model="pass0b-v4",
        sample_size=10_000,
    )

    assert first == second
    assert first.sampled_pairs == 2
    assert first.cosine_values == (0.0, 0.0)


def test_calibration_artifact_round_trips_and_supplies_ecdf(tmp_path):
    artifact = CalibrationArtifact(
        "profile-v1", "pass0b-v4", "default", 17, 3, (0.1, 0.5, 0.9)
    )
    path = tmp_path / "embedding-ecdf.json"

    artifact.write(path)
    loaded = CalibrationArtifact.read(path)

    assert loaded == artifact
    assert loaded.ecdf().normalize(0.5) == 2 / 3
    assert loaded.version != CalibrationArtifact(
        "profile-v1", "pass0b-v4", "default", 17, 3, (0.1, 0.5, 0.8)
    ).version
