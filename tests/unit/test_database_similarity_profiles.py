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
