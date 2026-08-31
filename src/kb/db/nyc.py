"""NYC directory administration data access (PostgreSQL schema ``atlas_nyc``).

The Mac Mini crawler owns discovery and enrichment; this module is the read
side of the human control plane plus the one mutation the site is allowed to
make today — the A/B/C/D review decision, which mirrors the Atlas
``nyc-review`` contract exactly (lock, capture previous state, update
``crawl_state``, insert ``nyc_directory_decision``, commit).

Boundaries enforced here:

* every statement is schema-qualified with ``atlas_nyc``; nothing relies on
  ``search_path``;
* no DDL — Atlas migrations own this schema. :meth:`nyc_schema_status` reports
  a missing/partial schema so the site can show an admin diagnostic instead of
  creating tables;
* JSON columns in this schema are ``json`` (not ``jsonb``), so merges read,
  mutate in Python, and write back with an explicit ``::json`` cast rather than
  using jsonb operators.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

NYC_SCHEMA = "atlas_nyc"

# Tables this feature depends on. A missing entry is an admin-visible
# diagnostic, never an attempt to create it.
NYC_REQUIRED_TABLES = (
    "nyc_provider",
    "nyc_venue",
    "nyc_offering",
    "nyc_occurrence",
    "nyc_event_evidence",
    "nyc_directory_decision",
    "nyc_discovery_run",
    "nyc_discovery_observation",
    "discovery_task",
)

# Optional: crawler telemetry and expedited-check status. Both are absent in
# older Atlas migrations, so the console degrades to "not recorded" rather than
# failing, and never creates them itself.
NYC_OPTIONAL_TABLES = ("fetch_log", "nyc_crawl_request")

# atlas_nyc.nyc_crawl_request is written by Atlas/the Mini and has no migration
# here yet, so its columns are discovered rather than assumed. Each row below is
# a preference order; the first column present wins.
_CRAWL_REQUEST_COLUMNS = {
    "provider": ("provider_id", "entity_id", "provider"),
    "status": ("status", "state"),
    "request_id": ("request_id", "id"),
    "requested_at": ("requested_at", "created_at", "updated_at", "observed_at"),
    "actor": ("requested_by", "actor"),
    "detail": ("last_error", "detail", "error", "message", "note"),
    "object_key": ("object_key",),
    "attempts": ("attempts",),
}

NYC_TASK_TYPE = "nyc_schedule_review"

# Mirrors feldenkrais_index.nyc_events.NYC_TIER_INTERVAL_DAYS.
NYC_TIER_INTERVAL_DAYS = {"A": 1, "B": 7, "C": 30}

NYC_TIERS = ("A", "B", "C", "D")

# Bounded excerpt shown to admins on the task screen.
NYC_TASK_EXCERPT_CHARS = 4000


class NycAdminError(Exception):
    """Base class for NYC admin failures that the UI renders as messages."""


class NycSchemaMissingError(NycAdminError):
    """The atlas_nyc schema is absent or incomplete."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("missing atlas_nyc tables: " + ", ".join(missing))


class NycNotFoundError(NycAdminError):
    """No such provider, venue, run, or task."""


class NycStaleFormError(NycAdminError):
    """Another writer changed the row after the form was rendered."""

    def __init__(self, current: dict):
        self.current = current
        super().__init__("row changed since the form was loaded")


# ── SQL fragments ────────────────────────────────────────────────────────

# nyc_entry_tier(): crawl_state.tier upper-cased, default and fallback "C".
def _tier_sql(alias: str) -> str:
    raw = f"upper(coalesce({alias}.crawl_state->>'tier', 'C'))"
    return f"CASE WHEN {raw} IN ('A','B','C','D') THEN {raw} ELSE 'C' END"


# nyc_entry_is_due(): active, enabled, tier != D, and older than its interval.
def _due_sql(alias: str, *, enabled: bool) -> str:
    enabled_clause = f"{alias}.enabled AND " if enabled else ""
    return f"""(
        {alias}.status = 'active' AND {enabled_clause}{alias}.tier <> 'D' AND (
            {alias}.last_crawled_at IS NULL
            OR now() - {alias}.last_crawled_at >= (CASE {alias}.tier
                WHEN 'A' THEN INTERVAL '1 day'
                WHEN 'B' THEN INTERVAL '7 days'
                ELSE INTERVAL '30 days' END)
        )
    )"""


_OCCURRENCE_BUCKET_SQL = """
    CASE
        WHEN c.status = 'cancelled' THEN 'cancelled'
        WHEN c.status = 'sold_out' THEN 'sold_out'
        WHEN c.missing_since IS NOT NULL THEN 'unconfirmed'
        WHEN c.starts_at < now() THEN 'expired'
        ELSE 'confirmed_upcoming'
    END
"""

