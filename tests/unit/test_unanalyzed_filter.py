"""Transcript-only lessons (transcribed, never analyzed) are hidden from lesson lists by default."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import html_builders
import web
from db.lesson_metadata import analyzed_sql
from web_pages import UNANALYZED_COOKIE, build_search_home_html, unanalyzed_filter_html


def _request(query=None, cookies=None):
    return SimpleNamespace(query_params=query or {}, cookies=cookies or {})


class TestPreference:
    def test_hidden_by_default(self):
        assert web._show_unanalyzed(_request()) is False

    def test_cookie_shows_them(self):
        assert web._show_unanalyzed(_request(cookies={UNANALYZED_COOKIE: "1"})) is True
        assert web._show_unanalyzed(_request(cookies={UNANALYZED_COOKIE: "0"})) is False

    def test_query_param_overrides_cookie(self):
        shown = {UNANALYZED_COOKIE: "1"}
        assert web._show_unanalyzed(_request({"unanalyzed": "hide"}, shown)) is False
        assert web._show_unanalyzed(_request({"unanalyzed": "show"})) is True
        assert web._show_unanalyzed(_request({"unanalyzed": "bogus"}, shown)) is True


class TestQueries:
    def test_predicate_keys_on_analysis_state_not_source(self):
        sql = analyzed_sql("lgm.document_id")
        assert "document_analysis_state" in sql
        assert "das.document_id = lgm.document_id" in sql
        assert "completed_at IS NOT NULL" in sql
        assert "profile_key" not in sql  # any model counts

    def test_source_lessons_flag_analyzed(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.get_source_lessons(4)
        assert "as analyzed" in mock_db._mock_cursor.execute.call_args[0][0]

    def test_groups_count_analyzed_members(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.get_lesson_groups()
        assert "as analyzed_count" in mock_db._mock_cursor.execute.call_args[0][0]

    def test_group_members_flag_analyzed(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.get_group_members(5)
        assert "as analyzed" in mock_db._mock_cursor.execute.call_args[0][0]


def _listing_db():
    db = MagicMock()
    db.get_lesson_categories.return_value = [
        {"source_id": 10, "source_name": "ATLAS_X", "source_category_name": "Atlas X"},
        {"source_id": 11, "source_name": "MIXED", "source_category_name": None},
    ]
    db.get_lessons_by_source.return_value = {
        10: [{"id": "a1", "title": "Atlas lesson", "analyzed": False}],
        11: [{"id": "m1", "title": "Analyzed", "analyzed": True},
             {"id": "m2", "title": "Not yet", "analyzed": False}],
    }
    return db


class TestHomePage:
    def test_listing_drops_transcript_only_and_counts_them(self):
        with patch.object(web, "_db", _listing_db()):
            categories, names, hidden = web.get_lesson_listing(include_unanalyzed=False)
        assert hidden == 2
        assert list(categories) == ["db:11"]  # the all-Atlas source vanishes
        assert [lesson[0] for lesson in categories["db:11"]] == ["m1"]

    def test_listing_can_include_them(self):
        with patch.object(web, "_db", _listing_db()):
            categories, _, count = web.get_lesson_listing(include_unanalyzed=True)
        assert count == 2
        assert sum(len(v) for v in categories.values()) == 3

    def test_legacy_shape_is_unchanged(self):
        with patch.object(web, "_db", _listing_db()):
            result = web.get_available_lessons()
        assert len(result) == 2

    def test_page_says_what_is_hidden(self):
        with patch.object(web, "_db", _listing_db()):
            page = build_search_home_html(get_lessons_fn=web.get_lesson_listing)
        assert "Hiding 2 transcript-only lessons" in page
        assert "Show them" in page
        assert "Atlas lesson" not in page
        assert "All Lessons (1)" in page

    def test_page_marks_them_when_shown(self):
        with patch.object(web, "_db", _listing_db()):
            page = build_search_home_html(get_lessons_fn=web.get_lesson_listing,
                                          include_unanalyzed=True)
        assert "Hide them" in page
        assert "Atlas lesson" in page
        assert page.count(">transcript only</span>") == 2

    def test_no_notice_when_nothing_is_transcript_only(self):
        assert unanalyzed_filter_html(0, showing=False) == ""


def _analysis_db():
    db = MagicMock()
    db.get_analysis_steps.return_value = [
        {"name": "decompose", "current_version": 1, "depends_on": [], "description": ""}]
    db.get_group_dashboard.return_value = [
        {"id": 1, "name": "Mixed group", "doc_count": 3, "analyzed_count": 1,
         "gates": {"decompose": True}},
        {"id": 2, "name": "Atlas only group", "doc_count": 4, "analyzed_count": 0,
         "gates": {"decompose": False}},
    ]
    db.count_runnable_steps.return_value = 0
    db.get_lesson_group.return_value = {"id": 1, "name": "Mixed group"}
    db.get_document_dashboard.return_value = [
        {"document_id": "d1", "title": "Analyzed lesson", "analyzed": True,
         "gates": {"decompose": {"state": "current", "completed_version": 1}}},
        {"document_id": "d2", "title": "Transcript lesson", "analyzed": False,
         "gates": {"decompose": {"state": "inactive"}}},
    ]
    return db


class TestAnalysisDashboard:
    def _groups(self, include):
        with patch.object(web, "_db", _analysis_db()), \
             patch.object(html_builders, "_build_profiles_panel", return_value=""), \
             patch.object(html_builders, "_build_atlas_projection_panel", return_value=""):
            return html_builders._build_analysis_html("gemini-3-flash", include)

    def test_groups_hide_transcript_only(self):
        page = self._groups(False)
        assert "Mixed group" in page
        assert "Atlas only group" not in page
        assert "Hiding 6 transcript-only lessons" in page
        assert "1 groups hold only transcript-only lessons" in page

    def test_groups_can_show_them(self):
        page = self._groups(True)
        assert "Atlas only group" in page
        assert "Hide them" in page

    def test_group_documents_hide_transcript_only(self):
        with patch.object(web, "_db", _analysis_db()):
            hidden = html_builders._build_analysis_group_html(1, "gemini-3-flash", False)
            shown = html_builders._build_analysis_group_html(1, "gemini-3-flash", True)
        assert "Transcript lesson" not in hidden
        assert "Hiding 1 transcript-only lesson:" in hidden
        assert "Transcript lesson" in shown and ">transcript only</span>" in shown


class TestHomeListing:
    def test_one_query_for_all_sources(self):
        db = _listing_db()
        with patch.object(web, "_db", db):
            web.get_lesson_listing()
        db.get_lessons_by_source.assert_called_once_with([10, 11])
        db.get_source_lessons.assert_not_called()

    def test_series_keep_their_order(self):
        db = MagicMock()
        db.get_lesson_categories.return_value = [
            {"source_id": 5, "source_name": "S", "source_category_name": None}]
        db.get_lessons_by_source.return_value = {5: [
            {"id": t, "title": t, "analyzed": True}
            for t in ["Pelvic clock part 10", "Pelvic clock part 2", "Pelvic clock part 1"]]}
        with patch.object(web, "_db", db):
            categories, _, _ = web.get_lesson_listing()
        assert [lesson[0] for lesson in categories["db:5"]] == [
            "Pelvic clock part 1", "Pelvic clock part 2", "Pelvic clock part 10"]

    def test_page_has_title_filter_and_search_restore(self):
        with patch.object(web, "_db", _listing_db()):
            page = build_search_home_html(get_lessons_fn=web.get_lesson_listing)
        assert 'id="lesson-filter"' in page
        assert "restoreSearch" in page
        assert '"db:11": "Mixed"' in page
