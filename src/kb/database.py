"""PostgreSQL database operations for the knowledge base.

This module re-exports the Database class from the db package.
All implementation lives in src/kb/db/ as domain-specific mixins.
"""

try:
    # When imported as part of kb package (e.g., from kb.database import Database)
    from kb.db import Database, SCHEMA, INDEXES, SEED_DATA
except ImportError:
    # When imported directly with src/kb on sys.path (e.g., from database import Database)
    from db import Database, SCHEMA, INDEXES, SEED_DATA

__all__ = ["Database", "SCHEMA", "INDEXES", "SEED_DATA"]
