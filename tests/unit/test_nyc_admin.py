"""Tests for the NYC crawler administration surface.

Covers the contract in docs/NYC_CRAWLER_ADMIN_SPEC.md: authorization on every
route, the atlas_nyc boundary (schema-qualified, no DDL), audited and atomic
review decisions, stale-form conflict protection, and rollback.
"""

import inspect
import json
import re
import time
from uuid import UUID
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from tests.unit.route_helpers import app_route_map, iter_routes


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def nyc_db(mock_db):
    """mock_db with the atlas_nyc schema reported as present."""
    mock_db._schema_ok = True
    _queue(mock_db, fetchall=[[{"table_name": t} for t in _all_tables()]])
    return mock_db


def _all_tables():
    from kb.db.nyc import NYC_OPTIONAL_TABLES, NYC_REQUIRED_TABLES
    return list(NYC_REQUIRED_TABLES) + list(NYC_OPTIONAL_TABLES)


def _queue(db, *, fetchone=None, fetchall=None):
    """Queue cursor results; the schema probe always runs first."""
    if fetchall is not None:
        db._mock_cursor.fetchall.side_effect = list(fetchall)
    if fetchone is not None:
        db._mock_cursor.fetchone.side_effect = list(fetchone)


def _sql(db) -> list[str]:
    return [c[0][0] for c in db._mock_cursor.execute.call_args_list]


def _find(db, needle: str) -> str:
    for statement in _sql(db):
        if needle in statement:
            return statement
    raise AssertionError(f"no statement containing {needle!r} in {_sql(db)}")


def _row(**overrides) -> dict:
    row = {
        "id": "p1",
        "name": "Test provider",
        "status": "candidate",
        "crawl_state": {"tier": "C", "discovery_observations": [], "added_by": "seed"},
        "updated_at": datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        "enabled": False,
    }
    row.update(overrides)
    return row


