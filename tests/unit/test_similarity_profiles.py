from kb.similarity.measures import TrademarkMechanism
from kb.similarity.profiles import (
    ORDINAL_WEIGHTS,
    profile_content_hash,
    profile_to_payload,
    project_existing_analysis,
)


def test_projection_uses_fixed_ordinals_not_free_form_strengths():
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        concepts={
            "concepts": [
                {"category": "body_region", "value": "ribs"},
                {"category": "movement_pattern", "value": "rolling"},
            ]
        },
        somatic={
            "enriched_steps": [
                {"clarified": "Roll the ribs and pelvis", "relevance": True},
                {"clarified": "Roll the ribs again", "relevance": True},
                {"clarified": "Touch the feet", "relevance": True},
            ]
        },
    )

    assert set(result.profile.regions.values()) <= set(ORDINAL_WEIGHTS.values())
    assert result.profile.regions["ribs_chest"] == 1.0
    assert result.profile.regions["feet"] == 0.3
    assert result.profile.themes["rolling"] == 1.0


def test_positions_use_step_durations_when_available():
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        spatial={
            "steps": [
                {"position": "lying on the back", "duration_seconds": 30},
                {"position": "standing upright", "duration_seconds": 10},
            ]
        },
    )

    assert result.profile.positions == {"supine": 0.75, "standing": 0.25}
    assert result.provenance["positions"] == "spatial_step_duration"


def test_positions_fail_to_fixed_tag_fallback_without_duration():
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        decompose={"steps": [{"starting_position": "sitting"}]},
        concepts={"concepts": [{"category": "position", "value": "supine"}]},
    )

    assert result.profile.positions == {"supine": 1.0, "sitting": 1.0}
    assert result.provenance["positions"] == "position_tags_fallback"


def test_routes_differentiations_and_sensory_are_projected_from_pass0b():
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        somatic={
            "steps": [
                {
                    "transmission_route": "hand → arm → ribs → pelvis",
                    "differentiation": "shoulder from ribs",
                    "sensory_inquiry": "Feel the contact with the floor and your breath",
                    "relevance": True,
                    "ground_push": "right hand into floor",
                }
            ]
        },
        language={"steps": [{"attention_cues": "notice your eyes", "image_language": "None"}]},
    )

    assert result.profile.prox_dist == {"distal_to_proximal": 1.0}
    assert result.profile.diffs == {("ribs_chest", "shoulders_scapulae"): 1.0}
    assert set(result.profile.sensory) >= {"eyes", "breath", "contact_floor"}
    assert result.profile.scalars["support_level"] == 1.0


def test_new_extractions_remain_explicit_inputs_and_keep_mode():
    mark = TrademarkMechanism("bell_hand", 1.0, "taught")
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        marks=[mark],
        architecture={
            "type": "single_motif",
            "side_compare": True,
            "act_vs_relationship": False,
        },
    )

    assert result.profile.marks == (mark,)
    assert result.profile.architecture_type == "single_motif"
    assert result.provenance["marks"] == "dedicated_extraction"


def test_profile_payload_is_stable_and_json_safe():
    result = project_existing_analysis(
        document_id="lesson",
        lesson_key="lesson:key",
        somatic={"steps": [{"differentiation": "shoulder from ribs"}]},
    )

    payload = profile_to_payload(result)
    assert payload["profile"]["diffs"] == [
        {"parts": ["ribs_chest", "shoulders_scapulae"], "weight": 1.0}
    ]
    assert profile_content_hash(result) == profile_content_hash(result)
