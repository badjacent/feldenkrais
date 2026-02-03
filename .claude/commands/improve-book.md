Analyze the book review summary and plan improvements.

## What this does

Reads the ranked issue list from `pipeline:review_summary` and creates an actionable plan to address the top issues.

## Prerequisites

The pipeline must have run through `review_summary`:
```bash
uv run python src/kb/pipeline.py status | grep review_summary
```

If stale, run:
```bash
uv run python src/kb/pipeline.py run --node review_summary
```

## Workflow

1. **Read the review summary** from the pipeline cache
2. **Analyze which issues are actionable** - check actual files, understand what can be changed
3. **Select ~3 high-impact issues** that don't conflict with each other
4. **Present a plan** for user approval
5. **Implement changes** if approved

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
