"""
Migration: analysis per (document, model profile) instead of per document.

Before this migration the model was chosen by a document's source name: sources
ending in ``-Astra`` ran OpenAI Astra, everything else Gemini. Progress was keyed
(document, step) and gates (group, step), so a document could carry only one
model's analysis.

This script, in one transaction:

1. Creates ``analysis_profile`` and seeds five profiles. ``gemini-3-flash`` owns
   every historical result (bare cache keys, canonical); ``astra-low`` owns the
   2026-09-22 Astra run (``:codex-astra`` keys); ``gemini-3.1``, ``astra-medium``
   and ``opus-5.5`` run under schema contract 3. Every profile starts switched
   off, so nothing is routed to any model until the owner turns one on.
2. Adds ``profile_key`` to document_analysis_state, group_analysis_gate,
   lesson_embedding and lesson_concept, backfills it, and makes it part of each
   primary key. Completed progress rows record contract version 2.
3. Returns the Astra sources to meaning provenance: strips ``-Astra`` from the
   sources that hold documents (and their groups), moves "FI Demo: Arching
   Through Rounding" back to FELDENKRAIS_ACCESS_YOUTUBE, and deletes the empty
   ``-Astra`` sources and their groups.
4. Verifies, before committing, that row counts match and that exactly the same
   work is runnable as before: the Gemini work under ``gemini-3-flash`` and the
   Astra work under ``astra-low``. Any mismatch rolls everything back.

No cache entry is touched: every existing result stays under the key it was
written with.

Usage:
    .venv/bin/python scripts/migrate_analysis_profiles.py --dry-run   # verify, then roll back
    .venv/bin/python scripts/migrate_analysis_profiles.py             # verify, then commit
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import psycopg2
from psycopg2.extras import Json, RealDictCursor

ANALYZER_LOCK = "feldenkrais:analysis-worker"
ASTRA_SUFFIX = "-Astra"
PILOT_GROUP = "NewMateria Astra Pilot"
FI_DEMO_ID = "501c9674-7659-4d4f-aaf3-5c554cc2d94c"
FI_DEMO_HOME_SOURCE = "FELDENKRAIS_ACCESS_YOUTUBE"

PROFILES = [
    dict(key="gemini-3-flash", provider="gemini", model="gemini-3-flash-preview", params={},
         cache_tag=None, contract_version=2, enabled=False, canonical=True, max_concurrency=4,
         notes="Every result produced before per-model profiles; bare cache keys."),
    dict(key="astra-low", provider="codex", model="gpt-6-astra", params={"reasoning_effort": "low"},
         cache_tag="codex-astra", contract_version=2, enabled=False, canonical=False, max_concurrency=6,
         notes="OpenAI Astra via the Codex CLI subscription, first run 2026-09-22. "
               "Contract 2 schemas, strictened by the Codex adapter."),
    dict(key="gemini-3.1", provider="gemini", model="gemini-3.1-pro-preview", params={},
         cache_tag="gemini-3.1", contract_version=3, enabled=False, canonical=False, max_concurrency=4,
         notes="Model id assumed to be gemini-3.1-pro-preview (gemini-3.1-flash-lite-preview also exists). "
               "Confirm before switching on."),
    dict(key="astra-medium", provider="codex", model="gpt-6-astra", params={"reasoning_effort": "medium"},
         cache_tag="astra-medium", contract_version=3, enabled=False, canonical=False, max_concurrency=4,
         notes="OpenAI Astra via the Codex CLI subscription, medium reasoning effort."),
    dict(key="opus-5.5", provider="anthropic", model="claude-opus-5-5",
         params={"effort": "medium", "max_tokens": 64000},
         cache_tag="opus-5.5", contract_version=3, enabled=False, canonical=False, max_concurrency=2,
         notes="Structured output via output_config.format; thinking always on, effort sets its depth."),
]

# A document belongs to the Astra run when its source is an -Astra source,
# except FI Demo, whose progress and derived rows all date from Gemini.
ASTRA_DOCS_SQL = f"""
    SELECT d.id FROM document d JOIN document_source ds ON ds.id = d.source_id
    WHERE ds.name LIKE '%{ASTRA_SUFFIX}' AND d.id <> '{FI_DEMO_ID}'
"""
ASTRA_GROUPS_SQL = f"""
    SELECT lg.id FROM lesson_group lg
    LEFT JOIN document_source ds ON ds.id = lg.auto_source_id
    WHERE ds.name LIKE '%{ASTRA_SUFFIX}' OR lg.name = '{PILOT_GROUP}'
