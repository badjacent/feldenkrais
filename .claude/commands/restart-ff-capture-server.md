Restart the FF Capture server (localhost:7890) for capturing Feldenkrais First lessons.

```bash
lsof -ti:7890 | xargs kill -9 2>/dev/null; sleep 1 && /Users/michaelhollander/github/feldenkrais/.venv/bin/python /Users/michaelhollander/github/feldenkrais/tools/ff-capture/server.py > /tmp/ff-capture-server.log 2>&1 &
echo "FF Capture server restarted"
```

Then verify it's up:

```bash
sleep 3 && tail -10 /tmp/ff-capture-server.log 2>/dev/null | strings
```
