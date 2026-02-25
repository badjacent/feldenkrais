Start the FF Capture server for capturing Feldenkrais First lessons.

First kill any existing instance:

```bash
lsof -ti:7890 | xargs kill -9 2>/dev/null; sleep 1
```

Then start the server in the background:

```bash
cd /Users/michaelhollander/github/feldenkrais && .venv/bin/python tools/ff-capture/server.py > /tmp/ff-capture-server.log 2>&1 &
echo "FF Capture server starting..."
```

Verify it's up:

```bash
sleep 2 && curl -s http://localhost:7890/health
```
