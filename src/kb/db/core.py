"""Core database infrastructure: connection pool, cursor, schema initialization."""

import json
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# Schema for initialization (PostgreSQL)
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_source (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category_name TEXT,
    visible BOOLEAN NOT NULL DEFAULT TRUE,
    public BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS document_type (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    source_url TEXT,
    s3_key TEXT,
    source_id INTEGER NOT NULL REFERENCES document_source(id),
    type_id INTEGER NOT NULL REFERENCES document_type(id),
    status TEXT NOT NULL DEFAULT 'pending',
    category_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    tags JSONB,
    metadata JSONB,
    lesson_date DATE,
    lesson_index TEXT,
    audio_fingerprint TEXT,
    origin TEXT,
    presenter TEXT
);

CREATE TABLE IF NOT EXISTS analysis_type (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS analysis (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES analysis_type(id),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transcription_job (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 1,
    error TEXT,
    chunk_index INTEGER,
    chunk_offset_seconds REAL,
    total_chunks INTEGER,
    result_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS youtube_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    video_id TEXT,
    video_title TEXT,
    channel_name TEXT,
    channel_id TEXT,
    duration_seconds INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fm_lesson_status (
    lesson_url TEXT PRIMARY KEY,
    lesson_title TEXT,
    youtube_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    video_count INTEGER DEFAULT 1,
    videos_staged INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_log (
    id SERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    document_id UUID,
    title TEXT,
    detail JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_node_state (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    input_hash TEXT NOT NULL DEFAULT '',
    output_hash TEXT NOT NULL DEFAULT '',
    last_run_at TIMESTAMPTZ,
    duration_seconds REAL,
    error_message TEXT,
    metadata JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lesson_embedding (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'default',
    embedding vector(1536),
    summary_text TEXT,
    summary_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (document_id, method)
);

CREATE TABLE IF NOT EXISTS lesson_concept (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (document_id, category, value)
);

CREATE TABLE IF NOT EXISTS analysis_step (
    name TEXT PRIMARY KEY,
    current_version INTEGER NOT NULL DEFAULT 1,
    cache_suffix TEXT,
    depends_on TEXT[] NOT NULL DEFAULT '{}',
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lesson_group (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    auto_source_id INTEGER REFERENCES document_source(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lesson_group_member (
    group_id INTEGER NOT NULL REFERENCES lesson_group(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, document_id)
);

CREATE TABLE IF NOT EXISTS group_analysis_gate (
    group_id INTEGER NOT NULL REFERENCES lesson_group(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL REFERENCES analysis_step(name),
    permitted BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (group_id, step_name)
);

CREATE TABLE IF NOT EXISTS document_analysis_state (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL REFERENCES analysis_step(name),
    completed_version INTEGER,
    completed_at TIMESTAMPTZ,
    upstream_hash TEXT,
    stale BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (document_id, step_name)
);

CREATE TABLE IF NOT EXISTS search_query_log (
    id SERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    result_count INTEGER,
    specificity_band TEXT,
    specificity_gap REAL,
    top_results JSONB,
    user_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    login TEXT NOT NULL,
    name TEXT,
    email TEXT,
    avatar_url TEXT,
    tier TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (provider, login)
);

CREATE TABLE IF NOT EXISTS allowed_logins (
    login TEXT PRIMARY KEY,
    added_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_source_access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, source_id)
);

CREATE TABLE IF NOT EXISTS user_beta_access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature TEXT NOT NULL,
    PRIMARY KEY (user_id, feature)
);

CREATE TABLE IF NOT EXISTS access_group (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_group (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES access_group(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS group_source_access (
    group_id INTEGER NOT NULL REFERENCES access_group(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, source_id)
);

CREATE TABLE IF NOT EXISTS external_lesson (
    id SERIAL PRIMARY KEY,
    lesson_source_id INTEGER REFERENCES lesson_source(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    url TEXT,
    description TEXT,
    tags TEXT[],
    position TEXT,
    duration_minutes INTEGER,
    language TEXT DEFAULT 'en',
    access TEXT,
    content_type TEXT,
    embedding vector(1536),
    embed_text TEXT,
    extras JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (lesson_source_id, title)
);

CREATE TABLE IF NOT EXISTS lesson_view (
    id SERIAL PRIMARY KEY,
    document_id TEXT NOT NULL,
    user_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lesson_chat_log (
    id SERIAL PRIMARY KEY,
    document_id TEXT NOT NULL,
    user_id INTEGER,
    query TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_document_source ON document(source_id);
CREATE INDEX IF NOT EXISTS idx_document_type ON document(type_id);
CREATE INDEX IF NOT EXISTS idx_document_status ON document(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_document_unique ON document(title, source_url);
CREATE INDEX IF NOT EXISTS idx_analysis_document ON analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis(type_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_document ON transcription_job(document_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_status ON transcription_job(status);
CREATE INDEX IF NOT EXISTS idx_transcription_job_batch ON transcription_job(batch_id);
CREATE INDEX IF NOT EXISTS idx_youtube_queue_status ON youtube_queue(status);
CREATE INDEX IF NOT EXISTS idx_fm_lesson_status ON fm_lesson_status(status);
CREATE INDEX IF NOT EXISTS idx_fm_lesson_youtube ON fm_lesson_status(youtube_id);
CREATE INDEX IF NOT EXISTS idx_document_lesson_date ON document(lesson_date);
CREATE INDEX IF NOT EXISTS idx_document_audio_fingerprint ON document(audio_fingerprint);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_event ON pipeline_log(event);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_created ON pipeline_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_document ON pipeline_log(document_id);
CREATE INDEX IF NOT EXISTS idx_lesson_embedding_hnsw ON lesson_embedding USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_lesson_embedding_method ON lesson_embedding (method);
CREATE INDEX IF NOT EXISTS idx_lesson_concept_value ON lesson_concept (value);
CREATE INDEX IF NOT EXISTS idx_lesson_concept_category ON lesson_concept (category, value);
CREATE INDEX IF NOT EXISTS idx_external_lesson_source ON external_lesson (lesson_source_id);
CREATE INDEX IF NOT EXISTS idx_external_lesson_embedding_hnsw ON external_lesson USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_lgm_document ON lesson_group_member (document_id);
CREATE INDEX IF NOT EXISTS idx_gag_permitted ON group_analysis_gate (group_id, step_name) WHERE permitted = TRUE;
CREATE INDEX IF NOT EXISTS idx_das_document ON document_analysis_state (document_id);
CREATE INDEX IF NOT EXISTS idx_das_step ON document_analysis_state (step_name);
CREATE INDEX IF NOT EXISTS idx_lesson_view_user ON lesson_view (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lesson_view_document ON lesson_view (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lesson_chat_document ON lesson_chat_log (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lesson_chat_user ON lesson_chat_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_transcript ON analysis (document_id) WHERE type_id = 1;
"""

SEED_DATA = """
INSERT INTO document_source (id, name) VALUES (1, 'OTHER'), (2, 'YOUTUBE'), (3, 'FELDENKRAIS_MANHATTAN'), (4, 'FELDENKRAIS_PROJECT') ON CONFLICT DO NOTHING;
INSERT INTO document_type (id, name) VALUES (1, 'TEXT'), (2, 'AUDIO'), (3, 'VIDEO') ON CONFLICT DO NOTHING;
INSERT INTO analysis_type (id, name) VALUES (1, 'TRANSCRIPTION'), (2, 'ANNOTATION') ON CONFLICT DO NOTHING;
INSERT INTO analysis_step (name, current_version, cache_suffix, depends_on, description) VALUES
    ('decompose', 3, ':decompose_v3', '{}', 'Decomposition + VL classification'),
    ('somatic', 2, ':somatic_v2', '{decompose}', 'Step-level somatic feature extraction'),
    ('language', 1, ':language_v1', '{decompose}', 'Step-level teaching voice analysis'),
    ('language_patterns', 3, ':language_patterns_v3', '{decompose,language}', 'Full-text language analysis'),
    ('concept_tags', 2, ':concept_tags_v2', '{decompose,somatic}', 'Concept tagging for search'),
    ('spatial', 2, ':spatial_v2', '{decompose,somatic}', 'Body configuration extraction'),
    ('condense', 4, ':condense_v4', '{somatic,language}', 'Condensed synthesis for embeddings'),
    ('embed', 1, NULL, '{condense}', 'Vector embedding generation'),
    ('index_concepts', 1, NULL, '{concept_tags}', 'Concept index build'),
    ('index_spatial', 1, NULL, '{spatial}', 'Spatial index build'),
    ('extract_steps', 2, ':extract_steps_v2', '{}', 'Step instruction extraction'),
    ('map_body_parts', 3, ':map_body_parts_v3', '{extract_steps}', 'Body part mapping')
ON CONFLICT DO NOTHING;
"""


class DatabaseCore:
    """Core database infrastructure: connection pool, cursor, schema."""

    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        self.database_url = database_url
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._min_connections = min_connections
        self._max_connections = max_connections
        self.public_only = False  # deprecated, kept for compat
        self.allowed_source_ids: set[int] | None = None  # None = no filtering (superuser/admin)

    def _get_pool(self) -> pool.ThreadedConnectionPool:
        """Get or create the connection pool."""
        if self._pool is None:
            self._pool = pool.ThreadedConnectionPool(
                self._min_connections,
                self._max_connections,
                self.database_url,
            )
        return self._pool

    @contextmanager
    def get_cursor(self, commit: bool = True):
        """
        Context manager for database operations.

        Yields a cursor and handles connection management.
        Commits by default; set commit=False for read-only operations.
        """
        conn = self._get_pool().getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
                if commit:
                    conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._get_pool().putconn(conn)

    def close(self):
        """Close all database connections."""
        if self._pool:
            self._pool.closeall()

    def initialize(self):
        """Initialize the database schema."""
        with self.get_cursor() as cursor:
            # Create tables
            cursor.execute(SCHEMA)
            # Create indexes (each separately to handle IF NOT EXISTS properly)
            for statement in INDEXES.strip().split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            # Migrations: add columns if missing (safe for concurrent execution)
            for table, column, col_type in [
                ("document_source", "category_name", "TEXT"),
                ("document_source", "visible", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("document_source", "public", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("document", "category_name", "TEXT"),
                ("document", "transcription_retries", "INTEGER DEFAULT 0"),
                ("document", "audio_fingerprint", "TEXT"),
                ("document", "origin", "TEXT"),
                ("document", "presenter", "TEXT"),
                ("users", "superuser_eligible", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ]:
                cursor.execute(
                    """SELECT 1 FROM information_schema.columns
                       WHERE table_name = %s AND column_name = %s""",
                    (table, column),
                )
                if not cursor.fetchone():
                    cursor.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE {table} ADD COLUMN {column} {col_type};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
            # Seed enum values
            for statement in SEED_DATA.strip().split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
        # Sync auto-groups from document sources (outside the schema transaction)
        self.sync_auto_groups()
