import pytest

from kb.analysis.print_prep import PrintPrepError, _complete_structure, _format_input, build_print_model


def _pass_a():
    return {
        "groups": [
            {"group_number": 1, "label": "Scan", "steps": [1]},
            {"group_number": 2, "label": "Rolling", "steps": [2, 3]},
        ],
        "steps": [
            {"step_number": 1, "instruction": "Lie on the back.", "is_home": True, "segments": [1, 2]},
            {"step_number": 2, "instruction": "Roll the head.", "segments": [3]},
            {"step_number": 3, "instruction": "Rest.", "segments": [4]},
        ],
        "pass_0": {"segments": [
            {"id": 1, "text": "Please lie on your back."},
            {"id": 2, "text": "Notice how the floor supports you."},
            {"id": 3, "text": "Slowly roll the head to the right, not the face toward the floor."},
            {"id": 4, "text": "Rest."},
        ]},
    }


def _answer(**overrides):
    steps = [
        {"step_number": 1, "position": "On back", "brief_summary": "Lie down and sense the floor.",
         "notable_quotes": [{"segment_id": 2, "text": "how the floor supports you"}]},
        {"step_number": 2, "position": "On back", "brief_summary": "Roll the head right.",
         "notable_quotes": [{"segment_id": 3, "text": "not the face toward the floor"}]},
        {"step_number": 3, "position": "On back", "brief_summary": "Rest.", "notable_quotes": []},
    ]
    return {"steps": overrides.get("steps", steps), "editorial_notes": overrides.get("notes", [])}


def test_quotes_are_stored_as_offsets_into_their_own_segment():
    model = build_print_model(_pass_a(), _answer())

    quote = model["steps"][1]["notable_quotes"][0]
    text = _pass_a()["pass_0"]["segments"][2]["text"]
    assert quote["segment_id"] == 3
    assert text[quote["start_offset"]:quote["end_offset"]] == "not the face toward the floor"
    assert model["steps"][0]["segment_ids"] == [1, 2]
    assert model["steps"][1]["group_number"] == 2
    assert model["groups"][1] == {"group_number": 2, "label": "Rolling", "step_numbers": [2, 3]}
    assert model["validation"] == {"rejected_quotes": [], "coverage_errors": []}


def test_a_quote_that_is_not_verbatim_is_dropped_and_reported():
    answer = _answer()
    answer["steps"][1]["notable_quotes"] = [{"segment_id": 3, "text": "roll your head to the right"}]

    model = build_print_model(_pass_a(), answer)

    assert model["steps"][1]["notable_quotes"] == []
    assert model["validation"]["rejected_quotes"][0]["reason"] == "not an exact excerpt"


def test_a_quote_from_another_steps_segment_is_dropped():
    answer = _answer()
    answer["steps"][1]["notable_quotes"] = [{"segment_id": 1, "text": "Please lie on your back."}]

    model = build_print_model(_pass_a(), answer)

    assert model["steps"][1]["notable_quotes"] == []
    assert model["validation"]["rejected_quotes"][0]["reason"] == "segment not in this step"


def test_overlapping_and_excess_quotes_are_dropped():
    answer = _answer()
    answer["steps"][1]["notable_quotes"] = [
        {"segment_id": 3, "text": "the face toward"},
        {"segment_id": 3, "text": "face toward the floor"},  # overlaps the first
    ] + [{"segment_id": 3, "text": w} for w in ("Slowly", "roll", "head", "right", "not", "floor", "to")]

    model = build_print_model(_pass_a(), answer)

    assert len(model["steps"][1]["notable_quotes"]) == 7
    reasons = [r["reason"] for r in model["validation"]["rejected_quotes"]]
    assert "repeats or overlaps another excerpt" in reasons
    assert "more than 7 excerpts" in reasons


def test_steps_that_do_not_match_the_decomposition_are_rejected():
    missing = _answer()
    missing["steps"] = missing["steps"][:2]
    with pytest.raises(PrintPrepError, match="missing \\[3\\]"):
        build_print_model(_pass_a(), missing)

    repeated = _answer()
    repeated["steps"][2]["step_number"] = 2
    with pytest.raises(PrintPrepError):
        build_print_model(_pass_a(), repeated)


