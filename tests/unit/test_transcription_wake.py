from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from kb.ingest import transcription_wake as tw


def _missing():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


def test_take_removes_a_present_marker():
    s3 = MagicMock()
    assert tw.take("bucket", client=s3) is True
    s3.delete_object.assert_called_once_with(Bucket="bucket", Key=tw.MARKER_KEY)


def test_take_reports_an_absent_marker_without_deleting():
    s3 = MagicMock()
    s3.head_object.side_effect = _missing()
    assert tw.take("bucket", client=s3) is False
    s3.delete_object.assert_not_called()


def test_take_raises_on_other_s3_errors():
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
    with pytest.raises(ClientError):
        tw.take("bucket", client=s3)


def test_arm_writes_an_empty_marker():
    s3 = MagicMock()
    tw.arm("bucket", client=s3)
    s3.put_object.assert_called_once_with(Bucket="bucket", Key=tw.MARKER_KEY, Body=b"")


def test_cli_exit_codes(monkeypatch):
    monkeypatch.setattr(tw, "take", lambda: False)
    assert tw.main(["x", "take"]) == tw.EXIT_ABSENT
    monkeypatch.setattr(tw, "take", lambda: True)
    assert tw.main(["x", "take"]) == 0
    assert tw.main(["x", "bogus"]) == 2
