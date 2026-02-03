Analyze the book review summary and plan improvements.

## What this does

Reads the ranked issue list from `pipeline:review_summary` and creates an actionable plan to address the top issues.

## Arguments

- `loop N` or `loop=N` - Run N improvement iterations, waiting for pipeline between each
- `auto` - Skip user approval, commit and push automatically
- `wait` - Wait for pipeline to complete before reading review summary

Examples:
- `/improve-book` - Single iteration with approval
- `/improve-book auto` - Single iteration, no approval needed
- `/improve-book loop 3 auto` - 3 iterations, no approval, wait for pipeline between each

## Workflow

1. **Wait for pipeline** (if `wait` or `loop` mode) - Poll JSON status until fresh or error
2. **Read the review summary** from the pipeline cache
3. **Analyze which issues are actionable** - check actual files, understand what can be changed
4. **Select ~3 high-impact issues** that don't conflict with each other
5. **Dry-run cost check** - run `pipeline.py run --dry-run --json` to estimate recomputation cost
6. **Present a plan** for user approval (skip if `auto`)
7. **Implement changes** if approved
8. **Commit and push** (in `auto` mode), recording the checkpoint commit hash
9. **Wait for pipeline**, monitoring for errors and regressions
10. **Rollback if needed** - revert if pipeline errors or performance regresses
11. **Repeat** (if `loop` mode and iterations remain)

## Waiting for pipeline (JSON status)

Use the structured JSON status endpoint to poll pipeline state. This gives machine-readable data about errors, regressions, and progress.

```bash
uv run python src/kb/pipeline.py status --json
```

Poll with early error detection:

```python
import subprocess, json, time

def get_pipeline_status():
    result = subprocess.run(
        ["uv", "run", "python", "src/kb/pipeline.py", "status", "--json"],
        capture_output=True, text=True, cwd="."
    )
    return json.loads(result.stdout)

def wait_for_pipeline():
    """Poll pipeline with early error detection. Returns status dict."""
    while True:
        status = get_pipeline_status()

        if status["has_errors"]:
            error_names = [e["name"] for e in status["errors"]]
            print(f"Pipeline errors: {error_names}")
            return {"result": "error", "status": status}

        if status["all_fresh"]:
            regressions = status.get("regressions", [])
            if regressions:
                print(f"Performance regressions detected: {regressions}")
                return {"result": "regression", "status": status}
            return {"result": "complete", "status": status}

        running = [n["name"] for n in status["nodes"] if n["status"] == "running"]
        stale = status["summary"]["stale"]
        print(f"Pipeline: {stale} stale, running: {running}")
        time.sleep(15)
```

## How to proceed

First, read the review summary from the database:

```python
from src.kb.database import Database
from dotenv import load_dotenv
import os
import json

load_dotenv()
db = Database(os.environ['DATABASE_URL'])
summary = db.get_cache('pipeline:review_summary')
print(json.dumps(summary, indent=2))
db.close()
```

Then analyze the issues and propose fixes. For each selected issue:
1. Identify the file(s) that need to change
2. Read the current content
3. Propose specific edits

## Minimizing recomputation

When possible, prefer changes to VL-specific files over shared files:
- `data/frameworks/v2/vl1.md` through `vl5.md` - only affects one VL's subgraph
- `data/frameworks/v2/pass_b.md` - affects ALL 5 VLs (use only when the fix applies broadly)
- `src/kb/web.py` BOOKLET_QUERY_TYPES - affects ALL booklets

If a fix targets one VL's analysis, edit the VL-specific prompt file.

Configurable files:
- `src/kb/web.py` - BOOKLET_QUERY_TYPES dict contains all query prompts
- `data/frameworks/v2/frameworks.json` - VL framework definitions
- `data/frameworks/v2/vl1.md` through `vl5.md` - VL-specific analysis prompts
- `data/frameworks/v2/pass_a.md` - Pass A (segmentation) prompt
- `data/frameworks/v2/pass_b.md` - Pass B (synthesis) prompt
- `data/council_intro.html` - Council of Five static content

## Dry-run cost check

Before committing, estimate how much recomputation the changes will trigger:

```bash
uv run python src/kb/pipeline.py run --dry-run --json
```

This returns estimated durations based on historical data. If the total is unreasonably high, reconsider the changes (e.g., scope to one VL instead of all 5).

## After implementing

Re-run the affected pipeline nodes:
```bash
uv run python src/kb/pipeline.py invalidate "<affected_nodes>"
uv run python src/kb/pipeline.py run
```

## Rollback protocol

Before making changes, record the checkpoint:
```bash
git rev-parse HEAD  # save this as checkpoint_hash
```

After the pipeline completes, check for problems:
```python
status = get_pipeline_status()

# Check for errors
if status["has_errors"]:
    print("Errors detected, rolling back")
    # git revert HEAD --no-edit && git push

# Check for performance regressions (>2x slower than baseline)
if status["regressions"]:
    print(f"Regressions: {status['regressions']}")
    # git revert HEAD --no-edit && git push
```

If rollback is needed:
1. `git revert HEAD --no-edit`
2. `git push`
3. Log the revert in the changelog with the reason
4. Continue to next iteration (which will pick different issues)

## Changelog

After each iteration, append an entry to `data/archive/improve_book_changelog.md` with:

1. **Iteration number and date**
2. **Issues addressed** — rank and one-line summary from the review
3. **Changes made** — files modified and what changed (keep it brief)
4. **Commit hash**
5. **Performance** — booklet:VL-* generation times from `pipeline status --json` after the run completes
6. **Review impact** — did the addressed issues disappear from the next review? Did new issues appear? Note any regressions.
7. **Reverted/simplified** — if any changes were rolled back, note what and why

Also note any changes that were removed for being too expensive (LLM calls, prompt bloat).
See `data/archive/lens_synthesis_feature.md` for an example of documenting a removed feature.

## User input

$ARGUMENTS