def test_segment_gaps_are_reported_as_coverage_errors():
    pass_a = _pass_a()
    pass_a["steps"][2]["segments"] = []

    model = build_print_model(pass_a, _answer())

    assert model["validation"]["coverage_errors"] == ["1 segments belong to no step (first: [4])"]


def test_input_lists_every_segment_and_the_spatial_position():
    text = _format_input(_pass_a(), {"steps": [{"step_number": 2, "position": "Lying on back",
                                                 "action": "Roll the head"}]})

    assert "[1] Please lie on your back." in text
    assert "[2] Notice how the floor supports you." in text
    assert "Position: Lying on back" in text
    assert "Position: (not described)" in text
    assert "### Step 1 [rest/home]" in text


def _lesson(steps, groups):
    segments = [{"id": n, "text": f"text {n}"} for n in range(1, 10)]
    return {"pass_0": {"segments": segments}, "steps": steps, "groups": groups}


def test_a_step_in_no_group_reaches_the_model_and_the_print_model():
    pass_a = _lesson(
        [{"step_number": 1, "instruction": "Opening", "segments": [1]},
         {"step_number": 2, "instruction": "Two", "segments": [2]},
         {"step_number": 3, "instruction": "Stranded", "segments": [3]},
         {"step_number": 4, "instruction": "Four", "segments": [4]}],
        [{"group_number": 1, "label": "A", "steps": [2]},
         {"group_number": 2, "label": "B", "steps": [4]}],
    )
    text = _format_input(pass_a, {"steps": []})
    assert text.index("### Step 1") < text.index("### Step 2") < text.index("### Step 3") < text.index("### Step 4")

    answer = {"steps": [{"step_number": n, "position": "", "brief_summary": "x", "notable_quotes": []}
                        for n in (1, 2, 3, 4)]}
    model = build_print_model(pass_a, answer)
    assert [g["step_numbers"] for g in model["groups"]] == [[1, 2, 3], [4]]
    assert {s["step_number"]: s["group_number"] for s in model["steps"]} == {1: 1, 2: 1, 3: 1, 4: 2}


def test_two_steps_sharing_a_number_are_both_kept():
    pass_a = _lesson(
        [{"step_number": 1, "instruction": "One", "segments": [1]},
         {"step_number": 2, "instruction": "First two", "segments": [2]},
         {"step_number": 3, "instruction": "Three", "segments": [3]},
         {"step_number": 2, "instruction": "Second two", "segments": [4]}],
        [{"group_number": 1, "label": "A", "steps": [1, 2]},
         {"group_number": 2, "label": "B", "steps": [3]}],
    )
    text = _format_input(pass_a, {"steps": [{"step_number": 2, "position": "Which one?"}]})
    assert "### Step 4" in text and "Second two" in text and "Which one?" not in text

    answer = {"steps": [{"step_number": n, "position": "", "brief_summary": "x", "notable_quotes": []}
                        for n in (1, 2, 3, 4)]}
    model = build_print_model(pass_a, answer)
    assert model["groups"][1]["step_numbers"] == [3, 4]
    assert {s["step_number"]: s["segment_ids"] for s in model["steps"]}[4] == [4]


def test_a_complete_structure_is_left_alone():
    pass_a = _lesson(
        [{"step_number": 1, "segments": [1]}, {"step_number": 2, "segments": [2]}],
        [{"group_number": 1, "label": "A", "steps": [1, 2]}],
    )
    assert _complete_structure(pass_a) == pass_a
    assert _complete_structure(_complete_structure(pass_a)) == pass_a


def test_a_misnumbered_step_takes_the_gap_its_group_still_lists():
    pass_a = _lesson(
        [{"step_number": 1, "instruction": "One", "segments": [1]},
         {"step_number": 2, "instruction": "Two", "segments": [2]},
         {"step_number": 1, "instruction": "Really three", "segments": [3]},
         {"step_number": 4, "instruction": "Four", "segments": [4]}],
        [{"group_number": 1, "label": "A", "steps": [1, 2]},
         {"group_number": 2, "label": "B", "steps": [3, 4]}],
    )
    fixed = _complete_structure(pass_a)
    assert [s["step_number"] for s in fixed["steps"]] == [1, 2, 3, 4]
    assert [g["steps"] for g in fixed["groups"]] == [[1, 2], [3, 4]]