# An offering with no dated occurrence is never synthesized into one. It is
# either an honest undated recurring offering (it says how it recurs) or it is
# a record a human needs to look at.
_OFFERING_BUCKET_SQL = """
    CASE
        WHEN occ.total > 0 THEN 'dated'
        WHEN o.recurrence_text IS NOT NULL AND btrim(o.recurrence_text) <> ''
            THEN 'undated_recurring'
        ELSE 'needs_review'
    END
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _same_timestamp(actual, expected: str | None) -> bool:
    """Compare a row timestamp against the value carried by a form."""
    if not expected:
        return False
    actual = _as_aware(actual)
    try:
        parsed = _as_aware(datetime.fromisoformat(str(expected)))
    except ValueError:
        return False
    if actual is None or parsed is None:
        return False
    # Round-tripping through the form can lose sub-microsecond precision.
    return abs((actual - parsed).total_seconds()) < 0.001


def _venue_match_clause(prefix: str = "") -> str:
    """Offerings belong to a venue when name+address match its identity.

    Mirrors ``_venue_key`` (sha256 of casefolded name + NUL + address) without
    needing pgcrypto: the same equality, expressed directly.
    """
    return f"""
        lower(btrim({prefix}venue_name)) = lower(btrim(%s))
        AND coalesce(lower(btrim({prefix}address_text)), '')
            = coalesce(lower(btrim(%s)), '')
    """


class NycAdminMixin:
    """Read adapters and the audited review mutation for ``atlas_nyc``."""

    # ── Schema guard ─────────────────────────────────────────────────────

    def nyc_schema_status(self) -> dict:
        """Which expected tables exist. Never creates anything."""
        with self.get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s",
                (NYC_SCHEMA,),
            )
            present = {r["table_name"] for r in cur.fetchall()}
        missing = [t for t in NYC_REQUIRED_TABLES if t not in present]
        return {
            "schema": NYC_SCHEMA,
            "ok": not missing,
            "present": sorted(present),
            "missing": missing,
            "optional_missing": [t for t in NYC_OPTIONAL_TABLES if t not in present],
        }

    def nyc_require_schema(self) -> dict:
        status = self.nyc_schema_status()
        if not status["ok"]:
            raise NycSchemaMissingError(status["missing"])
        return status

    # ── Overview ─────────────────────────────────────────────────────────

    def nyc_overview(self, *, upcoming_limit: int = 12) -> dict:
        status = self.nyc_require_schema()
        has_fetch_log = "fetch_log" not in status["optional_missing"]
        prov_tier = _tier_sql("p")
        venue_tier = _tier_sql("v")

        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                WITH p AS (
                    SELECT p.*, {prov_tier} AS tier FROM {NYC_SCHEMA}.nyc_provider p
                )
                SELECT status, tier,
                       count(*) AS total,
                       count(*) FILTER (WHERE {_due_sql('p', enabled=True)}) AS due
                FROM p GROUP BY status, tier ORDER BY status, tier
            """)
            provider_rows = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                WITH v AS (
                    SELECT v.*, {venue_tier} AS tier FROM {NYC_SCHEMA}.nyc_venue v
                )
                SELECT status, tier,
                       count(*) AS total,
                       count(*) FILTER (WHERE {_due_sql('v', enabled=False)}) AS due
                FROM v GROUP BY status, tier ORDER BY status, tier
            """)
            venue_rows = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_offering) AS offerings,
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_offering
                        WHERE status = 'active') AS offerings_active,
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence) AS occurrences,
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence
                        WHERE starts_at >= now() AND missing_since IS NULL
                          AND status IN ('scheduled', 'sold_out')) AS occurrences_upcoming,
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence
                        WHERE missing_since IS NOT NULL) AS occurrences_unconfirmed,
                    (SELECT count(*) FROM {NYC_SCHEMA}.discovery_task
                        WHERE task_type = %s AND status IN ('queued', 'running')) AS tasks_open,
                    (SELECT count(*) FROM {NYC_SCHEMA}.discovery_task
                        WHERE task_type = %s AND status = 'failed') AS tasks_failed,
                    (SELECT count(*) FROM {NYC_SCHEMA}.nyc_event_evidence) AS evidence
            """, (NYC_TASK_TYPE, NYC_TASK_TYPE))
            counts = dict(cur.fetchone())

            cur.execute(f"""
                SELECT * FROM {NYC_SCHEMA}.nyc_discovery_run
                ORDER BY started_at DESC LIMIT 5
            """)
            runs = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                WITH p AS (
                    SELECT p.*, {prov_tier} AS tier FROM {NYC_SCHEMA}.nyc_provider p
                )
                SELECT id, name, canonical_url, tier, last_crawled_at
                FROM p WHERE {_due_sql('p', enabled=True)}
                ORDER BY tier, last_crawled_at NULLS FIRST, name
            """)
            providers_due = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                WITH v AS (
                    SELECT v.*, {venue_tier} AS tier FROM {NYC_SCHEMA}.nyc_venue v
                )
                SELECT id, name, canonical_url, tier, last_crawled_at
                FROM v WHERE {_due_sql('v', enabled=False)}
                ORDER BY tier, last_crawled_at NULLS FIRST, name
            """)
            venues_due = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT c.id, c.starts_at, c.status, c.registration_url,
                       c.last_confirmed_at,
                       o.id AS offering_id, o.title, o.teacher, o.venue_name, o.area,
                       p.id AS provider_id, p.name AS provider_name
                FROM {NYC_SCHEMA}.nyc_occurrence c
                JOIN {NYC_SCHEMA}.nyc_offering o ON o.id = c.offering_id
                JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                WHERE c.starts_at >= now() AND c.missing_since IS NULL
                  AND c.status IN ('scheduled', 'sold_out')
                ORDER BY c.starts_at
                LIMIT %s
            """, (upcoming_limit,))
            upcoming = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT o.id, o.title, o.recurrence_text, o.venue_name, o.area,
                       p.id AS provider_id, p.name AS provider_name
                FROM {NYC_SCHEMA}.nyc_offering o
                JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                LEFT JOIN {NYC_SCHEMA}.nyc_occurrence c ON c.offering_id = o.id
                WHERE o.status = 'active'
                GROUP BY o.id, o.title, o.recurrence_text, o.venue_name, o.area,
                         p.id, p.name
                HAVING count(c.id) = 0
                   AND (o.recurrence_text IS NULL OR btrim(o.recurrence_text) = '')
                ORDER BY o.updated_at DESC
                LIMIT %s
            """, (upcoming_limit,))
            offerings_needing_review = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT id, name, canonical_url, created_at, updated_at
                FROM {NYC_SCHEMA}.nyc_provider
                WHERE status = 'candidate'
                ORDER BY created_at DESC
            """)
            provider_candidates = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT id, name, canonical_url, area, status, created_at, updated_at
                FROM {NYC_SCHEMA}.nyc_venue
                WHERE status IN ('candidate', 'observed')
                ORDER BY created_at DESC
            """)
            venue_candidates = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT d.*, COALESCE(p.name, v.name) AS entity_name
                FROM {NYC_SCHEMA}.nyc_directory_decision d
                LEFT JOIN {NYC_SCHEMA}.nyc_provider p
                       ON d.entity_type = 'provider' AND p.id = d.entity_id
                LEFT JOIN {NYC_SCHEMA}.nyc_venue v
                       ON d.entity_type = 'venue' AND v.id = d.entity_id
                ORDER BY d.decided_at DESC LIMIT 10
            """)
            recent_decisions = [dict(r) for r in cur.fetchall()]

            crawler = None
            if has_fetch_log:
                cur.execute(f"""
                    SELECT
                        interval_label,
                        count(*) AS checks,
                        count(*) FILTER (
                            WHERE error IS NOT NULL OR status_code >= 400
                        ) AS failures
                    FROM (
                        SELECT '1h' AS interval_label, error, status_code
                        FROM {NYC_SCHEMA}.fetch_log
                        WHERE fetched_at >= now() - INTERVAL '1 hour'
                        UNION ALL
                        SELECT '24h', error, status_code FROM {NYC_SCHEMA}.fetch_log
                        WHERE fetched_at >= now() - INTERVAL '24 hours'
                        UNION ALL
                        SELECT '7d', error, status_code FROM {NYC_SCHEMA}.fetch_log
                        WHERE fetched_at >= now() - INTERVAL '7 days'
                    ) w
                    GROUP BY interval_label
                """)
                windows = {r["interval_label"]: dict(r) for r in cur.fetchall()}
                crawler = {
                    label: {
                        "checks": windows.get(label, {}).get("checks", 0),
                        "failures": windows.get(label, {}).get("failures", 0),
                    }
                    for label in ("1h", "24h", "7d")
                }

                cur.execute(f"""
                    SELECT url, status_code, error, fetched_at
                    FROM {NYC_SCHEMA}.fetch_log
                    WHERE (error IS NOT NULL OR status_code >= 400)
                      AND fetched_at >= now() - INTERVAL '7 days'
                    ORDER BY fetched_at DESC LIMIT 15
                """)
                recent_failures = [dict(r) for r in cur.fetchall()]
            else:
                recent_failures = []

            cur.execute(f"""
                SELECT id, status, attempts, error, created_at, finished_at,
                       payload->>'provider_name' AS provider_name,
                       payload->>'source_url' AS source_url
                FROM {NYC_SCHEMA}.discovery_task
                WHERE task_type = %s AND status IN ('queued', 'running', 'failed')
                ORDER BY created_at
                LIMIT 15
            """, (NYC_TASK_TYPE,))
            open_tasks = [dict(r) for r in cur.fetchall()]

        return {
            "schema": status,
            "counts": counts,
            "providers_by_state": provider_rows,
            "venues_by_state": venue_rows,
            "providers_due": providers_due,
            "venues_due": venues_due,
            "runs": runs,
            "upcoming": upcoming,
            "offerings_needing_review": offerings_needing_review,
            "provider_candidates": provider_candidates,
            "venue_candidates": venue_candidates,
            "recent_decisions": recent_decisions,
            "crawler": crawler,
            "recent_failures": recent_failures,
            "open_tasks": open_tasks,
            "generated_at": _now(),
        }

    # ── Providers ────────────────────────────────────────────────────────

    def nyc_providers(
        self,
        *,
        tier: str | None = None,
        status: str | None = None,
        area: str | None = None,
        due: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        self.nyc_require_schema()
        where, params = [], []
        if tier:
            where.append("p.tier = %s")
            params.append(tier.upper())
        if status:
            where.append("p.status = %s")
            params.append(status)
        if area:
            where.append("%s = ANY(coalesce(off.areas, ARRAY[]::text[]))")
            params.append(area)
        if due == "due":
            where.append("p.is_due")
        elif due == "not_due":
            where.append("NOT p.is_due")
        if q:
            where.append("(p.name ILIKE %s OR p.canonical_url ILIKE %s)")
            params.extend([f"%{q}%", f"%{q}%"])
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        sql = f"""
            WITH p AS (
                SELECT p.*, {_tier_sql('p')} AS tier FROM {NYC_SCHEMA}.nyc_provider p
            ),
            p2 AS (
                SELECT p.*, {_due_sql('p', enabled=True)} AS is_due FROM p
            ),
            off AS (
                SELECT provider_id,
                       count(*) AS offering_count,
                       count(*) FILTER (WHERE status = 'active') AS active_offerings,
                       array_remove(array_agg(DISTINCT area), NULL) AS areas
                FROM {NYC_SCHEMA}.nyc_offering GROUP BY provider_id
            ),
            occ AS (
                SELECT o.provider_id,
                       count(*) FILTER (
                           WHERE c.starts_at >= now() AND c.missing_since IS NULL
                             AND c.status IN ('scheduled', 'sold_out')
                       ) AS upcoming_occurrences,
                       max(c.last_confirmed_at) AS last_confirmed_at
                FROM {NYC_SCHEMA}.nyc_offering o
                JOIN {NYC_SCHEMA}.nyc_occurrence c ON c.offering_id = o.id
                GROUP BY o.provider_id
            ),
            ev AS (
                SELECT provider_id, count(*) AS evidence_count,
                       max(observed_at) AS last_evidence_at
                FROM {NYC_SCHEMA}.nyc_event_evidence GROUP BY provider_id
            ),
            obs AS (
                SELECT provider_id, count(*) AS observation_count,
                       max(observed_at) AS last_observed_at
                FROM {NYC_SCHEMA}.nyc_discovery_observation
                WHERE provider_id IS NOT NULL GROUP BY provider_id
            ),
            tsk AS (
                SELECT payload->>'provider_id' AS provider_id,
                       count(*) FILTER (WHERE status IN ('queued', 'running')) AS open_tasks,
                       count(*) FILTER (WHERE status = 'failed') AS failed_tasks,
                       max(finished_at) AS last_task_at
                FROM {NYC_SCHEMA}.discovery_task
                WHERE task_type = %s GROUP BY 1
            ),
            dec AS (
                SELECT DISTINCT ON (entity_id) entity_id, decision, tier AS decided_tier,
                       decided_at, actor
                FROM {NYC_SCHEMA}.nyc_directory_decision
                WHERE entity_type = 'provider'
                ORDER BY entity_id, decided_at DESC
            )
            SELECT p.id, p.name, p.canonical_url, p.schedule_urls, p.status, p.enabled,
                   p.tier, p.is_due, p.crawl_state, p.last_crawled_at,
                   p.created_at, p.updated_at,
                   coalesce(off.offering_count, 0) AS offering_count,
                   coalesce(off.active_offerings, 0) AS active_offerings,
                   coalesce(off.areas, ARRAY[]::text[]) AS areas,
                   coalesce(occ.upcoming_occurrences, 0) AS upcoming_occurrences,
                   occ.last_confirmed_at,
                   coalesce(ev.evidence_count, 0) AS evidence_count,
                   ev.last_evidence_at,
                   coalesce(obs.observation_count, 0) AS observation_count,
                   obs.last_observed_at,
                   coalesce(tsk.open_tasks, 0) AS open_tasks,
                   coalesce(tsk.failed_tasks, 0) AS failed_tasks,
                   tsk.last_task_at,
                   dec.decision AS last_decision, dec.decided_at AS last_decided_at,
                   dec.actor AS last_decided_by
            FROM p2 p
            LEFT JOIN off ON off.provider_id = p.id
            LEFT JOIN occ ON occ.provider_id = p.id
            LEFT JOIN ev ON ev.provider_id = p.id
            LEFT JOIN obs ON obs.provider_id = p.id
            LEFT JOIN tsk ON tsk.provider_id = p.id
            LEFT JOIN dec ON dec.entity_id = p.id
            {clause}
            ORDER BY
                CASE p.status WHEN 'candidate' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                p.tier, p.name
        """
        with self.get_cursor(commit=False) as cur:
            cur.execute(sql, [NYC_TASK_TYPE, *params])
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            row["new_evidence"] = _new_evidence_count(row)
        return rows

    def nyc_provider_detail(self, provider_id: str) -> dict:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT p.*, {_tier_sql('p')} AS tier
                FROM {NYC_SCHEMA}.nyc_provider p WHERE p.id = %s
            """, (provider_id,))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"provider not found: {provider_id}")
            provider = dict(row)

            cur.execute(f"""
                SELECT o.*, (
                    SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence c
                    WHERE c.offering_id = o.id
                ) AS occurrence_count, (
                    SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence c
                    WHERE c.offering_id = o.id AND c.starts_at >= now()
                      AND c.missing_since IS NULL
                      AND c.status IN ('scheduled', 'sold_out')
                ) AS upcoming_count
                FROM {NYC_SCHEMA}.nyc_offering o
                WHERE o.provider_id = %s ORDER BY o.updated_at DESC
            """, (provider_id,))
            offerings = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT d.* FROM {NYC_SCHEMA}.nyc_directory_decision d
                WHERE d.entity_type = 'provider' AND d.entity_id = %s
                ORDER BY d.decided_at DESC
            """, (provider_id,))
            decisions = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT o.*, r.started_at AS run_started_at, r.status AS run_status
                FROM {NYC_SCHEMA}.nyc_discovery_observation o
                LEFT JOIN {NYC_SCHEMA}.nyc_discovery_run r ON r.id = o.run_id
                WHERE o.provider_id = %s ORDER BY o.observed_at DESC LIMIT 50
            """, (provider_id,))
            observations = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT id, source_url, content_hash, extraction_method, observed_at,
                       facts, offering_id, occurrence_id
                FROM {NYC_SCHEMA}.nyc_event_evidence
                WHERE provider_id = %s ORDER BY observed_at DESC LIMIT 50
            """, (provider_id,))
            evidence = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT id, status, attempts, error, created_at, started_at, finished_at,
                       payload->>'source_url' AS source_url, result
                FROM {NYC_SCHEMA}.discovery_task
                WHERE task_type = %s AND payload->>'provider_id' = %s
                ORDER BY created_at DESC LIMIT 25
            """, (NYC_TASK_TYPE, provider_id))
            tasks = [dict(r) for r in cur.fetchall()]

        provider["is_due"] = _is_due(provider, enabled_field=True)
        provider["new_evidence"] = _new_evidence_count(
            {"crawl_state": provider.get("crawl_state"),
             "last_observed_at": observations[0]["observed_at"] if observations else None}
        )
        return {
            "provider": provider,
            "offerings": offerings,
            "decisions": decisions,
            "observations": observations,
            "evidence": evidence,
            "tasks": tasks,
        }

    # ── Venues ───────────────────────────────────────────────────────────

    def nyc_venues(
        self,
        *,
        tier: str | None = None,
        status: str | None = None,
        area: str | None = None,
        due: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        self.nyc_require_schema()
        where, params = [], []
        if tier:
            where.append("v.tier = %s")
            params.append(tier.upper())
        if status:
            where.append("v.status = %s")
            params.append(status)
        if area:
            where.append("v.area = %s")
            params.append(area)
        if due == "due":
            where.append("v.is_due")
        elif due == "not_due":
            where.append("NOT v.is_due")
        if q:
            where.append(
                "(v.name ILIKE %s OR coalesce(v.canonical_url, '') ILIKE %s"
                " OR coalesce(v.address_text, '') ILIKE %s)"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        sql = f"""
            WITH v0 AS (
                SELECT v.*, {_tier_sql('v')} AS tier FROM {NYC_SCHEMA}.nyc_venue v
            ),
            v AS (
                SELECT v.*, {_due_sql('v', enabled=False)} AS is_due FROM v0 v
            ),
            dec AS (
                SELECT DISTINCT ON (entity_id) entity_id, decision, decided_at, actor
                FROM {NYC_SCHEMA}.nyc_directory_decision
                WHERE entity_type = 'venue' ORDER BY entity_id, decided_at DESC
            ),
            off AS (
                SELECT lower(btrim(venue_name)) AS vname,
                       coalesce(lower(btrim(address_text)), '') AS vaddr,
                       count(*) AS offering_count,
                       count(DISTINCT provider_id) AS provider_count
                FROM {NYC_SCHEMA}.nyc_offering
                WHERE venue_name IS NOT NULL GROUP BY 1, 2
            )
            SELECT v.id, v.name, v.canonical_url, v.schedule_urls, v.address_text,
                   v.area, v.status, v.tier, v.is_due, v.crawl_state,
                   v.last_crawled_at, v.created_at, v.updated_at,
                   coalesce(off.offering_count, 0) AS offering_count,
                   coalesce(off.provider_count, 0) AS provider_count,
                   dec.decision AS last_decision, dec.decided_at AS last_decided_at,
                   dec.actor AS last_decided_by
            FROM v
            LEFT JOIN off ON off.vname = lower(btrim(v.name))
                         AND off.vaddr = coalesce(lower(btrim(v.address_text)), '')
            LEFT JOIN dec ON dec.entity_id = v.id
            {clause}
            ORDER BY
                CASE v.status WHEN 'candidate' THEN 0 WHEN 'observed' THEN 1
                              WHEN 'active' THEN 2 ELSE 3 END,
                v.tier, v.name
        """
        with self.get_cursor(commit=False) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def nyc_venue_detail(self, venue_id: str) -> dict:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT v.*, {_tier_sql('v')} AS tier
                FROM {NYC_SCHEMA}.nyc_venue v WHERE v.id = %s
            """, (venue_id,))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"venue not found: {venue_id}")
            venue = dict(row)

            cur.execute(f"""
                SELECT o.id, o.title, o.teacher, o.area, o.status, o.recurrence_text,
                       o.registration_url, o.updated_at,
                       p.id AS provider_id, p.name AS provider_name, p.status AS provider_status,
                       (SELECT count(*) FROM {NYC_SCHEMA}.nyc_occurrence c
                        WHERE c.offering_id = o.id AND c.starts_at >= now()
                          AND c.missing_since IS NULL
                          AND c.status IN ('scheduled', 'sold_out')) AS upcoming_count
                FROM {NYC_SCHEMA}.nyc_offering o
                JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                WHERE {_venue_match_clause('o.')}
                ORDER BY o.updated_at DESC
            """, (venue["name"], venue.get("address_text")))
            offerings = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT * FROM {NYC_SCHEMA}.nyc_directory_decision
                WHERE entity_type = 'venue' AND entity_id = %s
                ORDER BY decided_at DESC
            """, (venue_id,))
            decisions = [dict(r) for r in cur.fetchall()]

        venue["is_due"] = _is_due(venue, enabled_field=False)
        providers = {}
        for off in offerings:
            providers.setdefault(off["provider_id"], {
                "id": off["provider_id"],
                "name": off["provider_name"],
                "status": off["provider_status"],
                "offerings": 0,
            })["offerings"] += 1
        return {
            "venue": venue,
            "offerings": offerings,
            "providers": list(providers.values()),
            "decisions": decisions,
        }

    # ── Events ───────────────────────────────────────────────────────────

    def nyc_occurrences(
        self,
        *,
        bucket: str | None = None,
        provider_id: str | None = None,
        area: str | None = None,
        q: str | None = None,
        limit: int = 300,
    ) -> list[dict]:
        self.nyc_require_schema()
        where, params = [], []
        if provider_id:
            where.append("p.id = %s")
            params.append(provider_id)
        if area:
            where.append("o.area = %s")
            params.append(area)
        if q:
            where.append("(o.title ILIKE %s OR coalesce(o.teacher, '') ILIKE %s"
                         " OR coalesce(o.venue_name, '') ILIKE %s)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        having = ""
        if bucket:
            having = "WHERE bucket = %s"

        sql = f"""
            SELECT * FROM (
                SELECT c.id, c.starts_at, c.ends_at, c.status, c.registration_url,
                       c.last_confirmed_at, c.missing_since, c.source_timezone,
                       {_OCCURRENCE_BUCKET_SQL} AS bucket,
                       o.id AS offering_id, o.title, o.teacher, o.venue_name,
                       o.address_text, o.area, o.event_kind, o.price_text,
                       o.provenance->>'method' AS extraction_method,
                       p.id AS provider_id, p.name AS provider_name,
                       (SELECT count(*) FROM {NYC_SCHEMA}.nyc_event_evidence e
                        WHERE e.occurrence_id = c.id) AS evidence_count
                FROM {NYC_SCHEMA}.nyc_occurrence c
                JOIN {NYC_SCHEMA}.nyc_offering o ON o.id = c.offering_id
                JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                {clause}
            ) rows
            {having}
            ORDER BY starts_at DESC
            LIMIT %s
        """
        if bucket:
            params.append(bucket)
        params.append(limit)
        with self.get_cursor(commit=False) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def nyc_offerings(
        self,
        *,
        bucket: str | None = None,
        provider_id: str | None = None,
        area: str | None = None,
        q: str | None = None,
        limit: int = 300,
    ) -> list[dict]:
        """Offering-level view. ``bucket`` is dated / undated_recurring / needs_review."""
        self.nyc_require_schema()
        where, params = [], []
        if provider_id:
            where.append("o.provider_id = %s")
            params.append(provider_id)
        if area:
            where.append("o.area = %s")
            params.append(area)
        if q:
            where.append("(o.title ILIKE %s OR coalesce(o.teacher, '') ILIKE %s"
                         " OR coalesce(o.venue_name, '') ILIKE %s)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        having = "WHERE bucket = %s" if bucket else ""

        sql = f"""
            SELECT * FROM (
                SELECT o.id, o.title, o.teacher, o.venue_name, o.address_text, o.area,
                       o.event_kind, o.price_text, o.recurrence_text,
                       o.registration_url, o.status, o.updated_at,
                       o.provenance->>'method' AS extraction_method,
                       o.provenance->>'url' AS provenance_url,
                       p.id AS provider_id, p.name AS provider_name,
                       coalesce(occ.total, 0) AS occurrence_count,
                       coalesce(occ.upcoming, 0) AS upcoming_count,
                       occ.last_confirmed_at,
                       {_OFFERING_BUCKET_SQL} AS bucket
                FROM {NYC_SCHEMA}.nyc_offering o
                JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                LEFT JOIN (
                    SELECT offering_id, count(*) AS total,
                           count(*) FILTER (
                               WHERE starts_at >= now() AND missing_since IS NULL
                                 AND status IN ('scheduled', 'sold_out')
                           ) AS upcoming,
                           max(last_confirmed_at) AS last_confirmed_at
                    FROM {NYC_SCHEMA}.nyc_occurrence GROUP BY offering_id
                ) occ ON occ.offering_id = o.id
                {clause}
            ) rows
            {having}
            ORDER BY updated_at DESC
            LIMIT %s
        """
        if bucket:
            params.append(bucket)
        params.append(limit)
        with self.get_cursor(commit=False) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def nyc_event_areas(self) -> list[str]:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT DISTINCT area FROM {NYC_SCHEMA}.nyc_offering
                WHERE area IS NOT NULL
                UNION
                SELECT DISTINCT area FROM {NYC_SCHEMA}.nyc_venue WHERE area IS NOT NULL
                ORDER BY 1
            """)
            return [r["area"] for r in cur.fetchall()]

    def nyc_evidence_for(self, *, offering_id: str | None = None,
                         occurrence_id: str | None = None, limit: int = 50) -> list[dict]:
        self.nyc_require_schema()
        if not offering_id and not occurrence_id:
            return []
        where, params = [], []
        if offering_id:
            where.append("offering_id = %s")
            params.append(offering_id)
        if occurrence_id:
            where.append("occurrence_id = %s")
            params.append(occurrence_id)
        params.append(limit)
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT id, provider_id, offering_id, occurrence_id, source_url,
                       content_hash, extraction_method, observed_at, facts
                FROM {NYC_SCHEMA}.nyc_event_evidence
                WHERE {' OR '.join(where)}
                ORDER BY observed_at DESC LIMIT %s
            """, params)
            return [dict(r) for r in cur.fetchall()]

    # ── Discovery ────────────────────────────────────────────────────────

    def nyc_discovery_runs(self, limit: int = 50) -> list[dict]:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT r.*,
                       (SELECT count(*) FROM {NYC_SCHEMA}.nyc_discovery_observation o
                        WHERE o.run_id = r.id) AS observation_count,
                       (SELECT count(DISTINCT o.query) FROM {NYC_SCHEMA}.nyc_discovery_observation o
                        WHERE o.run_id = r.id) AS query_count,
                       extract(epoch FROM (coalesce(r.finished_at, now()) - r.started_at))
                           AS duration_seconds
                FROM {NYC_SCHEMA}.nyc_discovery_run r
                ORDER BY r.started_at DESC LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    def nyc_discovery_run_detail(self, run_id: str,
                                 *, disposition: str | None = None) -> dict:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT * FROM {NYC_SCHEMA}.nyc_discovery_run WHERE id = %s
            """, (run_id,))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"discovery run not found: {run_id}")
            run = dict(row)

            params = [run_id]
            clause = ""
            if disposition:
                clause = "AND o.disposition = %s"
                params.append(disposition)
            cur.execute(f"""
                SELECT o.*, p.name AS provider_name, p.status AS provider_status
                FROM {NYC_SCHEMA}.nyc_discovery_observation o
                LEFT JOIN {NYC_SCHEMA}.nyc_provider p ON p.id = o.provider_id
                WHERE o.run_id = %s {clause}
                ORDER BY o.query, o.rank
            """, params)
            observations = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT query,
                       count(*) AS results,
                       count(*) FILTER (WHERE disposition = 'rejected') AS rejected,
                       count(*) FILTER (WHERE disposition LIKE '%%candidate%%') AS candidates,
                       count(*) FILTER (WHERE disposition = 'known_provider_observed')
                           AS known,
                       count(DISTINCT provider_id) AS providers
                FROM {NYC_SCHEMA}.nyc_discovery_observation
                WHERE run_id = %s GROUP BY query ORDER BY results DESC, query
            """, (run_id,))
            by_query = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT disposition, count(*) AS n
                FROM {NYC_SCHEMA}.nyc_discovery_observation
                WHERE run_id = %s GROUP BY disposition ORDER BY n DESC
            """, (run_id,))
            by_disposition = [dict(r) for r in cur.fetchall()]

        return {
            "run": run,
            "observations": observations,
            "by_query": by_query,
            "by_disposition": by_disposition,
        }

    def nyc_discovery_coverage(self, *, runs: int = 8) -> dict:
        """Cross-run query productivity and area/source coverage."""
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                WITH recent AS (
                    SELECT id FROM {NYC_SCHEMA}.nyc_discovery_run
                    ORDER BY started_at DESC LIMIT %s
                )
                SELECT o.query,
                       count(*) AS results,
                       count(DISTINCT o.url) AS distinct_urls,
                       count(*) FILTER (WHERE o.disposition = 'rejected') AS rejected,
                       count(*) FILTER (WHERE o.disposition <> 'rejected'
                                          AND o.provider_id IS NULL) AS unlinked,
                       count(DISTINCT o.provider_id) AS providers,
                       max(o.observed_at) AS last_seen
                FROM {NYC_SCHEMA}.nyc_discovery_observation o
                WHERE o.run_id IN (SELECT id FROM recent)
                GROUP BY o.query ORDER BY providers DESC, results DESC
            """, (runs,))
            by_query = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT coalesce(area, '(unknown)') AS area,
                       count(*) AS offerings,
                       count(DISTINCT provider_id) AS providers
                FROM {NYC_SCHEMA}.nyc_offering GROUP BY 1 ORDER BY offerings DESC
            """)
            by_area = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT split_part(split_part(canonical_url, '://', 2), '/', 1) AS host,
                       count(*) AS providers,
                       count(*) FILTER (WHERE status = 'active') AS active
                FROM {NYC_SCHEMA}.nyc_provider GROUP BY 1 ORDER BY providers DESC LIMIT 25
            """)
            by_host = [dict(r) for r in cur.fetchall()]

        return {"by_query": by_query, "by_area": by_area, "by_host": by_host}

    # ── Tasks ────────────────────────────────────────────────────────────

    def nyc_tasks(self, *, status: str | None = None, limit: int = 200) -> list[dict]:
        self.nyc_require_schema()
        params: list = [NYC_TASK_TYPE]
        clause = ""
        if status:
            clause = "AND t.status = %s"
            params.append(status)
        params.append(limit)
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT t.id, t.status, t.priority, t.attempts, t.error,
                       t.created_at, t.started_at, t.finished_at, t.result,
                       t.payload->>'provider_id' AS provider_id,
                       t.payload->>'provider_name' AS provider_name,
                       t.payload->>'source_url' AS source_url,
                       t.payload->>'extraction_window' AS extraction_window,
                       extract(epoch FROM (now() - t.created_at)) AS age_seconds,
                       p.name AS current_provider_name
                FROM {NYC_SCHEMA}.discovery_task t
                LEFT JOIN {NYC_SCHEMA}.nyc_provider p
                       ON p.id = t.payload->>'provider_id'
                WHERE t.task_type = %s {clause}
                ORDER BY
                    CASE t.status WHEN 'failed' THEN 0 WHEN 'running' THEN 1
                                  WHEN 'queued' THEN 2 ELSE 3 END,
                    t.created_at DESC
                LIMIT %s
            """, params)
            return [dict(r) for r in cur.fetchall()]

    def nyc_task_detail(self, task_id: str) -> dict:
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT t.*, p.name AS current_provider_name, p.canonical_url
                FROM {NYC_SCHEMA}.discovery_task t
                LEFT JOIN {NYC_SCHEMA}.nyc_provider p
                       ON p.id = t.payload->>'provider_id'
                WHERE t.id = %s AND t.task_type = %s
            """, (task_id, NYC_TASK_TYPE))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"task not found: {task_id}")
        task = dict(row)
        payload = dict(task.get("payload") or {})
        excerpt = str(payload.pop("text_excerpt", "") or "")
        task["payload_without_excerpt"] = payload
        task["text_excerpt"] = excerpt[:NYC_TASK_EXCERPT_CHARS]
        task["text_excerpt_truncated"] = len(excerpt) > NYC_TASK_EXCERPT_CHARS
        task["text_excerpt_chars"] = len(excerpt)
        return task

    def nyc_task_action(self, *, task_id: str, action: str, actor: str,
                        expected_status: str | None = None) -> dict:
        """Change queue state so the Mini worker can claim (or skip) a task.

        Never runs Hermes: this only moves the row. ``retry`` clears the
        attempt counter so the worker's two-strike rule starts fresh;
        ``mark_reviewed`` closes the task without an extraction.
        """
        self.nyc_require_schema()
        if action not in ("retry", "mark_reviewed"):
            raise ValueError("NYC task action must be retry or mark_reviewed")
        if not actor:
            raise ValueError("NYC task action requires an actor")
        now = _now()
        with self.get_cursor() as cur:
            cur.execute(f"""
                SELECT id, status, attempts, result
                FROM {NYC_SCHEMA}.discovery_task
                WHERE id = %s AND task_type = %s FOR UPDATE
            """, (task_id, NYC_TASK_TYPE))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"task not found: {task_id}")
            current = dict(row)
            if expected_status and current["status"] != expected_status:
                raise NycStaleFormError(current)
            if current["status"] == "running":
                raise NycAdminError(
                    "task is running on the Mini; wait for it to finish"
                )

            result = dict(current.get("result") or {})
            history = list(result.get("admin_actions") or [])
            history.append({
                "action": action,
                "actor": actor,
                "at": now.isoformat(),
                "previous_status": current["status"],
                "previous_attempts": current["attempts"],
            })
            result["admin_actions"] = history[-20:]

            if action == "retry":
                cur.execute(f"""
                    UPDATE {NYC_SCHEMA}.discovery_task
                    SET status = 'queued', attempts = 0, error = NULL,
                        started_at = NULL, finished_at = NULL, result = %s::json
                    WHERE id = %s
                """, (json.dumps(result), task_id))
                new_status = "queued"
            else:
                cur.execute(f"""
                    UPDATE {NYC_SCHEMA}.discovery_task
                    SET status = 'done', finished_at = %s, error = NULL,
                        result = %s::json
                    WHERE id = %s
                """, (now, json.dumps(result), task_id))
                new_status = "done"

        return {
            "task_id": task_id,
            "action": action,
            "previous_status": current["status"],
            "status": new_status,
            "actor": actor,
        }

    # ── Expedited check requests (read-only; Atlas owns the writes) ──────

    def nyc_crawl_request_shape(self) -> dict | None:
        """Discovered column mapping, or None when the table is unusable.

        The site only ever reads this table: the request itself travels through
        the S3 control prefix, and Atlas owns whatever schema it settles on.
        """
        with self.get_cursor(commit=False) as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'nyc_crawl_request'",
                (NYC_SCHEMA,),
            )
            present = {r["column_name"] for r in cur.fetchall()}
        if not present:
            return None
        shape = {}
        for role, candidates in _CRAWL_REQUEST_COLUMNS.items():
            shape[role] = next((c for c in candidates if c in present), None)
        # Without a provider and a status there is nothing the console can say.
        if not shape["provider"] or not shape["status"]:
            return None
        return shape

    def nyc_latest_crawl_requests(self, provider_ids: list[str] | None = None) -> dict:
        """Latest expedited-check request per provider, keyed by provider id.

        Returns {} when the table is absent or does not carry the columns the
        console needs — the caller then shows "status not tracked yet".
        """
        shape = self.nyc_crawl_request_shape()
        if not shape:
            return {}
        provider_col = shape["provider"]
        select_bits = [f"{provider_col} AS provider_id", f"{shape['status']} AS status"]
        for role in ("request_id", "requested_at", "actor", "detail",
                     "object_key", "attempts"):
            column = shape[role]
            select_bits.append(f"{column} AS {role}" if column else f"NULL AS {role}")
        order_col = shape["requested_at"] or shape["request_id"] or provider_col

        params: list = []
        where = ""
        if provider_ids is not None:
            if not provider_ids:
                return {}
            where = f"WHERE {provider_col} = ANY(%s)"
            params.append(list(provider_ids))

        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT DISTINCT ON ({provider_col}) {', '.join(select_bits)}
                FROM {NYC_SCHEMA}.nyc_crawl_request
                {where}
                ORDER BY {provider_col}, {order_col} DESC
            """, params)
            return {str(r["provider_id"]): dict(r) for r in cur.fetchall()}

    def nyc_provider_for_check(self, provider_id: str) -> dict:
        """Server-loaded provider record used to build a check request.

        The UUID written into the control object comes from here, never from
        the browser.
        """
        self.nyc_require_schema()
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT p.id, p.name, p.status, p.enabled, p.last_crawled_at,
                       {_tier_sql('p')} AS tier
                FROM {NYC_SCHEMA}.nyc_provider p WHERE p.id = %s
            """, (provider_id,))
            row = cur.fetchone()
        if not row:
            raise NycNotFoundError(f"NYC provider not found: {provider_id}")
        provider = dict(row)
        provider["check_eligible"], provider["check_blocked_reason"] = (
            _check_eligibility(provider))
        return provider

    # ── The one policy mutation: A/B/C/D review ──────────────────────────

    def nyc_review_entry(self, *, entity_type: str, entity_id: str, decision: str,
                         tier: str, actor: str, rationale: str | None = None,
                         expected_updated_at: str | None = None) -> dict:
        """Apply an owner review decision atomically, mirroring `nyc-review`.

        Lock the row, capture previous status/tier, update crawl_state review
        keys without discarding unrelated keys, flip status/enabled, and insert
        the ``nyc_directory_decision`` audit row — all in one transaction, which
        rolls back whole if any step raises.
        """
        self.nyc_require_schema()
        if entity_type not in ("provider", "venue"):
            raise ValueError("NYC entity_type must be provider or venue")
        if decision not in ("approved", "rejected"):
            raise ValueError("NYC decision must be approved or rejected")
        tier = (tier or "").upper()
        if decision == "rejected":
            tier = "D"
        if tier not in NYC_TIERS:
            raise ValueError("NYC tier must be A, B, C, or D")
        if decision == "approved" and tier == "D":
            raise ValueError("approved NYC entries must use tier A, B, or C")
        if not actor:
            raise ValueError("NYC review requires an actor")

        table = "nyc_provider" if entity_type == "provider" else "nyc_venue"
        is_provider = entity_type == "provider"
        now = _now()

        with self.get_cursor() as cur:
            cur.execute(f"""
                SELECT id, name, status, crawl_state, updated_at
                       {', enabled' if is_provider else ''}
                FROM {NYC_SCHEMA}.{table} WHERE id = %s FOR UPDATE
            """, (entity_id,))
            row = cur.fetchone()
            if not row:
                raise NycNotFoundError(f"NYC {entity_type} not found: {entity_id}")
            entry = dict(row)

            if expected_updated_at is not None and not _same_timestamp(
                entry["updated_at"], expected_updated_at
            ):
                # Rolls back on the way out; the caller re-renders current state.
                raise NycStaleFormError(entry)

            previous_status = entry["status"]
            previous_tier = _normalized_tier(entry.get("crawl_state"))

            state = dict(entry.get("crawl_state") or {})
            state.update({
                "tier": tier,
                "review_decision": decision,
                "reviewed_at": now.isoformat(),
                "reviewed_by": actor,
                "review_rationale": rationale,
            })
            new_status = "active" if decision == "approved" else "retired"

            if is_provider:
                cur.execute(f"""
                    UPDATE {NYC_SCHEMA}.{table}
                    SET crawl_state = %s::json, status = %s, enabled = %s, updated_at = %s
                    WHERE id = %s
                """, (json.dumps(state), new_status, decision == "approved", now, entity_id))
            else:
                cur.execute(f"""
                    UPDATE {NYC_SCHEMA}.{table}
                    SET crawl_state = %s::json, status = %s, updated_at = %s
                    WHERE id = %s
                """, (json.dumps(state), new_status, now, entity_id))

            audit_id = str(uuid.uuid4())
            cur.execute(f"""
                INSERT INTO {NYC_SCHEMA}.nyc_directory_decision
                    (id, entity_type, entity_id, decision, previous_status,
                     previous_tier, tier, actor, rationale, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (audit_id, entity_type, entity_id, decision, previous_status,
                  previous_tier, tier, actor, rationale, now))

        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "name": entry["name"],
            "decision": decision,
            "tier": tier,
            "previous_status": previous_status,
            "previous_tier": previous_tier,
            "status": new_status,
            "audit_id": audit_id,
            "actor": actor,
        }

    def nyc_decisions(self, *, entity_type: str | None = None,
                      limit: int = 200) -> list[dict]:
        self.nyc_require_schema()
        params: list = []
        clause = ""
        if entity_type:
            clause = "WHERE d.entity_type = %s"
            params.append(entity_type)
        params.append(limit)
        with self.get_cursor(commit=False) as cur:
            cur.execute(f"""
                SELECT d.*, coalesce(p.name, v.name) AS entity_name
                FROM {NYC_SCHEMA}.nyc_directory_decision d
                LEFT JOIN {NYC_SCHEMA}.nyc_provider p
                       ON d.entity_type = 'provider' AND p.id = d.entity_id
                LEFT JOIN {NYC_SCHEMA}.nyc_venue v
                       ON d.entity_type = 'venue' AND v.id = d.entity_id
                {clause}
                ORDER BY d.decided_at DESC LIMIT %s
            """, params)
            return [dict(r) for r in cur.fetchall()]


# ── Helpers shared with the routes ───────────────────────────────────────

def _normalized_tier(crawl_state) -> str:
    tier = str((crawl_state or {}).get("tier") or "C").upper()
    return tier if tier in NYC_TIERS else "C"


def _is_due(entry: dict, *, enabled_field: bool, now: datetime | None = None) -> bool:
    """Python mirror of nyc_entry_is_due, for single-row detail pages."""
    now = now or _now()
    tier = entry.get("tier") or _normalized_tier(entry.get("crawl_state"))
    if tier == "D" or entry.get("status") != "active":
        return False
    if enabled_field and not entry.get("enabled", True):
        return False
    last = _as_aware(entry.get("last_crawled_at"))
    if last is None:
        return True
    return (now - last).total_seconds() >= NYC_TIER_INTERVAL_DAYS[tier] * 86400


def _check_eligibility(provider: dict) -> tuple[bool, str]:
    """Only an approved, still-crawled provider may be checked early."""
    if provider.get("status") != "active" or not provider.get("enabled", True):
        return False, "only an approved provider can be checked early"
    if provider.get("tier") not in ("A", "B", "C"):
        return False, "a tier D record is never crawled again"
    return True, ""


def _new_evidence_count(row: dict) -> int:
    """Discovery observations recorded since the last owner review."""
    state = row.get("crawl_state") or {}
    observations = state.get("discovery_observations") or []
    reviewed_at = state.get("reviewed_at")
    if not reviewed_at:
        return len(observations)
    try:
        cutoff = _as_aware(datetime.fromisoformat(str(reviewed_at)))
    except ValueError:
        return len(observations)
    count = 0
    for obs in observations:
        seen = obs.get("observed_at")
        try:
            if seen and _as_aware(datetime.fromisoformat(str(seen))) > cutoff:
                count += 1
        except ValueError:
            continue
    return count
