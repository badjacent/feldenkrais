# Protocol and Schema Management Guide

This guide explains how to manage Feldenkrais lesson analysis protocols and their associated JSON schemas.

## Directory Structure

```
data/
  washing.md                    # Current active protocol
  washing.schema.json           # Current active JSON schema
  washing-archive/              # Archived protocol versions
    washing_original.md         # Original v1 protocol
    washing_original.schema.json # Original v1 schema
    washing_v2.md               # (example) Archived v2 protocol
    washing_v2.schema.json      # (example) Archived v2 schema
```

## File Pairing Convention

Each protocol markdown file has a corresponding `.schema.json` file with the same base name:

- `washing.md` → `washing.schema.json`
- `washing_original.md` → `washing_original.schema.json`

## How to Create a New Protocol Version

### 1. Archive the Current Protocol

Before making changes, archive the current version:

```bash
# Copy current protocol to archive with version suffix
cp data/washing.md data/washing-archive/washing_v2.md
cp data/washing.schema.json data/washing-archive/washing_v2.schema.json
```

### 2. Edit the Protocol

Modify `data/washing.md` with your new protocol structure. Changes may include:
- Adding/removing/renaming phases
- Changing table columns
- Adding new tags or fields

### 3. Update the Pydantic Schema

Edit `src/kb/schemas.py` to match the new protocol structure:

1. Update field names and types to match protocol changes
2. Update field descriptions to match protocol language
3. Add/remove classes as needed for new phases or structures

### 4. Export the JSON Schema

After updating the Pydantic models, export to JSON Schema:

```python
# Run from project root
import sys
sys.path.insert(0, 'src/kb')
import json
from schemas import LessonAnalysis

schema = LessonAnalysis.model_json_schema()
schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
schema['description'] = 'Feldenkrais Lesson Analysis Protocol (VL-1 vX.X)'

with open('data/washing.schema.json', 'w') as f:
    json.dump(schema, f, indent=2)
```

Or as a one-liner:

```bash
python -c "
import sys; sys.path.insert(0, 'src/kb')
import json
from schemas import LessonAnalysis
schema = LessonAnalysis.model_json_schema()
schema['\$schema'] = 'https://json-schema.org/draft/2020-12/schema'
open('data/washing.schema.json', 'w').write(json.dumps(schema, indent=2))
"
```

### 5. Update the Web Renderer

Edit `src/kb/web.py` function `render_analysis()` to match the new schema structure:
- Update field references to match new Pydantic model names
- Update table headers to match new columns
- Update section headers to match new phase names

## Schema Structure

The JSON Schema defines:

- **Top-level fields**: `lesson_title`, phase objects
- **Phase objects**: Each phase has its own structure with required/optional fields
- **Table rows**: Array of objects with specific columns
- **Enums**: For constrained values (e.g., `NegotiationState`)

## How to Load a Specific Protocol/Schema

To use an archived protocol instead of the current one:

```python
from pathlib import Path
import json

# Load protocol text
protocol = Path('data/washing-archive/washing_original.md').read_text()

# Load corresponding schema
schema = json.loads(Path('data/washing-archive/washing_original.schema.json').read_text())
```

## Validation

To validate that your Pydantic schema matches your JSON Schema:

```python
import sys
sys.path.insert(0, 'src/kb')
from schemas import LessonAnalysis

# This should match what's in washing.schema.json
generated = LessonAnalysis.model_json_schema()
```

## Key Pydantic Features Used

- `Field(description="...")` - Descriptions appear in JSON Schema
- `Literal["A", "B", "C"]` - Creates enum constraints
- `list[SomeModel]` - Creates array of objects
- `Optional[str]` - Makes field not required

## Troubleshooting

**Schema out of sync with protocol**: Re-export JSON Schema after editing Pydantic models.

**LLM not following schema**: Check that field descriptions clearly explain expected content.

**Truncated output**: Increase `default_max_tokens` in `get_llm()` or simplify the schema.
