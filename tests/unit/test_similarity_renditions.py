from kb.similarity.renditions import (
    RAW_COSINE_THRESHOLD,
    SHINGLE_THRESHOLD,
    RenditionDocument,
    build_rendition_groups,
    canonical_lesson_identity,
    transcript_shingles,
)


def lesson(document_id, title, transcript, **metadata):
    return RenditionDocument(document_id, title, metadata, transcript)


def test_type_and_split_gates_fail_closed_before_rendition_scoring():
    result = build_rendition_groups(
        [
            lesson("atm", "ATM", "move gently " * 20, lesson_type="ATM"),
            lesson("talk", "Talk", "move gently " * 20, lesson_type="LECTURE"),
            lesson(
                "multi",
                "Two lessons",
                "move gently " * 20,
                lesson_type="ATM",
                lesson_count=2,
            ),
            lesson("unknown", "Unknown", "move gently " * 20),
        ]
    )

    assert set(result.lesson_keys) == {"atm"}
    assert result.excluded == {
        "multi": "multi_lesson_requires_split",
        "talk": "not_atm",
        "unknown": "not_atm",
    }


def test_shared_ay_identity_groups_different_teacher_renditions():
    first = lesson("a", "AY 23 Palate, Mouth and Teeth", "first teacher wording " * 20, lesson_type="ATM")
    second = lesson(
        "b",
        "A different title",
        "entirely different language " * 20,
        lesson_type="ATM",
        origin="Alexander Yanai lesson 023",
    )

    result = build_rendition_groups([first, second])

    assert canonical_lesson_identity(first) == "ay:23"
    assert result.same_rendition("a", "b")
    assert result.lesson_keys["a"] == "ay:23"
    assert result.renditions_for("a") == ("b",)
    assert result.evidence[0].reasons == ("canonical_identity",)


def test_ambiguous_multiple_ay_references_do_not_create_identity():
    document = lesson(
        "discussion",
        "AY 23 compared with AY 24",
        "movement wording " * 20,
        lesson_type="ATM",
    )
    assert canonical_lesson_identity(document) is None


def test_exact_shingle_threshold_is_strict():
    left = frozenset({"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m"})
    right = frozenset({"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "n", "o"})
    # Intersection 10, union 15: above 0.65.
    assert len(left & right) / len(left | right) > SHINGLE_THRESHOLD

    text = "one two three four five six seven eight nine ten eleven twelve"
    assert transcript_shingles(text)


def test_transcript_overlap_groups_duplicates_and_is_transitive():
    shared = "lie on your back bend your knees feel the floor and roll your pelvis slowly "
    a = lesson("a", "Circular Breathing A", shared * 12 + "first ending", lesson_type="ATM")
    b = lesson("b", "Circular Breathing B", shared * 12 + "second ending", lesson_type="ATM")
    c = lesson("c", "Circular Breathing C", shared * 12 + "third ending", lesson_type="ATM")

    result = build_rendition_groups([a, b, c])

    assert result.same_rendition("a", "b")
    assert result.same_rendition("b", "c")
    assert len(result.groups[result.lesson_keys["a"]]) == 3
    assert all("transcript_shingles" in item.reasons for item in result.evidence)


def test_raw_cosine_gate_is_strict_and_separate_from_summary_embeddings():
    base = [1.0, 0.0, 0.0]
    close = [1.0, 0.01, 0.0]
    far = [0.0, 1.0, 0.0]
    docs = [
        RenditionDocument("a", "A", {"lesson_type": "ATM"}, "alpha words " * 20, base),
        RenditionDocument("b", "B", {"lesson_type": "ATM"}, "beta words " * 20, close),
        RenditionDocument("c", "C", {"lesson_type": "ATM"}, "gamma words " * 20, far),
    ]

    result = build_rendition_groups(docs)

    assert RAW_COSINE_THRESHOLD == 0.985
    assert result.same_rendition("a", "b")
    assert not result.same_rendition("a", "c")
    assert result.evidence[0].reasons == ("raw_embedding",)


def test_existing_lesson_key_is_preserved_when_a_rendition_is_added():
    shared = "stand and shift weight through the feet while turning the pelvis " * 15
    docs = [
        lesson("old", "Old", shared + "old", lesson_type="ATM"),
        lesson("new", "New", shared + "new", lesson_type="ATM"),
    ]

    result = build_rendition_groups(docs, existing_lesson_keys={"old": "rendition:stable"})

    assert result.lesson_keys == {
        "new": "rendition:stable",
        "old": "rendition:stable",
    }


def test_circular_and_global_breathing_identities_do_not_merge():
    docs = [
        lesson(
            "circular-1",
            "Circular Breathing",
            "breathe around the ribs " * 20,
            lesson_type="ATM",
            canonical_lesson_identity="circular_breathing",
        ),
        lesson(
            "circular-2",
            "Circular Breathing rendition",
            "another teacher speaks here " * 20,
            lesson_type="ATM",
            canonical_lesson_identity="circular_breathing",
        ),
        lesson(
            "global-1",
            "Global Breathing",
            "breathe through the whole self " * 20,
            lesson_type="ATM",
            canonical_lesson_identity="global_breathing",
        ),
        lesson(
            "global-2",
            "Global Breathing rendition",
            "different global lesson words " * 20,
            lesson_type="ATM",
            canonical_lesson_identity="global_breathing",
        ),
    ]

    result = build_rendition_groups(docs)

    assert result.same_rendition("circular-1", "circular-2")
    assert result.same_rendition("global-1", "global-2")
    assert not result.same_rendition("circular-1", "global-1")