class TestS3CrawlTrigger:
    def test_request_is_strict_provider_only_json(self):
        from kb.routes import nyc_admin

        s3 = MagicMock()
        provider_id = "d16f3551-e3df-4fa3-af4b-7d2529a25e61"
        with patch.object(nyc_admin._web, "KB_BUCKET", "private-bucket"), \
             patch.object(nyc_admin._web, "get_s3_client", return_value=s3):
            request_id = nyc_admin._queue_provider_crawl(provider_id, "owner")

        UUID(request_id)
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "private-bucket"
        assert kwargs["Key"].startswith("control/atlas-nyc/requests/")
        assert kwargs["ServerSideEncryption"] == "AES256"
        payload = json.loads(kwargs["Body"])
        assert payload == {
            "version": 1,
            "action": "crawl_provider",
            "provider_id": provider_id,
            "requested_by": "owner",
            "requested_at": payload["requested_at"],
            "request_id": request_id,
        }
        assert payload["requested_at"].endswith("+00:00")

    def test_request_requires_bucket_configuration(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", None), \
             pytest.raises(nyc_admin.NycAdminError, match="KB_BUCKET"):
            nyc_admin._queue_provider_crawl(
                "d16f3551-e3df-4fa3-af4b-7d2529a25e61", "owner")


class TestCrawlRequestStatus:
    """atlas_nyc.nyc_crawl_request has no migration yet — degrade, never fail."""

    def test_absent_table_means_status_is_untracked(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []
        assert mock_db.nyc_crawl_request_shape() is None
        assert mock_db.nyc_latest_crawl_requests(["p1"]) == {}

    def test_columns_are_discovered_not_assumed(self, mock_db):
        mock_db._mock_cursor.fetchall.side_effect = [
            [{"column_name": c} for c in
             ("entity_id", "state", "id", "created_at")],
            [{"provider_id": "p1", "status": "queued", "request_id": "r1",
              "requested_at": None, "actor": None, "detail": None}],
        ]
        rows = mock_db.nyc_latest_crawl_requests(["p1"])
        sql = _find(mock_db, "atlas_nyc.nyc_crawl_request")
        assert "entity_id AS provider_id" in sql
        assert "state AS status" in sql
        assert rows["p1"]["status"] == "queued"

    def test_table_without_a_status_column_is_unusable(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"column_name": "provider_id"}, {"column_name": "note"}]
        assert mock_db.nyc_crawl_request_shape() is None

    def test_terminal_statuses_match_the_mini_result_prefixes(self):
        from kb.nyc_control import TERMINAL_REQUEST_STATUSES, is_terminal

        assert TERMINAL_REQUEST_STATUSES == {"succeeded", "completed",
                                             "rejected", "dead"}
        assert is_terminal("Succeeded") is True
        # "failed" is retryable on the Mini and the request object stays in
        # place, so it is not finished; unrecognised statuses count as in
        # flight too, so we never double-send.
        assert is_terminal("failed") is False
        assert is_terminal("running") is False
        assert is_terminal(None) is False


class TestCheckSoonGating:
    def test_only_approved_providers_are_eligible(self):
        from kb.routes.nyc_admin import _check_eligibility

        assert _check_eligibility(
            {"status": "active", "enabled": True, "tier": "B"})[0] is True
        assert _check_eligibility(
            {"status": "candidate", "enabled": False, "tier": "C"})[0] is False

    def test_a_tier_d_record_is_never_checked(self):
        from kb.routes.nyc_admin import _check_eligibility

        eligible, reason = _check_eligibility(
            {"status": "active", "enabled": True, "tier": "D"})
        assert eligible is False
        assert "never crawled again" in reason

    def test_button_is_disabled_while_a_request_is_in_flight(self):
        from kb.routes import nyc_admin

        provider = {"id": "p1", "status": "active", "enabled": True, "tier": "A"}
        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"):
            state = nyc_admin._check_state(
                provider, {"status": "queued"}, True)
            assert state["enabled"] is False
            assert "already in flight" in state["reason"]

            done = nyc_admin._check_state(provider, {"status": "succeeded"}, True)
            assert done["enabled"] is True

            retrying = nyc_admin._check_state(provider, {"status": "failed"}, True)
            assert retrying["enabled"] is False

    def test_without_a_bucket_the_action_is_disabled_not_broken(self):
        from kb.routes import nyc_admin

        provider = {"id": "p1", "status": "active", "enabled": True, "tier": "A"}
        with patch.object(nyc_admin._web, "KB_BUCKET", None):
            state = nyc_admin._check_state(provider, None, False)
        assert state["enabled"] is False
        assert "control bucket" in state["reason"]
        html = nyc_admin._check_soon_control(provider, state, compact=False)
        assert "disabled" in html

    def test_untracked_status_is_stated_plainly(self):
        from kb.routes import nyc_admin

        provider = {"id": "p1", "status": "active", "enabled": True, "tier": "A"}
        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"):
            state = nyc_admin._check_state(provider, None, False)
        html = nyc_admin._check_soon_control(provider, state, compact=True)
        assert "not tracked yet" in html
        assert "disabled" not in html   # still sendable; only status is unknown

    def test_a_status_read_failure_never_breaks_the_page(self):
        from kb.routes import nyc_admin

        db = MagicMock()
        db.nyc_crawl_request_shape.side_effect = RuntimeError("relation missing")
        with patch.object(nyc_admin._web, "_db", db):
            assert nyc_admin._latest_check_requests(["p1"]) == ({}, False)


class TestPostCommitSignal:
    """"A failed S3 write must not roll back an already-audited tier decision."""

    def _result(self, **over):
        base = {"name": "P", "decision": "approved", "tier": "A",
                "audit_id": "abcd1234", "previous_status": "candidate",
                "previous_tier": "C", "status": "active"}
        base.update(over)
        return base

    def test_approval_requests_an_expedited_check(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"), \
             patch.object(nyc_admin, "_latest_check_requests", return_value=({}, True)), \
             patch.object(nyc_admin, "_queue_provider_crawl",
                          return_value="11111111-2222-3333-4444-555555555555") as put:
            note = nyc_admin._signal_after_policy_change(
                "provider", "p1", self._result(), "owner")
        assert put.call_args[0] == ("p1", "owner")
        assert "Expedited check requested" in note

    def test_rejection_never_signals(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"), \
             patch.object(nyc_admin, "_queue_provider_crawl") as put:
            note = nyc_admin._signal_after_policy_change(
                "provider", "p1", self._result(decision="rejected", tier="D"), "owner")
        assert note == ""
        put.assert_not_called()

    def test_venue_review_never_signals(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"), \
             patch.object(nyc_admin, "_queue_provider_crawl") as put:
            nyc_admin._signal_after_policy_change("venue", "v1", self._result(), "owner")
        put.assert_not_called()

    def test_s3_failure_is_reported_and_the_decision_stands(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"), \
             patch.object(nyc_admin, "_latest_check_requests", return_value=({}, True)), \
             patch.object(nyc_admin, "_queue_provider_crawl",
                          side_effect=RuntimeError("AccessDenied")):
            note = nyc_admin._signal_after_policy_change(
                "provider", "p1", self._result(), "owner")
        assert "signal failed" in note
        assert "AccessDenied" in note

    def test_an_in_flight_request_is_not_duplicated(self):
        from kb.routes import nyc_admin

        with patch.object(nyc_admin._web, "KB_BUCKET", "bucket"), \
             patch.object(nyc_admin, "_latest_check_requests",
                          return_value=({"p1": {"status": "queued"}}, True)), \
             patch.object(nyc_admin, "_queue_provider_crawl") as put:
            note = nyc_admin._signal_after_policy_change(
                "provider", "p1", self._result(), "owner")
        put.assert_not_called()
        assert "already in flight" in note


# ── The atlas_nyc boundary ───────────────────────────────────────────────

class TestSchemaBoundary:
    def test_no_ddl_anywhere(self):
        """The site must never create or alter tables in request handling."""
        from kb.db import nyc as nyc_db_mod
        from kb.routes import nyc_admin

        for module in (nyc_db_mod, nyc_admin):
            source = inspect.getsource(module).upper()
            for forbidden in (r"CREATE\s+TABLE", r"ALTER\s+TABLE", r"DROP\s+TABLE",
                              r"CREATE\s+SCHEMA", r"\bTRUNCATE\b"):
                assert not re.search(forbidden, source), (
                    f"{module.__name__} contains {forbidden}")

    def test_every_nyc_table_reference_is_schema_qualified(self):
        """No reliance on search_path — the app's default schema is a different DB area."""
        from kb.db import nyc as nyc_db_mod
        from kb.db.nyc import NYC_REQUIRED_TABLES

        source = inspect.getsource(nyc_db_mod)
        for table in NYC_REQUIRED_TABLES:
            for keyword in ("FROM", "JOIN", "INTO", "UPDATE"):
                bad = re.findall(rf"{keyword}\s+{table}\b", source)
                assert not bad, f"unqualified {keyword} {table}"

    def test_writes_target_only_atlas_nyc(self):
        """Every mutating statement in the adapter names an atlas_nyc table."""
        from kb.db import nyc as nyc_db_mod

        source = inspect.getsource(nyc_db_mod)
        for verb in ("UPDATE", "INSERT INTO", "DELETE FROM"):
            # "FOR UPDATE" is a lock hint, not a write.
            for match in re.finditer(rf"(?<!FOR ){verb}\s+(\S+)", source):
                target = match.group(1)
                assert target.startswith("{NYC_SCHEMA}."), (
                    f"{verb} targets {target}, which is outside atlas_nyc")

    def test_schema_status_reports_missing_tables(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"table_name": "nyc_provider"}, {"table_name": "nyc_venue"}]
        status = mock_db.nyc_schema_status()
        assert status["ok"] is False
        assert "nyc_offering" in status["missing"]

    def test_require_schema_raises_diagnostic(self, mock_db):
        from kb.db.nyc import NycSchemaMissingError

        mock_db._mock_cursor.fetchall.return_value = [{"table_name": "nyc_provider"}]
        with pytest.raises(NycSchemaMissingError) as exc:
            mock_db.nyc_require_schema()
        assert "nyc_discovery_run" in exc.value.missing

    def test_reads_check_schema_before_querying(self, mock_db):
        from kb.db.nyc import NycSchemaMissingError

        mock_db._mock_cursor.fetchall.return_value = []
        for call in (mock_db.nyc_overview, mock_db.nyc_providers, mock_db.nyc_venues,
                     mock_db.nyc_discovery_runs, mock_db.nyc_tasks):
            with pytest.raises(NycSchemaMissingError):
                call()

    def test_optional_tables_may_be_absent(self, mock_db):
        """fetch_log and nyc_crawl_request are conveniences, not requirements."""
        from kb.db.nyc import NYC_OPTIONAL_TABLES, NYC_REQUIRED_TABLES

        mock_db._mock_cursor.fetchall.return_value = [
            {"table_name": t} for t in NYC_REQUIRED_TABLES]
        status = mock_db.nyc_schema_status()
        assert status["ok"] is True
        assert status["optional_missing"] == list(NYC_OPTIONAL_TABLES)


# ── Tier and due-state semantics ─────────────────────────────────────────

class TestTierSemantics:
    def test_intervals_match_the_crawler(self):
        """A daily, B weekly, C monthly — the Mini's NYC_TIER_INTERVAL_DAYS."""
        from kb.db.nyc import NYC_TIER_INTERVAL_DAYS

        assert NYC_TIER_INTERVAL_DAYS == {"A": 1, "B": 7, "C": 30}

    def test_due_sql_encodes_each_interval(self):
        from kb.db.nyc import _due_sql

        sql = _due_sql("p", enabled=True)
        assert "INTERVAL '1 day'" in sql
        assert "INTERVAL '7 days'" in sql
        assert "INTERVAL '30 days'" in sql
        assert "p.tier <> 'D'" in sql
        assert "p.enabled" in sql

    def test_venue_due_sql_has_no_enabled_column(self):
        from kb.db.nyc import _due_sql

        assert "enabled" not in _due_sql("v", enabled=False)

    def test_tier_defaults_to_c_and_rejects_junk(self):
        from kb.db.nyc import _normalized_tier

        assert _normalized_tier({}) == "C"
        assert _normalized_tier({"tier": "b"}) == "B"
        assert _normalized_tier({"tier": "Z"}) == "C"

    def test_rejected_entry_is_never_due(self):
        from kb.db.nyc import _is_due

        entry = {"tier": "D", "status": "retired", "enabled": False,
                 "last_crawled_at": None}
        assert _is_due(entry, enabled_field=True) is False

    def test_approved_candidate_is_immediately_due(self):
        """Acceptance: approving on the site makes the record due to the Mini."""
        from kb.db.nyc import _is_due

        approved = {"tier": "B", "status": "active", "enabled": True,
                    "last_crawled_at": None}
        assert _is_due(approved, enabled_field=True) is True

    def test_due_respects_the_tier_interval(self):
        from kb.db.nyc import _is_due

        now = datetime.now(timezone.utc)
        recent = {"tier": "B", "status": "active", "enabled": True,
                  "last_crawled_at": now - timedelta(days=3)}
        stale = {"tier": "B", "status": "active", "enabled": True,
                 "last_crawled_at": now - timedelta(days=8)}
        assert _is_due(recent, enabled_field=True) is False
        assert _is_due(stale, enabled_field=True) is True


# ── The review transaction ───────────────────────────────────────────────

class TestReviewTransaction:
    def test_approval_locks_updates_and_audits_in_one_transaction(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        result = nyc_db.nyc_review_entry(
            entity_type="provider", entity_id="p1", decision="approved",
            tier="A", actor="owner", rationale="verified live schedule")

        select_sql = _find(nyc_db, "FOR UPDATE")
        assert "atlas_nyc.nyc_provider" in select_sql

        update_sql = _find(nyc_db, "UPDATE atlas_nyc.nyc_provider")
        assert "status = %s" in update_sql and "enabled = %s" in update_sql

        insert_sql = _find(nyc_db, "INSERT INTO atlas_nyc.nyc_directory_decision")
        assert "previous_status" in insert_sql and "previous_tier" in insert_sql

        # One connection, one commit: lock, update, and audit are atomic.
        assert nyc_db._pool.getconn.call_count == 2  # schema probe + review
        conn = nyc_db._pool.getconn.return_value
        assert conn.commit.call_count == 1
        conn.rollback.assert_not_called()

        assert result["decision"] == "approved"
        assert result["tier"] == "A"
        assert result["previous_status"] == "candidate"
        assert result["previous_tier"] == "C"
        assert result["status"] == "active"
        assert result["audit_id"]

    def test_approval_sets_active_and_enabled(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                decision="approved", tier="B", actor="owner")
        params = _params_for(nyc_db, "UPDATE atlas_nyc.nyc_provider")
        state = json.loads(params[0])
        assert params[1] == "active"
        assert params[2] is True
        assert state["tier"] == "B"
        assert state["review_decision"] == "approved"
        assert state["reviewed_by"] == "owner"

    def test_rejection_retires_disables_and_forces_tier_d(self, nyc_db):
        _queue(nyc_db, fetchone=[_row(status="active", enabled=True,
                                      crawl_state={"tier": "A"})])
        result = nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                         decision="rejected", tier="A", actor="owner")
        params = _params_for(nyc_db, "UPDATE atlas_nyc.nyc_provider")
        state = json.loads(params[0])
        assert params[1] == "retired"
        assert params[2] is False
        assert state["tier"] == "D"
        assert result["tier"] == "D"

    def test_unrelated_crawl_state_keys_survive(self, nyc_db):
        _queue(nyc_db, fetchone=[_row(crawl_state={
            "tier": "C", "duplicate_of": "other", "discovery_observations": [{"url": "u"}],
            "last_schedule_check": "2026-08-01T00:00:00+00:00"})])
        nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                decision="approved", tier="A", actor="owner")
        state = json.loads(_params_for(nyc_db, "UPDATE atlas_nyc.nyc_provider")[0])
        assert state["duplicate_of"] == "other"
        assert state["last_schedule_check"] == "2026-08-01T00:00:00+00:00"
        assert state["discovery_observations"] == [{"url": "u"}]

    def test_venue_review_does_not_touch_enabled(self, nyc_db):
        _queue(nyc_db, fetchone=[_row(id="v1", name="Venue")])
        nyc_db.nyc_review_entry(entity_type="venue", entity_id="v1",
                                decision="approved", tier="C", actor="owner")
        update_sql = _find(nyc_db, "UPDATE atlas_nyc.nyc_venue")
        assert "enabled" not in update_sql

    def test_audit_row_carries_actor_and_rationale(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                decision="approved", tier="A", actor="owner",
                                rationale="first-party schedule")
        params = _params_for(nyc_db, "INSERT INTO atlas_nyc.nyc_directory_decision")
        assert params[1] == "provider"
        assert params[2] == "p1"
        assert params[3] == "approved"
        assert params[4] == "candidate"   # previous_status
        assert params[5] == "C"           # previous_tier
        assert params[6] == "A"           # new tier
        assert params[7] == "owner"
        assert params[8] == "first-party schedule"

    def test_blank_rationale_is_stored_as_null(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                decision="approved", tier="A", actor="owner",
                                rationale=None)
        assert _params_for(nyc_db, "INSERT INTO atlas_nyc.nyc_directory_decision")[8] is None

    @pytest.mark.parametrize("kwargs", [
        {"entity_type": "lesson", "decision": "approved", "tier": "A"},
        {"entity_type": "provider", "decision": "maybe", "tier": "A"},
        {"entity_type": "provider", "decision": "approved", "tier": "D"},
        {"entity_type": "provider", "decision": "approved", "tier": "Z"},
    ])
    def test_invalid_decisions_write_nothing(self, nyc_db, kwargs):
        with pytest.raises(ValueError):
            nyc_db.nyc_review_entry(entity_id="p1", actor="owner", **kwargs)
        assert not _writes(nyc_db)

    def test_actor_is_required(self, nyc_db):
        with pytest.raises(ValueError):
            nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                    decision="approved", tier="A", actor="")

    def test_missing_entity_raises_not_found(self, nyc_db):
        from kb.db.nyc import NycNotFoundError

        _queue(nyc_db, fetchone=[None])
        with pytest.raises(NycNotFoundError):
            nyc_db.nyc_review_entry(entity_type="provider", entity_id="ghost",
                                    decision="approved", tier="A", actor="owner")

    def test_failure_after_the_lock_rolls_the_whole_thing_back(self, nyc_db):
        """An error between UPDATE and the audit insert must leave no partial write."""
        _queue(nyc_db, fetchone=[_row()])
        nyc_db._mock_cursor.execute.side_effect = [
            None,                        # SELECT ... FOR UPDATE
            None,                        # UPDATE
            RuntimeError("audit insert exploded"),
        ]
        with pytest.raises(RuntimeError):
            nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                    decision="approved", tier="A", actor="owner")
        conn = nyc_db._pool.getconn.return_value
        conn.rollback.assert_called_once()
        assert conn.commit.call_count == 0


