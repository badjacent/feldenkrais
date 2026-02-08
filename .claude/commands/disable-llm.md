Disable LLM calls.

## How to run

```bash
cd /Users/michaelhollander/github/feldenkrais/src/kb && ../../.venv/bin/python -c "
import os
from database import Database
from dotenv import load_dotenv
load_dotenv()
db = Database(os.environ['DATABASE_URL'])
db.set_cache('settings:llm_enabled', {'enabled': False})
print('LLM calls: DISABLED')
"
```

Report the new state to the user.
