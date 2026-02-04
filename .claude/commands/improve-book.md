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

**IMPORTANT: If `wait`, `loop`, or if the pipeline has any stale/running nodes, you MUST wait for the pipeline to finish BEFORE reading the review summary. Do not skip this step.**

1. **Wait for pipeline** - Run the wait_for_pipeline() function below. This is MANDATORY for `loop` and `wait` modes, and RECOMMENDED always. Do not proceed to step 2 until it returns "complete".
2. **Read the review summary** from the pipeline cache
3. **Analyze which issues are actionable** - check actual files, understand what can be changed
4. **Select ~3 high-impact issues** that don't conflict with each other
5. **Dry-run cost check** - run `pipeline.py run --dry-run --json` to estimate recomputation cost
6. **Present a plan** for user approval (skip if `auto`)
7. **Acquire pipeline lock, then implement changes** - grab the lock BEFORE editing any files so the pipeline doesn't start processing partial changes
8. **Commit and push, then release pipeline lock** (in `auto` mode), recording the checkpoint commit hash
9. **Wait for pipeline again** - MANDATORY. Poll until complete, monitoring for errors and regressions
10. **Rollback if needed** - revert if pipeline errors or performance regresses
11. **Repeat** from step 1 (if `loop` mode and iterations remain)

## Step 1: Waiting for pipeline (MANDATORY)

You MUST run this before reading the review summary. Execute this as a bash script and wait for it to complete:

```bash
uv run python -c "
import subprocess, json, time, sys

def get_pipeline_status():
    result = subprocess.run(
        ['uv', 'run', 'python', 'src/kb/pipeline.py', 'status', '--json'],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)

while True:
    status = get_pipeline_status()
    if status['has_errors']:
        names = [e['name'] for e in status['errors']]
        print(f'ERRORS: {names}', flush=True)
        sys.exit(1)
    if status['all_fresh']:
        print('Pipeline complete - all nodes fresh', flush=True)
        break
    running = [n['name'] for n in status['nodes'] if n['status'] == 'running']
    stale = status['summary']['stale']
    print(f'Waiting... {stale} stale, running: {running}', flush=True)
    time.sleep(15)
"
```

If this exits with an error, check the error nodes and decide whether to proceed or abort.

## Step 2: Read the review summary

Only after the pipeline wait completes successfully, read the review summary:

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

## Step 7: Acquire pipeline lock

**MANDATORY** before editing any files. This prevents the pipeline from processing partial changes. The lock auto-expires after 5 minutes.

```bash
uv run python -c "
from dotenv import load_dotenv
import os, sys
load_dotenv()
sys.path.insert(0, 'src/kb')
from web import acquire_pipeline_lock
if acquire_pipeline_lock('improve-book iteration'):
    print('Lock acquired')
else:
    print('ERROR: Lock already held, waiting...')
    import time
    for i in range(30):
        time.sleep(10)
        if acquire_pipeline_lock('improve-book iteration'):
            print('Lock acquired')
            break
    else:
        print('FAILED to acquire lock after 5 minutes')
        sys.exit(1)
"
```

## Step 8: Release pipeline lock

**MANDATORY** after committing and pushing. Run this AFTER `git push`:

```bash
uv run python -c "
from dotenv import load_dotenv
import os, sys
load_dotenv()
sys.path.insert(0, 'src/kb')
from web import release_pipeline_lock
release_pipeline_lock()
"
```

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