class TestStaleFormProtection:
    def test_stale_form_is_rejected_and_shows_current_state(self, nyc_db):
        from kb.db.nyc import NycStaleFormError

        current = _row(status="active",
                       updated_at=datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc))
        _queue(nyc_db, fetchone=[current])
        with pytest.raises(NycStaleFormError) as exc:
            nyc_db.nyc_review_entry(
                entity_type="provider", entity_id="p1", decision="rejected",
                tier="D", actor="owner",
                expected_updated_at="2026-08-27T12:00:00+00:00")
        assert exc.value.current["status"] == "active"

    def test_stale_form_writes_nothing_and_rolls_back(self, nyc_db):
        from kb.db.nyc import NycStaleFormError

        _queue(nyc_db, fetchone=[_row()])
        with pytest.raises(NycStaleFormError):
            nyc_db.nyc_review_entry(
                entity_type="provider", entity_id="p1", decision="approved",
                tier="A", actor="owner", expected_updated_at="2020-01-01T00:00:00+00:00")
        assert not _writes(nyc_db)
        nyc_db._pool.getconn.return_value.rollback.assert_called_once()

    def test_matching_stamp_proceeds(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        nyc_db.nyc_review_entry(
            entity_type="provider", entity_id="p1", decision="approved", tier="A",
            actor="owner", expected_updated_at="2026-08-27T12:00:00+00:00")
        assert _find(nyc_db, "UPDATE atlas_nyc.nyc_provider")

    def test_unparseable_stamp_is_treated_as_stale(self, nyc_db):
        from kb.db.nyc import NycStaleFormError

        _queue(nyc_db, fetchone=[_row()])
        with pytest.raises(NycStaleFormError):
            nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                    decision="approved", tier="A", actor="owner",
                                    expected_updated_at="not-a-timestamp")

    def test_omitted_stamp_skips_the_check(self, nyc_db):
        _queue(nyc_db, fetchone=[_row()])
        nyc_db.nyc_review_entry(entity_type="provider", entity_id="p1",
                                decision="approved", tier="A", actor="owner")
        assert _find(nyc_db, "UPDATE atlas_nyc.nyc_provider")


