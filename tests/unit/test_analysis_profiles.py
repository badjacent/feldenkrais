"""Analysis profiles: cache keys, per-profile reads, the v3 schema contract, and clients."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kb.db.analysis_profiles import profile_cache_suffix, profile_provider_key

GEMINI = {"key": "gemini-3-flash", "provider": "gemini", "model": "gemini-3-flash-preview",
          "params": {}, "cache_tag": None, "contract_version": 2}
ASTRA_LOW = {"key": "astra-low", "provider": "codex", "model": "gpt-6-astra",
             "params": {"reasoning_effort": "low"}, "cache_tag": "codex-astra", "contract_version": 2}
OPUS = {"key": "opus-5.5", "provider": "anthropic", "model": "claude-opus-5-5",
        "params": {"effort": "medium", "max_tokens": 64000}, "cache_tag": "opus-5.5", "contract_version": 3}


def test_historical_profile_keeps_bare_cache_keys():
    assert profile_provider_key(GEMINI) == "gemini"
    assert profile_cache_suffix(GEMINI) == ""


def test_astra_low_keeps_the_keys_it_was_written_under():
    assert profile_provider_key(ASTRA_LOW) == "codex-astra"
    assert profile_cache_suffix(ASTRA_LOW) == ":codex-astra"


def test_contract_three_profiles_carry_the_contract_in_their_keys():
    assert profile_provider_key(OPUS) == "c3:opus-5.5"
    assert profile_cache_suffix(OPUS) == ":c3:opus-5.5"


def test_analysis_modules_build_the_same_key_as_the_profile_reader():
    """The analysis package appends ':' + provider_key; readers must agree."""
    from kb import cache_helpers

    for profile in (GEMINI, ASTRA_LOW, OPUS):
        provider_key = profile_provider_key(profile)
        suffix = ":somatic_v2" + ("" if provider_key == "gemini" else f":{provider_key}")
        written = cache_helpers.get_cache_key("lesson" + suffix)
        assert cache_helpers.profile_result_key("lesson", ":somatic_v2", profile) == written


def test_profile_reader_never_falls_back_to_another_profile():
    from kb import cache_helpers

    gemini_key = cache_helpers.get_cache_key("lesson:decompose_v3")
    cache = {gemini_key: {"steps": [1]}}
    with patch.object(cache_helpers, "load_cached_analysis", side_effect=cache.get):
        key, result = cache_helpers.load_profile_result("lesson", ":decompose_v3", ASTRA_LOW)
        _, gemini = cache_helpers.load_profile_result("lesson", ":decompose_v3", GEMINI)

    assert key == cache_helpers.get_cache_key("lesson:decompose_v3:codex-astra")
    assert result is None
    assert gemini == {"steps": [1]}


def test_strict_contract_requires_everything_and_makes_optional_fields_nullable():
    from kb.llm import strict_contract_schema

    schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "integer"},
                        "ground_push": {"type": "string"},
                        "kind": {"type": "string", "enum": ["move", "rest"]},
                        "ref": {"$ref": "#/$defs/thing"},
                    },
                    "required": ["step_number"],
                },
            },
        },
        "required": ["steps"],
    }
    strict = strict_contract_schema(schema)
    item = strict["properties"]["steps"]["items"]

    assert strict["additionalProperties"] is False
    assert item["additionalProperties"] is False
    assert item["required"] == ["step_number", "ground_push", "kind", "ref"]
    assert item["properties"]["step_number"] == {"type": "integer"}  # required keeps its type
    assert item["properties"]["ground_push"]["type"] == ["string", "null"]
    assert item["properties"]["kind"]["enum"] == ["move", "rest", None]
    assert item["properties"]["ref"] == {"anyOf": [{"$ref": "#/$defs/thing"}, {"type": "null"}]}
    # The input schema is not modified.
    assert "additionalProperties" not in schema


def test_contract_three_client_sends_the_strict_schema_and_the_null_instruction():
    from kb.llm import LLM, NULL_WHEN_NOT_APPLICABLE

    llm = LLM(provider="gemini", model="gemini-3.1-pro-preview", api_key="k", schema_contract=3)
    schema = {"type": "object", "properties": {"note": {"type": "string"}}}
    with patch.object(LLM, "_structured_json_gemini", return_value={"note": None}) as call:
        llm.complete_structured_json("Analyze.", schema)

    prompt, sent_schema = call.call_args.args[0], call.call_args.args[1]
    assert prompt.endswith(NULL_WHEN_NOT_APPLICABLE)
    assert sent_schema["properties"]["note"]["type"] == ["string", "null"]


def test_contract_two_client_leaves_the_schema_alone():
    from kb.llm import LLM

    llm = LLM(provider="gemini", model="gemini-3-flash-preview", api_key="k")
    schema = {"type": "object", "properties": {"note": {"type": "string"}}}
    with patch.object(LLM, "_structured_json_gemini", return_value={}) as call:
        llm.complete_structured_json("Analyze.", schema)

    assert call.call_args.args[0] == "Analyze."
    assert call.call_args.args[1] is schema


def test_opus_uses_output_config_not_a_forced_tool():
    from kb.llm import LLM

    response = SimpleNamespace(
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text='{"note": null}')],
    )
    stream = MagicMock()
    stream.__enter__.return_value.get_final_message.return_value = response
    client = MagicMock()
    client.messages.stream.return_value = stream

    llm = LLM(provider="anthropic", model="claude-opus-5-5", api_key="k",
              reasoning_effort="medium", default_max_tokens=64000)
    llm._client = client
    schema = {"type": "object", "properties": {"note": {"type": ["string", "null"], "maxLength": 20}},
              "required": ["note"], "additionalProperties": False}
    result = llm.complete_structured_json("Analyze.", schema, system="Be exact.")

    kwargs = client.messages.stream.call_args.kwargs
    assert result == {"note": None}
    assert "tools" not in kwargs and "tool_choice" not in kwargs
    assert "temperature" not in kwargs
    assert kwargs["output_config"]["effort"] == "medium"
    sent = kwargs["output_config"]["format"]["schema"]
    assert "maxLength" not in sent["properties"]["note"]
    assert llm.last_usage == {"tokens_in": 10, "tokens_out": 5}


def test_get_llm_builds_new_profiles_from_the_profile_table():
    from kb import llm_config

    db = MagicMock()
    db.get_cache.return_value = {"enabled": True}
    db.get_profile_by_provider_key.return_value = OPUS
    with patch.object(llm_config, "_db", db), patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        llm = llm_config.get_llm("c3:opus-5.5")

    assert (llm.provider, llm.model) == ("anthropic", "claude-opus-5-5")
    assert llm.reasoning_effort == "medium"
    assert llm.default_max_tokens == 64000
    assert llm.schema_contract == 3


def test_get_llm_keeps_historical_configs_for_existing_keys():
    from kb import llm_config

    db = MagicMock()
    db.get_cache.return_value = {"enabled": True}
    with patch.object(llm_config, "_db", db), patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}):
        llm = llm_config.get_llm("gemini")

    assert llm.model == "gemini-3-flash-preview"
    assert llm.schema_contract == 2
    db.get_profile_by_provider_key.assert_not_called()


def test_lesson_opens_on_a_model_that_has_analyzed_it(mock_db):
    mock_db._mock_cursor.fetchone.return_value = {"key": "astra-low"}
    assert mock_db.default_profile_for_document("doc-1") == "astra-low"
    sql = mock_db._mock_cursor.execute.call_args_list[0][0][0]
    assert "ORDER BY p.canonical DESC" in sql  # canonical wins when it has results


def test_lesson_without_progress_opens_on_the_canonical_model(mock_db):
    mock_db._mock_cursor.fetchone.side_effect = [None, {**GEMINI, "canonical": True}]
    assert mock_db.default_profile_for_document("doc-1") == "gemini-3-flash"


def test_every_lesson_page_has_the_model_selector_but_only_local_pages_compare():
    from kb.web_pages import build_lesson_page_html

    production = build_lesson_page_html("doc-1", default_profile="astra-low")
    local = build_lesson_page_html("doc-1", default_profile="astra-low", pipeline_visible=True)

    for page in (production, local):
        assert 'id="profile-select"' in page
        assert "|| \"astra-low\";" in page  # the server's choice is the default
    assert "Compare models" not in production
    assert "switchTab('pipeline')" not in production
    assert "Compare models" in local


def test_lesson_context_explains_itself_and_keeps_the_transcript():
    from kb.lesson_context import build_lesson_context

    doc = build_lesson_context(
        document={"id": "doc-1", "title": "Clarifying the Feet", "source_url": "https://example.org/l13"},
        source_name="San Francisco Evening Class Notes",
        lesson_url="https://aionyourside.net/lesson/doc-1?profile=astra-low",
        profile=ASTRA_LOW,
        transcript="Lie on your back.",
        whisper_segments=[{"id": 0, "start": 0.0, "end": 2.0, "text": " Lie on your back."}],
        pass_a={"groups": [{"group_number": 1, "label": "Feet"}],
                "steps": [{"step_number": 1, "group_number": 1, "instruction": "Roll the right foot",
                           "tags": ["S", "Q"]}]},
        somatic={"enriched_steps": [{"step_number": 1, "ground_push": "right heel",
                                     "inhibition": "None", "differentiation": None}]},
        language=None, condensed=None, concepts=None, positions=None,
        field_descriptions={"ground_push": "Where the body presses into the floor. Write 'None' if not applicable."},
        exported_at="2026-09-23T12:00Z",
    )
    assert "Everything else is machine analysis" in doc
    assert "gpt-6-astra" in doc
    assert "*ground push* — Where the body presses into the floor" in doc  # schema definition, no model instructions
    assert "Write 'None'" not in doc
    assert "- ground push: right heel" in doc
    assert "inhibition" not in doc.split("## Groups and steps")[1].split("## Transcript")[0]  # placeholder dropped
    assert "[0:00] Lie on your back." in doc
    assert "Reference: lesson doc-1 · profile astra-low" in doc
    assert "**Step 1**" in doc


def test_lesson_context_for_a_text_source_does_not_claim_a_recording():
    from kb.lesson_context import build_lesson_context

    doc = build_lesson_context(
        document={"id": "doc-2", "title": "Lesson 13"}, source_name="Evening Class Notes",
        lesson_url="u", profile=ASTRA_LOW, transcript="Lie on your stomach.", whisper_segments=None,
        pass_a={"groups": [{"group_number": 1, "label": "Heel", "steps": [1]}],
                "steps": [{"step_number": 1, "instruction": "Bend the right knee"}]},
        somatic=None, language=None, condensed=None, concepts=None, positions=None,
        field_descriptions={},
    )
    assert "speech-to-text" not in doc and "timestamps" not in doc
    assert "## Lesson text" in doc
    assert "**Step 1**: Bend the right knee" in doc  # steps listed under their group
