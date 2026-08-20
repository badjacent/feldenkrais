from unittest.mock import patch

from kb.similarity.profiles import profile_content_hash, project_existing_analysis


def test_replace_profiles_is_version_scoped_and_json_safe(mock_db):
    projected = project_existing_analysis(
        document_id="doc",
        lesson_key="lesson:key",
        somatic={"steps": [{"differentiation": "ribs and shoulder"}]},
    )
    hashes = {"doc": profile_content_hash(projected)}

    with patch("kb.db.similarity_profiles.execute_values") as execute_values:
        mock_db.replace_similarity_profiles([projected], weight_set="weights-v1", content_hashes=hashes)

    delete_sql, delete_params = mock_db._mock_cursor.execute.call_args_list[0].args
    assert "DELETE FROM lesson_profile" in delete_sql
    assert delete_params == ("profile-v1", "pass0b-existing", "weights-v1")
    payload = execute_values.call_args.args[2][0][6]
    assert '"diffs": [' in payload


def test_empty_profile_batch_does_not_delete_existing_profiles(mock_db):
    mock_db.replace_similarity_profiles([], weight_set="weights-v1", content_hashes={})
    mock_db._mock_cursor.execute.assert_not_called()


def test_read_profiles_joins_embedding_without_storing_it_in_json(mock_db):
    projected = project_existing_analysis(document_id="doc", lesson_key="lesson:key")
    from kb.similarity.profiles import profile_to_payload

    mock_db._mock_cursor.fetchall.return_value = [
        {
            "profile": profile_to_payload(projected),
            "content_hash": "abc",
            "embedding": "[0.1,0.2]",
        }
    ]

    rows = mock_db.get_similarity_profiles(
        schema_version="profile-v1",
        extraction_model="pass0b-existing",
        weight_set="weights-v1",
    )

    assert rows[0]["embedding"] == [0.1, 0.2]
    assert "embedding" not in rows[0]["payload"]["profile"]