# ── Task queue actions ───────────────────────────────────────────────────

class TestTaskActions:
    def test_retry_requeues_and_clears_the_attempt_counter(self, nyc_db):
        _queue(nyc_db, fetchone=[{"id": "t1", "status": "failed", "attempts": 2,
                                  "result": {"model": "hermes"}}])
        result = nyc_db.nyc_task_action(task_id="t1", action="retry", actor="owner")
        update_sql = _find(nyc_db, "UPDATE atlas_nyc.discovery_task")
        assert "status = 'queued'" in update_sql
        assert "attempts = 0" in update_sql
        assert result["status"] == "queued"

    def test_action_records_the_actor_in_the_row(self, nyc_db):
        _queue(nyc_db, fetchone=[{"id": "t1", "status": "failed", "attempts": 2,
                                  "result": {}}])
        nyc_db.nyc_task_action(task_id="t1", action="retry", actor="owner")
        stored = json.loads(_params_for(nyc_db, "UPDATE atlas_nyc.discovery_task")[0])
        entry = stored["admin_actions"][-1]
        assert entry["actor"] == "owner"
        assert entry["action"] == "retry"
        assert entry["previous_status"] == "failed"

    def test_mark_reviewed_closes_the_task(self, nyc_db):
        _queue(nyc_db, fetchone=[{"id": "t1", "status": "queued", "attempts": 0,
                                  "result": {}}])
        result = nyc_db.nyc_task_action(task_id="t1", action="mark_reviewed",
                                        actor="owner")
        assert "status = 'done'" in _find(nyc_db, "UPDATE atlas_nyc.discovery_task")
        assert result["status"] == "done"

    def test_running_task_is_left_to_the_mini(self, nyc_db):
        from kb.db.nyc import NycAdminError

        _queue(nyc_db, fetchone=[{"id": "t1", "status": "running", "attempts": 1,
                                  "result": {}}])
        with pytest.raises(NycAdminError):
            nyc_db.nyc_task_action(task_id="t1", action="retry", actor="owner")
        assert not _writes(nyc_db)

    def test_state_change_since_page_load_is_a_conflict(self, nyc_db):
        from kb.db.nyc import NycStaleFormError

        _queue(nyc_db, fetchone=[{"id": "t1", "status": "done", "attempts": 1,
                                  "result": {}}])
        with pytest.raises(NycStaleFormError):
            nyc_db.nyc_task_action(task_id="t1", action="retry", actor="owner",
                                   expected_status="failed")

    def test_unknown_action_is_rejected(self, nyc_db):
        with pytest.raises(ValueError):
            nyc_db.nyc_task_action(task_id="t1", action="delete", actor="owner")

    def test_only_nyc_schedule_review_tasks_are_visible(self, nyc_db):
        _queue(nyc_db, fetchall=[[{"table_name": t} for t in _all_tables()], []])
        nyc_db.nyc_tasks()
        sql = _find(nyc_db, "atlas_nyc.discovery_task")
        assert "task_type = %s" in sql
        params = _params_for(nyc_db, "atlas_nyc.discovery_task")
        assert params[0] == "nyc_schedule_review"

    def test_task_excerpt_is_bounded(self, nyc_db):
        from kb.db.nyc import NYC_TASK_EXCERPT_CHARS

        _queue(nyc_db, fetchone=[{"id": "t1", "status": "failed",
                                  "payload": {"text_excerpt": "x" * 50000,
                                              "source_url": "https://e.test"},
                                  "result": {}}])
        task = nyc_db.nyc_task_detail("t1")
        assert len(task["text_excerpt"]) == NYC_TASK_EXCERPT_CHARS
        assert task["text_excerpt_truncated"] is True
        assert "text_excerpt" not in task["payload_without_excerpt"]