"""

# The runnable query as it stood before this migration.
OLD_RUNNABLE_SQL = """
    WITH candidates AS (
        SELECT DISTINCT d.id as document_id, s.name as step_name, s.depends_on
        FROM lesson_group_member lgm
        JOIN group_analysis_gate gag ON gag.group_id = lgm.group_id AND gag.permitted = TRUE
        JOIN analysis_step s ON s.name = gag.step_name
        JOIN document d ON d.id = lgm.document_id
        LEFT JOIN document_analysis_state das ON das.document_id = d.id AND das.step_name = s.name
        WHERE (das.completed_version IS NULL
               OR das.completed_version < s.current_version
               OR das.stale = TRUE)
          AND (das.error IS NULL)
    )
    SELECT c.document_id, c.step_name FROM candidates c
    WHERE NOT EXISTS (
        SELECT 1 FROM unnest(c.depends_on) dep(name)
        WHERE NOT EXISTS (
            SELECT 1 FROM document_analysis_state das2
            WHERE das2.document_id = c.document_id AND das2.step_name = dep.name
              AND das2.completed_version IS NOT NULL AND das2.stale IS NOT TRUE
              AND das2.error IS NULL
        )
    )
"""

# The runnable query after this migration (same as PipelineStateMixin), across
# every profile regardless of its switch.
NEW_RUNNABLE_SQL = """
    WITH candidates AS (
        SELECT DISTINCT d.id as document_id, p.key as profile_key,
               s.name as step_name, s.depends_on
        FROM lesson_group_member lgm
        JOIN group_analysis_gate gag ON gag.group_id = lgm.group_id AND gag.permitted = TRUE
        JOIN analysis_profile p ON p.key = gag.profile_key
        JOIN analysis_step s ON s.name = gag.step_name
        JOIN document d ON d.id = lgm.document_id
        LEFT JOIN document_analysis_state das
          ON das.document_id = d.id AND das.step_name = s.name AND das.profile_key = p.key
        WHERE (das.completed_version IS NULL
               OR das.completed_version < s.current_version
               OR das.contract_version IS DISTINCT FROM p.contract_version
               OR das.stale = TRUE)
          AND (das.error IS NULL)
    )
    SELECT c.document_id, c.profile_key, c.step_name FROM candidates c
    WHERE NOT EXISTS (
        SELECT 1 FROM unnest(c.depends_on) dep(name)
        WHERE NOT EXISTS (
            SELECT 1 FROM document_analysis_state das2
            WHERE das2.document_id = c.document_id AND das2.profile_key = c.profile_key
              AND das2.step_name = dep.name
              AND das2.completed_version IS NOT NULL AND das2.stale IS NOT TRUE
              AND das2.error IS NULL
        )
    )
"""


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return list(row.values())[0]


def table_counts(cur) -> dict:
    return {t: scalar(cur, f"SELECT COUNT(*) FROM {t}")
            for t in ("document_analysis_state", "group_analysis_gate", "lesson_embedding",
                      "lesson_concept", "lesson_group_member", "document", "cache")}


def swap_primary_key(cur, table: str, columns: str):
    cur.execute("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype = 'p'
    """, (table,))
    name = cur.fetchone()["conname"]
    cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
    cur.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({columns})")


def add_profile_column(cur, table: str, backfill_sql: str):
    cur.execute(f"ALTER TABLE {table} ADD COLUMN profile_key TEXT")
    cur.execute(backfill_sql)
    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE profile_key IS NULL")
    missing = cur.fetchone()["n"]
    if missing:
        raise RuntimeError(f"{table}: {missing} rows left without a profile")
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN profile_key SET NOT NULL")
    cur.execute(f"""
        ALTER TABLE {table} ADD CONSTRAINT {table}_profile_key_fkey
        FOREIGN KEY (profile_key) REFERENCES analysis_profile(key)
    """)


def by_document(table: str) -> str:
    return f"""
        UPDATE {table} t SET profile_key = CASE
            WHEN t.document_id IN ({ASTRA_DOCS_SQL}) THEN 'astra-low'
            ELSE 'gemini-3-flash' END
    """


