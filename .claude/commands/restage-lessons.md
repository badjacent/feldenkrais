Find transcription jobs that are stuck or failed from the past day and restage them.

## What this does

Queries the `transcription_job` table for any jobs with `failed` status, then DELETE all transcription_job records for each affected document so it becomes eligible for re-transcription on the next Lambda run.

## Steps

1. **Find stuck/failed jobs from the past day**

Run this query via Python (using the project's database connection):

```python
import sys; sys.path.insert(0, 'src/kb')
from database import Database
import os
db = Database(os.environ.get('DATABASE_URL'))
```

```sql
SELECT j.document_id, d.title, j.status, j.attempts, j.error, j.batch_id,
       j.created_at, j.updated_at, j.chunk_index, j.total_chunks
FROM transcription_job j
JOIN document d ON d.id = j.document_id
WHERE j.status = 'failed'
ORDER BY d.title, j.chunk_index
```

2. **Show the user what was found** — list each document with its title, job count, statuses, and any error messages. If nothing found, say so and stop.

3. **Ask for confirmation** before deleting.

4. **Delete transcription jobs** for each affected document:

```sql
DELETE FROM transcription_job WHERE document_id = '<doc_id>'
```

5. **Verify** the documents are now eligible by checking they have no remaining transcription_job records.

6. **Report** what was restaged. Remind the user that the transcription Lambda will pick these up on its next scheduled run (every 1 minute if triggers are enabled), or they can manually invoke:

```bash
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "create_batch"}' /tmp/out.json && cat /tmp/out.json
```

## User input

$ARGUMENTS