# ── Events: nothing is synthesized ───────────────────────────────────────

class TestEventSemantics:
    def test_undated_offerings_are_labelled_not_dated(self):
        """An offering that only says "weekly" never becomes a dated occurrence."""
        from kb.db.nyc import _OFFERING_BUCKET_SQL

        assert "undated_recurring" in _OFFERING_BUCKET_SQL
        assert "needs_review" in _OFFERING_BUCKET_SQL

    def test_occurrence_buckets_cover_every_spec_state(self):
        from kb.db.nyc import _OCCURRENCE_BUCKET_SQL
        from kb.routes.nyc_admin import _BUCKET_LABEL

        for state in ("cancelled", "sold_out", "unconfirmed", "expired",
                      "confirmed_upcoming"):
            assert state in _OCCURRENCE_BUCKET_SQL
            assert state in _BUCKET_LABEL

    def test_no_occurrence_is_ever_inserted(self):
        from kb.db import nyc as nyc_db_mod

        source = inspect.getsource(nyc_db_mod)
        assert "INSERT INTO {NYC_SCHEMA}.nyc_occurrence" not in source
        assert "INSERT INTO {NYC_SCHEMA}.nyc_offering" not in source


# ── Routes: authorization and privacy ────────────────────────────────────

def _cookie(tier: str, login: str) -> str:
    import auth as auth_mod

    return auth_mod.encode_cookie({
        "user_id": 1, "tier": tier, "login": login,
        "superuser_eligible": False, "exp": int(time.time()) + 3600,
    })


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import web

    fake_db = MagicMock()
    with patch.object(web, "_db", fake_db):
        with TestClient(web.app, follow_redirects=False) as c:
            c.fake_db = fake_db
            yield c


