Restart the development server.

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; sleep 2 && cd /Users/michaelhollander/github/feldenkrais/src/kb && uv run uvicorn web:app --reload --port 8000 > /tmp/feldenkrais-server.log 2>&1 &
echo "Server restarted"
```

Then verify it's up:

```bash
sleep 3 && tail -5 /tmp/feldenkrais-server.log 2>/dev/null | strings
```
