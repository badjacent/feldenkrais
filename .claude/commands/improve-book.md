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

1. **Wait for pipeline** (if `wait` or `loop` mode) - Poll until `review_summary` is fresh
2. **Read the review summary** from the pipeline cache
3. **Analyze which issues are actionable** - check actual files, understand what can be changed
4. **Select ~3 high-impact issues** that don't conflict with each other
5. **Present a plan** for user approval (skip if `auto`)
6. **Implement changes** if approved
7. **Commit and push** (in `auto` mode)
8. **Repeat** (if `loop` mode and iterations remain)

## Waiting for pipeline

To wait for the pipeline to complete, poll the node status:

```bash
# Check if review_summary is stale or running
uv run python src/kb/pipeline.py status 2>/dev/null | grep review_summary
```

If it shows `stale` or is missing, the pipeline is still running. Poll every 30 seconds until it shows `fresh`:

```python
import subprocess
import time

def is_review_summary_fresh():
    result = subprocess.run(
        ["uv", "run", "python", "src/kb/pipeline.py", "status"],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if "review_summary" in line and "fresh" in line:
            return True
    return False

# Poll until fresh
while not is_review_summary_fresh():
    print("Waiting for pipeline... (checking every 30s)")
    time.sleep(30)
print("Pipeline complete!")
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

Configurable files:
- `src/kb/web.py` - BOOKLET_QUERY_TYPES dict contains all query prompts
- `data/frameworks/v2/frameworks.json` - VL framework definitions
- `data/frameworks/v2/vl1.md` through `vl5.md` - VL-specific analysis prompts
- `data/frameworks/v2/pass_a.md` - Pass A (segmentation) prompt
- `data/frameworks/v2/pass_b.md` - Pass B (synthesis) prompt
- `data/council_intro.html` - Council of Five static content

## After implementing

Re-run the affected pipeline nodes to see if the changes improve the review:
```bash
uv run python src/kb/pipeline.py invalidate "<affected_nodes>"
uv run python src/kb/pipeline.py run
```

## User input

$ARGUMENTS