NYC_GET_ROUTES = [
    "/admin/nyc",
    "/admin/nyc/providers",
    "/admin/nyc/providers/p1",
    "/admin/nyc/venues",
    "/admin/nyc/venues/v1",
    "/admin/nyc/events",
    "/admin/nyc/events/offering/o1",
    "/admin/nyc/discovery",
    "/admin/nyc/discovery/r1",
    "/admin/nyc/tasks",
    "/admin/nyc/tasks/t1",
    "/admin/nyc/decisions",
]

NYC_POST_ROUTES = [
    ("/admin/nyc/providers/p1/review", {"decision": "approved", "tier": "A"}),
    ("/admin/nyc/providers/p1/check-soon", {}),
    ("/admin/nyc/venues/v1/review", {"decision": "approved", "tier": "A"}),
    ("/admin/nyc/tasks/t1/action", {"action": "retry"}),
]


class TestRouteAuthorization:
    @pytest.mark.parametrize("path", NYC_GET_ROUTES)
    def test_anonymous_gets_the_login_flow(self, client, path):
        response = client.get(path)
        assert response.status_code == 401
        assert "sign in" in response.text.lower()
        client.fake_db.assert_not_called()

    @pytest.mark.parametrize("path", NYC_GET_ROUTES)
    def test_signed_in_non_admin_is_forbidden(self, client, path):
        import auth as auth_mod

        client.cookies.set(auth_mod.COOKIE_NAME, _cookie("user", "member"))
        response = client.get(path)
        assert response.status_code == 403

    @pytest.mark.parametrize("path,data", NYC_POST_ROUTES)
    def test_mutations_reject_anonymous_without_touching_the_database(
            self, client, path, data):
        response = client.post(path, data=data)
        assert response.status_code == 401
        assert not client.fake_db.nyc_review_entry.called
        assert not client.fake_db.nyc_task_action.called

    @pytest.mark.parametrize("path,data", NYC_POST_ROUTES)
    def test_mutations_reject_non_admins_without_touching_the_database(
            self, client, path, data):
        import auth as auth_mod

        client.cookies.set(auth_mod.COOKIE_NAME, _cookie("user", "member"))
        response = client.post(path, data=data)
        assert response.status_code == 403
        assert not client.fake_db.nyc_review_entry.called
        assert not client.fake_db.nyc_task_action.called

    def test_localhost_needs_no_login(self, client):
        """Local development has full access, as elsewhere on the site."""
        client.fake_db.nyc_task_action.return_value = {
            "action": "retry", "previous_status": "failed", "status": "queued"}
        response = client.post("/admin/nyc/tasks/t1/action", data={"action": "retry"},
                               headers={"host": "localhost:8000"})
        assert response.status_code == 303
        assert client.fake_db.nyc_task_action.call_args.kwargs["actor"] == "localhost"

    def test_localhost_bypass_is_off_in_lambda(self):
        """The deployed console still requires the admin role."""
        import web
        from routes.nyc_admin import _is_local_request

        request = MagicMock()
        request.headers = {"host": "localhost:8000"}
        with patch.object(web, "IS_LAMBDA", True):
            assert _is_local_request(request) is False
        with patch.object(web, "IS_LAMBDA", False):
            assert _is_local_request(request) is True
            request.headers = {"host": "aionyourside.net"}
            assert _is_local_request(request) is False

    def test_every_nyc_route_is_guarded(self):
        """No handler under /admin/nyc may skip the admin guard."""
        from kb.routes import nyc_admin

        for route in nyc_admin.router.routes:
            source = inspect.getsource(route.endpoint)
            guarded = "_guard(request)" in source or "_apply_review(" in source
            assert guarded, f"{route.path} is unguarded"

    def test_no_nyc_route_lives_outside_the_admin_prefix(self):
        from kb.routes import nyc_admin

        for route in nyc_admin.router.routes:
            assert route.path.startswith("/admin/nyc"), route.path

    def test_nyc_routes_are_mounted_on_the_app(self):
        from kb.web import app

        paths = set(app_route_map(app))
        assert "/admin/nyc" in paths
        assert "/admin/nyc/providers/{provider_id}/review" in paths

    def test_no_public_json_endpoint_exposes_nyc_data(self):
        from kb.web import app

        for route in iter_routes(app.routes):
            path = getattr(route, "path", "")
            if "nyc" in path.lower():
                assert path.startswith("/admin/nyc"), path