def migrate(db_url: str, dry_run: bool) -> int:
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    conn.autocommit = False
    cur = conn.cursor()
    report: dict = {}
    try:
        cur.execute("SELECT to_regclass('public.analysis_profile') AS t")
        if cur.fetchone()["t"]:
            print("analysis_profile already exists; nothing to do.")
            conn.rollback()
            return 0

        # No analysis worker may run while the tables change shape.
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (ANALYZER_LOCK,))
        if not cur.fetchone()["ok"]:
            print("An analysis worker holds its lease. Stop it first.")
            conn.rollback()
            return 1

        # Take every table lock up front, in the order initialize() creates its
        # indexes (document first). Workers and Lambdas run initialize() at
        # startup; taking locks mid-transaction deadlocks against them, while
        # taking them first only makes a starting process wait a few seconds.
        cur.execute("SET LOCAL lock_timeout = '60s'")
        cur.execute("""
            LOCK TABLE document, lesson_embedding, lesson_concept, lesson_group,
                       lesson_group_member, group_analysis_gate, document_analysis_state,
                       document_source
            IN ACCESS EXCLUSIVE MODE
        """)

        # ── Before ────────────────────────────────────────────────────────
        before = table_counts(cur)
        cur.execute(ASTRA_DOCS_SQL)
        astra_docs = {str(r["id"]) for r in cur.fetchall()}
        cur.execute(OLD_RUNNABLE_SQL)
        old_runnable = {(str(r["document_id"]), r["step_name"]) for r in cur.fetchall()}
        old_gemini = {r for r in old_runnable if r[0] not in astra_docs and r[0] != FI_DEMO_ID}
        old_astra = {r for r in old_runnable if r[0] in astra_docs}

        cur.execute(f"SELECT id FROM document_source WHERE name = %s", (FI_DEMO_HOME_SOURCE,))
        home_source = cur.fetchone()["id"]
        cur.execute(f"SELECT id FROM lesson_group WHERE auto_source_id = %s AND name = %s",
                    (home_source, FI_DEMO_HOME_SOURCE))
        home_group = cur.fetchone()["id"]
        cur.execute(f"""
            SELECT ds.id, ds.name, lg.id AS group_id,
                   (SELECT COUNT(*) FROM document d WHERE d.source_id = ds.id) AS docs
            FROM document_source ds
            LEFT JOIN lesson_group lg ON lg.name = ds.name
            WHERE ds.name LIKE '%{ASTRA_SUFFIX}'
        """)
        astra_sources = cur.fetchall()

        # ── 1. Profiles ───────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE analysis_profile (
                key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                params JSONB NOT NULL DEFAULT '{}'::jsonb,
                cache_tag TEXT UNIQUE,
                contract_version INTEGER NOT NULL DEFAULT 2,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                canonical BOOLEAN NOT NULL DEFAULT FALSE,
                max_concurrency INTEGER NOT NULL DEFAULT 2,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE UNIQUE INDEX idx_analysis_profile_one_canonical ON analysis_profile ((TRUE)) WHERE canonical")
        for p in PROFILES:
            cur.execute("""
                INSERT INTO analysis_profile
                    (key, provider, model, params, cache_tag, contract_version, enabled, canonical,
                     max_concurrency, notes)
                VALUES (%(key)s, %(provider)s, %(model)s, %(params)s, %(cache_tag)s, %(contract_version)s,
                        %(enabled)s, %(canonical)s, %(max_concurrency)s, %(notes)s)
            """, {**p, "params": Json(p["params"])})

        # ── 2. Profile-keyed tables ───────────────────────────────────────
        cur.execute("ALTER TABLE document_analysis_state ADD COLUMN contract_version INTEGER")
        add_profile_column(cur, "document_analysis_state", by_document("document_analysis_state"))
        cur.execute("UPDATE document_analysis_state SET contract_version = 2 WHERE completed_version IS NOT NULL")
        swap_primary_key(cur, "document_analysis_state", "document_id, profile_key, step_name")

        add_profile_column(cur, "group_analysis_gate", f"""
            UPDATE group_analysis_gate g SET profile_key = CASE
                WHEN g.group_id IN ({ASTRA_GROUPS_SQL}) THEN 'astra-low'
                ELSE 'gemini-3-flash' END
        """)
        swap_primary_key(cur, "group_analysis_gate", "group_id, profile_key, step_name")
        cur.execute("""CREATE INDEX idx_gag_profile_permitted ON group_analysis_gate
                       (group_id, profile_key, step_name) WHERE permitted = TRUE""")

        add_profile_column(cur, "lesson_embedding", by_document("lesson_embedding"))
        swap_primary_key(cur, "lesson_embedding", "document_id, profile_key, method")
        cur.execute("CREATE INDEX idx_lesson_embedding_profile_method ON lesson_embedding (profile_key, method)")

        add_profile_column(cur, "lesson_concept", by_document("lesson_concept"))
        swap_primary_key(cur, "lesson_concept", "document_id, profile_key, category, value")

        # ── 3. Sources mean provenance again ──────────────────────────────
        deleted_groups = []
        renamed = []
        for src in astra_sources:
            base = src["name"][: -len(ASTRA_SUFFIX)]
            if src["name"] == FI_DEMO_HOME_SOURCE + ASTRA_SUFFIX:
                cur.execute("UPDATE document SET source_id = %s, updated_at = NOW() WHERE id = %s",
                            (home_source, FI_DEMO_ID))
                cur.execute("""INSERT INTO lesson_group_member (group_id, document_id)
                               VALUES (%s, %s) ON CONFLICT DO NOTHING""", (home_group, FI_DEMO_ID))
                if src["group_id"]:
                    cur.execute("DELETE FROM lesson_group WHERE id = %s", (src["group_id"],))
                    deleted_groups.append(src["name"])
                cur.execute("DELETE FROM document_source WHERE id = %s", (src["id"],))
            elif src["docs"] == 0:
                cur.execute("DELETE FROM lesson_group WHERE auto_source_id = %s", (src["id"],))
                deleted_groups.extend([src["name"]] * cur.rowcount)
                cur.execute("DELETE FROM document_source WHERE id = %s", (src["id"],))
            else:
                cur.execute("""UPDATE document_source SET name = %s,
                               category_name = regexp_replace(category_name, %s, '')
                               WHERE id = %s""", (base, ASTRA_SUFFIX + "$", src["id"]))
                if src["group_id"]:
                    cur.execute("UPDATE lesson_group SET name = %s WHERE id = %s", (base, src["group_id"]))
                renamed.append(base)
        cur.execute(f"SELECT COUNT(*) AS n FROM document_source WHERE name LIKE '%{ASTRA_SUFFIX}'")
        left = cur.fetchone()["n"]
        if left:
            raise RuntimeError(f"{left} -Astra sources remain")

        # ── 4. Verify ─────────────────────────────────────────────────────
        after = table_counts(cur)
        cur.execute(NEW_RUNNABLE_SQL)
        new_runnable = {(str(r["document_id"]), r["profile_key"], r["step_name"]) for r in cur.fetchall()}
        new_gemini = {(d, s) for d, p, s in new_runnable if p == "gemini-3-flash" and d != FI_DEMO_ID}
        new_astra = {(d, s) for d, p, s in new_runnable if p == "astra-low"}
        fi_demo_now = sorted(s for d, p, s in new_runnable if d == FI_DEMO_ID)

        problems = []
        # The cache is never touched here but other processes write it, so it is
        # reported, not compared.
        for table in ("document_analysis_state", "lesson_embedding", "lesson_concept", "document"):
            if before[table] != after[table]:
                problems.append(f"{table}: {before[table]} rows before, {after[table]} after")
        if new_gemini != old_gemini:
            problems.append(f"gemini-3-flash runnable work changed: {len(old_gemini)} before, {len(new_gemini)} after")
        if new_astra != old_astra:
            problems.append(f"astra-low runnable work changed: {len(old_astra)} before, {len(new_astra)} after")
        others = {p for _, p, _ in new_runnable} - {"gemini-3-flash", "astra-low"}
        if others:
            problems.append(f"profiles that should have no gates have runnable work: {sorted(others)}")

        report = {
            "before": before, "after": after,
            "runnable_gemini_3_flash": len(new_gemini),
            "runnable_astra_low": len(new_astra),
            "fi_demo_runnable_under_its_home_gates": fi_demo_now,
            "sources_renamed": len(renamed),
            "groups_deleted": len(deleted_groups),
            "state_rows_by_profile": {},
        }
        cur.execute("SELECT profile_key, COUNT(*) AS n FROM document_analysis_state GROUP BY 1 ORDER BY 1")
        report["state_rows_by_profile"] = {r["profile_key"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT profile_key, COUNT(*) AS n FROM group_analysis_gate GROUP BY 1 ORDER BY 1")
        report["gates_by_profile"] = {r["profile_key"]: r["n"] for r in cur.fetchall()}
        print(json.dumps(report, indent=2, default=str))

        if problems:
            print("\nVerification failed; rolling back:")
            for problem in problems:
                print(f"  - {problem}")
            conn.rollback()
            return 1

        if dry_run:
            print("\nDry run: verification passed; rolling back.")
            conn.rollback()
        else:
            conn.commit()
            print("\nMigrated and committed.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="verify, then roll back")
    args = parser.parse_args()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set")
    sys.exit(migrate(db_url, args.dry_run))


if __name__ == "__main__":
    main()
