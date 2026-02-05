# Query Agent Cache: File to Database Migration

## Current State

- **File-based cache**: ~300 JSON files in `data/cache/query_agent/`
- **Cache key**: SHA256 hash (first 16 chars) of `(query_type, prompt, context)`
- **File content**:
  ```json
  {
    "query_type": "pedagogical-gloss",
    "prompt": "...(truncated to 200 chars)...",
    "text": "...full LLM response..."
  }
  ```
- **Usage**: `QueryAgent` in `query_agent.py`, called from `pipeline_graph.py`

## Proposed Approach

**Use the existing `cache` table** with a namespaced key pattern: `query_agent:{hash}`

This is consistent with how pipeline results are already stored (e.g., `pipeline:analysis_a:vl1`).

### Why not a dedicated table?

- The existing `cache` table is already set up with JSONB storage
- `QueryAgent` already has database access via the web module
- Keeps the caching pattern consistent across the codebase
- Simpler implementation - no schema changes needed

## Implementation Steps

### 1. Modify `QueryAgent` to accept a database connection

```python
class QueryAgent:
    def __init__(self, ..., db: Database = None, cache_dir: str = None):
        self.db = db
        self.cache_dir = cache_dir  # Fallback for backward compatibility
```

### 2. Update cache key format

Current: `5bd1faa324b6d992` (raw hash)
New: `query_agent:5bd1faa324b6d992` (namespaced)

### 3. Update `_load_cached()` method

```python
def _load_cached(self, block: QueryBlock) -> str | None:
    key = self._cache_key(block)

    # Try database first if available
    if self.db:
        data = self.db.get_cache(f"query_agent:{key}")
        if data:
            return data.get("text")

    # Fallback to file cache
    if self.cache_dir:
        cache_file = Path(self.cache_dir) / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text()).get("text")

    return None
```

### 4. Update `_save_cached()` method

```python
def _save_cached(self, block: QueryBlock, text: str) -> None:
    key = self._cache_key(block)
    data = {
        "query_type": block.query_type,
        "prompt": block.prompt[:200],  # Truncate for readability
        "text": text
    }

    # Save to database if available
    if self.db:
        self.db.set_cache(f"query_agent:{key}", data)

    # Also save to file cache if configured (optional fallback)
    if self.cache_dir:
        cache_file = Path(self.cache_dir) / f"{key}.json"
        cache_file.write_text(json.dumps(data, indent=2))
```

### 5. Update callers to pass database

**`pipeline_graph.py`** (line 182):
```python
# Current
agent = QueryAgent(model="...", cache_dir=str(w.DATA_DIR / "cache" / "query_agent"))

# New
agent = QueryAgent(model="...", db=self.db)  # Pass database from pipeline
```

### 6. One-time migration script

Create `scripts/migrate_query_cache.py`:
```python
"""Migrate file-based query_agent cache to database."""
import json
from pathlib import Path
from database import Database

def migrate():
    db = Database(os.environ["DATABASE_URL"])
    cache_dir = Path("data/cache/query_agent")

    for cache_file in cache_dir.glob("*.json"):
        key = cache_file.stem  # e.g., "5bd1faa324b6d992"
        data = json.loads(cache_file.read_text())
        db.set_cache(f"query_agent:{key}", data)
        print(f"Migrated {key}")

    print(f"Done. Migrated {len(list(cache_dir.glob('*.json')))} entries.")
```

## File Changes Summary

| File | Change |
|------|--------|
| `src/kb/query_agent.py` | Add `db` parameter, update `_load_cached`/`_save_cached` |
| `src/kb/pipeline_graph.py` | Pass `db` to QueryAgent |
| `scripts/migrate_query_cache.py` | New migration script |

## Post-Migration

After verifying the database cache works:
1. Run migration script to copy existing cache entries
2. Remove `cache_dir` parameter from QueryAgent calls
3. Delete `data/cache/query_agent/` directory

## Rollback

If issues arise, the file-based fallback remains available by passing `cache_dir` instead of `db`.
