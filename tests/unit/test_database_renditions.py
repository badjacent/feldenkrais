from unittest.mock import patch

from kb.similarity.renditions import RenditionDocument, build_rendition_groups


def test_get_rendition_inputs_only_uses_explicit_raw_vectors(mock_db):
    mock_db._mock_cursor.fetchall.return_value = [
        {
            "document_id": "doc-1",
            "title": "Lesson",
            "metadata": {"lesson_type": "ATM"},
            "transcript": "move slowly",
            "raw_embedding": "[0.1,0.2]",
        }
    ]

    result = mock_db.get_rendition_inputs()

    sql = mock_db._mock_cursor.execute.call_args[0][0]
    assert "raw.method = 'raw'" in sql
    assert result[0]["raw_embedding"] == [0.1, 0.2]


def test_replace_rendition_gate_replaces_assignments_and_evidence(mock_db):
    shared = "lie on the floor and slowly roll the pelvis while breathing " * 12
    build = build_rendition_groups(
        [
            RenditionDocument("a", "A", {"lesson_type": "ATM"}, shared + "a"),
            RenditionDocument("b", "B", {"lesson_type": "ATM"}, shared + "b"),
        ]
    )

    with patch("kb.db.renditions.execute_values") as execute_values:
        mock_db.replace_rendition_gate(build)

    deletes = [call.args[0] for call in mock_db._mock_cursor.execute.call_args_list]
    assert deletes == [
        "DELETE FROM lesson_rendition_evidence",
        "DELETE FROM lesson_rendition",
    ]
    assert execute_values.call_count == 2
    assignments = execute_values.call_args_list[0].args[2]
    assert {row[0] for row in assignments} == {"a", "b"}
    assert len({row[1] for row in assignments}) == 1
    evidence = execute_values.call_args_list[1].args[2]
    assert evidence[0][3] == ["transcript_shingles"]


def test_get_renditions_is_explicit_and_excludes_query_member(mock_db):
    mock_db._mock_cursor.fetchall.return_value = [
        {
            "document_id": "peer",
            "title": "Peer",
            "lesson_key": "ay:23",
            "detection_version": "rendition-v1",
        }
    ]

    rows = mock_db.get_renditions("query")

    sql, params = mock_db._mock_cursor.execute.call_args[0]
    assert "peer.lesson_key = query.lesson_key" in sql
    assert "peer.document_id <> query.document_id" in sql
    assert params == ("query",)
    assert rows[0]["document_id"] == "peer"
