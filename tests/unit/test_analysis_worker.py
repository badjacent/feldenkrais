from unittest.mock import MagicMock, patch

from kb.pipeline.analysis import run_bounded_worker


class LockContext:
    def __init__(self, acquired):
        self.acquired = acquired

    def __enter__(self):
        return self.acquired

    def __exit__(self, *_args):
        return False


def test_worker_exits_when_another_host_holds_lease():
    db = MagicMock()
    db.advisory_lock.return_value = LockContext(False)

    result = run_bounded_worker(
        db, max_steps=10, max_seconds=60, workers=1, step_timeout=30
    )

    assert result == {"status": "busy", "completed": 0, "failed": 0}
    db.get_runnable_steps.assert_not_called()


def test_worker_drains_runnable_steps():
    db = MagicMock()
    db.advisory_lock.return_value = LockContext(True)
    db.get_runnable_steps.side_effect = [
        [{"document_id": "doc-1", "step_name": "decompose"}],
        [],
    ]
    db.count_runnable_steps.return_value = 0

    with patch(
        "kb.pipeline.analysis._run_step_process",
        return_value={"ok": True, "document_id": "doc-1", "step_name": "decompose"},
    ):
        result = run_bounded_worker(
            db, max_steps=10, max_seconds=60, workers=1, step_timeout=30
        )

    assert result["status"] == "completed"
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert result["remaining_runnable"] == 0