class TestRouteMutations:
    def _admin(self, client):
        import auth as auth_mod

        client.cookies.set(auth_mod.COOKIE_NAME, _cookie("admin", "owner"))

    def test_review_passes_the_authenticated_actor(self, client):
        self._admin(client)
        client.fake_db.nyc_review_entry.return_value = {
            "name": "P", "decision": "approved", "tier": "A", "audit_id": "abcd1234",
            "previous_status": "candidate", "previous_tier": "C", "status": "active"}
        response = client.post("/admin/nyc/providers/p1/review", data={
            "decision": "approved", "tier": "A", "rationale": "live schedule",
            "expected_updated_at": "2026-08-27T12:00:00+00:00"})
        assert response.status_code == 303
        kwargs = client.fake_db.nyc_review_entry.call_args.kwargs
        assert kwargs["actor"] == "owner"
        assert kwargs["entity_type"] == "provider"
        assert kwargs["entity_id"] == "p1"
        assert kwargs["tier"] == "A"
        assert kwargs["rationale"] == "live schedule"
        assert kwargs["expected_updated_at"] == "2026-08-27T12:00:00+00:00"

    def test_venue_review_targets_the_venue_entity(self, client):
        self._admin(client)
        client.fake_db.nyc_review_entry.return_value = {
            "name": "V", "decision": "rejected", "tier": "D", "audit_id": "beef0000",
            "previous_status": "observed", "previous_tier": "C", "status": "retired"}
        client.post("/admin/nyc/venues/v1/review",
                    data={"decision": "rejected", "tier": "D"})
        assert client.fake_db.nyc_review_entry.call_args.kwargs["entity_type"] == "venue"

    def test_compact_reject_button_overrides_the_approve_default(self, client):
        self._admin(client)
        client.fake_db.nyc_review_entry.return_value = {
            "name": "P", "decision": "rejected", "tier": "D", "audit_id": "0000beef",
            "previous_status": "candidate", "previous_tier": "C", "status": "retired"}
        client.post("/admin/nyc/providers/p1/review",
                    data={"decision": "approved", "decision_override": "rejected"})
        assert client.fake_db.nyc_review_entry.call_args.kwargs["decision"] == "rejected"

    def test_stale_form_reports_the_conflict_instead_of_overwriting(self, client):
        from routes.nyc_admin import NycStaleFormError

        self._admin(client)
        client.fake_db.nyc_review_entry.side_effect = NycStaleFormError(
            {"status": "active"})
        response = client.post("/admin/nyc/providers/p1/review",
                               data={"decision": "approved", "tier": "A",
                                     "expected_updated_at": "2026-01-01T00:00:00+00:00"})
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert "changed" in response.headers["location"]

    def test_invalid_review_is_reported_not_raised(self, client):
        self._admin(client)
        client.fake_db.nyc_review_entry.side_effect = ValueError(
            "approved NYC entries must use tier A, B, or C")
        response = client.post("/admin/nyc/providers/p1/review",
                               data={"decision": "approved", "tier": "D"})
        assert response.status_code == 303
        assert "err=" in response.headers["location"]

    def test_task_retry_only_changes_queue_state(self, client):
        self._admin(client)
        client.fake_db.nyc_task_action.return_value = {
            "action": "retry", "previous_status": "failed", "status": "queued"}
        response = client.post("/admin/nyc/tasks/t1/action",
                               data={"action": "retry", "expected_status": "failed"})
        assert response.status_code == 303
        kwargs = client.fake_db.nyc_task_action.call_args.kwargs
        assert kwargs == {"task_id": "t1", "action": "retry", "actor": "owner",
                          "expected_status": "failed"}

    def test_check_soon_sends_the_server_loaded_uuid(self, client):
        # the module the app actually serves, not a second import of the file
        from routes import nyc_admin

        self._admin(client)
        provider_id = "d16f3551-e3df-4fa3-af4b-7d2529a25e61"
        client.fake_db.nyc_provider_for_check.return_value = {
            "id": provider_id, "name": "P", "status": "active", "enabled": True,
            "tier": "A", "check_eligible": True, "check_blocked_reason": ""}
        s3 = MagicMock()
        with patch.object(nyc_admin._web, "KB_BUCKET", "private-bucket"), \
             patch.object(nyc_admin._web, "get_s3_client", return_value=s3), \
             patch.object(nyc_admin, "_latest_check_requests", return_value=({}, True)):
            response = client.post("/admin/nyc/providers/p1/check-soon", data={})
        assert response.status_code == 303
        client.fake_db.nyc_provider_for_check.assert_called_once_with("p1")
        payload = json.loads(s3.put_object.call_args.kwargs["Body"])
        assert payload["provider_id"] == provider_id   # from the DB row, not the URL
        assert "url" not in payload and "command" not in payload

    def test_check_soon_reports_a_refused_write(self, client):
        from routes import nyc_admin

        self._admin(client)
        client.fake_db.nyc_provider_for_check.return_value = {
            "id": "d16f3551-e3df-4fa3-af4b-7d2529a25e61", "name": "P",
            "status": "active", "enabled": True, "tier": "A",
            "check_eligible": True, "check_blocked_reason": ""}
        s3 = MagicMock()
        s3.put_object.side_effect = RuntimeError("AccessDenied")
        with patch.object(nyc_admin._web, "KB_BUCKET", "private-bucket"), \
             patch.object(nyc_admin._web, "get_s3_client", return_value=s3), \
             patch.object(nyc_admin, "_latest_check_requests", return_value=({}, True)):
            response = client.post("/admin/nyc/providers/p1/check-soon", data={})
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert "AccessDenied" in response.headers["location"]

    def test_check_soon_refuses_an_ineligible_provider(self, client):
        from routes import nyc_admin

        self._admin(client)
        client.fake_db.nyc_provider_for_check.return_value = {
            "id": "p1", "name": "P", "status": "retired", "enabled": False,
            "tier": "D", "check_eligible": False,
            "check_blocked_reason": "a tier D record is never crawled again"}
        s3 = MagicMock()
        with patch.object(nyc_admin._web, "KB_BUCKET", "private-bucket"), \
             patch.object(nyc_admin._web, "get_s3_client", return_value=s3), \
             patch.object(nyc_admin, "_latest_check_requests", return_value=({}, True)):
            response = client.post("/admin/nyc/providers/p1/check-soon", data={})
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        s3.put_object.assert_not_called()

    def test_the_site_never_lists_or_reads_the_control_prefix(self):
        """The web Lambda holds PutObject on the request prefix and nothing more."""
        from kb import nyc_control
        from kb.routes import nyc_admin

        for module in (nyc_control, nyc_admin):
            source = inspect.getsource(module)
            assert "list_objects" not in source
            assert "get_object" not in source
            assert "delete_object" not in source

    def test_the_site_never_runs_the_crawler_or_hermes(self):
        from kb.routes import nyc_admin

        source = inspect.getsource(nyc_admin)
        for forbidden in ("subprocess", "os.system", "enrich_nyc_schedule_tasks",
                          "crawl_nyc_providers", "make_enricher"):
            assert forbidden not in source

    def test_the_site_never_writes_the_bootstrap_yaml(self):
        from kb.db import nyc as nyc_db_mod
        from kb.routes import nyc_admin

        for module in (nyc_db_mod, nyc_admin):
            source = inspect.getsource(module)
            assert "nyc.yaml" not in source

    def test_factual_edits_are_not_exposed_yet(self):
        """Milestone 6: no factual edit until nyc_directory_change exists."""
        from kb.routes import nyc_admin

        paths = {r.path for r in nyc_admin.router.routes}
        assert not [p for p in paths if p.endswith(("/edit", "/merge", "/defer"))]
        assert "nyc_directory_change" in nyc_admin.PENDING_MIGRATION_NOTE


# ── Helpers ──────────────────────────────────────────────────────────────

def _writes(db) -> list[str]:
    """Statements that actually mutate — "FOR UPDATE" is a lock, not a write."""
    return [s for s in _sql(db)
            if re.search(r"(?<!FOR )\bUPDATE\s+\{?atlas_nyc|INSERT INTO", s)]


def _params_for(db, needle: str):
    for call in db._mock_cursor.execute.call_args_list:
        if needle in call[0][0]:
            return call[0][1]
    raise AssertionError(f"no statement containing {needle!r}")
