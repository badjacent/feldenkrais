import threading
import time
from unittest.mock import MagicMock, patch

from kb.pipeline.analysis import EXIT_PROFILE_DISABLED, run_bounded_worker


class LockContext:
    def __init__(self, acquired):
        self.acquired = acquired

    def __enter__(self):
        return self.acquired

    def __exit__(self, *_args):
        return False


def _profile(key, *, enabled=True, max_concurrency=2, canonical=False):
    return {"key": key, "enabled": enabled, "max_concurrency": max_concurrency, "canonical": canonical}


def _worker_db(runnable_batches, profiles):
    db = MagicMock()
    db.advisory_lock.return_value = LockContext(True)
    db.get_runnable_steps.side_effect = runnable_batches + [[]] * 10
    db.get_analysis_profiles.return_value = profiles
    db.get_canonical_profile.return_value = next(p for p in profiles if p["canonical"])
    db.count_runnable_steps.return_value = 0
    return db


def _row(doc, step, profile):
    return {"document_id": doc, "step_name": step, "profile_key": profile}


def test_worker_exits_when_another_host_holds_lease():
    db = MagicMock()
    db.advisory_lock.return_value = LockContext(False)

    result = run_bounded_worker(
        db, max_steps=10, max_seconds=60, workers=1, step_timeout=30
    )

    assert result == {"status": "busy", "completed": 0, "failed": 0, "skipped": 0}
    db.get_runnable_steps.assert_not_called()


def test_worker_drains_runnable_steps_under_their_profiles():
    db = _worker_db(
        [[_row("doc-1", "decompose", "gemini-3-flash"), _row("doc-1", "decompose", "astra-low")]],
        [_profile("gemini-3-flash", canonical=True), _profile("astra-low")],
    )
    calls = []

    def run(document_id, step_name, profile_key, timeout):
        calls.append((document_id, step_name, profile_key))
        return {"ok": True, "document_id": document_id, "step_name": step_name, "profile": profile_key}

    with patch("kb.pipeline.analysis._run_step_process", side_effect=run), \
         patch("atlas_projection.maybe_publish_after_sweep") as publish:
        result = run_bounded_worker(db, max_steps=10, max_seconds=60, workers=2, step_timeout=30)

    assert result["status"] == "completed"
    assert result["completed"] == 2
    assert sorted(calls) == [("doc-1", "decompose", "astra-low"), ("doc-1", "decompose", "gemini-3-flash")]
    # Only the canonical profile's completions feed the Atlas projection.
    publish.assert_called_once_with(db, {"decompose"})


def test_worker_does_not_claim_for_a_switched_off_profile():
    db = _worker_db(
        [[_row("doc-1", "somatic", "astra-low"), _row("doc-2", "somatic", "gemini-3-flash")]],
        [_profile("gemini-3-flash", canonical=True), _profile("astra-low", enabled=False)],
    )
    with patch("kb.pipeline.analysis._run_step_process",
               return_value={"ok": True, "step_name": "somatic"}) as run, \
         patch("atlas_projection.maybe_publish_after_sweep"):
        result = run_bounded_worker(db, max_steps=10, max_seconds=60, workers=4, step_timeout=30)

    claimed = [c.args[2] for c in run.call_args_list]
    assert claimed == ["gemini-3-flash"]
    assert result["completed"] == 1


def test_worker_holds_each_profile_to_its_concurrency_limit():
    rows = [_row(f"doc-{i}", "somatic", "astra-low") for i in range(3)]
    db = _worker_db([rows], [_profile("gemini-3-flash", canonical=True),
                             _profile("astra-low", max_concurrency=1)])
    lock = threading.Lock()
    active = [0]
    peak = []

    def run(document_id, step_name, profile_key, timeout):
        with lock:
            active[0] += 1
            peak.append(active[0])
        time.sleep(0.05)  # hold the slot long enough for a second claim to overlap
        with lock:
            active[0] -= 1
        return {"ok": True, "step_name": step_name}

    with patch("kb.pipeline.analysis._run_step_process", side_effect=run), \
         patch("atlas_projection.maybe_publish_after_sweep"):
        result = run_bounded_worker(db, max_steps=10, max_seconds=60, workers=4, step_timeout=30)

    assert result["completed"] == 3
    assert max(peak) == 1


def test_worker_counts_a_step_skipped_because_its_profile_was_switched_off():
    db = _worker_db([[_row("doc-1", "language", "astra-low")]],
                    [_profile("gemini-3-flash", canonical=True), _profile("astra-low")])
    with patch("kb.pipeline.analysis._run_step_process",
               return_value={"ok": False, "skipped": True, "step_name": "language"}), \
         patch("atlas_projection.maybe_publish_after_sweep"):
        result = run_bounded_worker(db, max_steps=10, max_seconds=60, workers=1, step_timeout=30)

    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert EXIT_PROFILE_DISABLED == 3
