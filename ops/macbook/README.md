# Temporary MacBook transcription helper

This manual helper drains a small part of the shared Neon transcription queue
using audio from S3. Atomic database claims allow it to coexist with the Mini
worker. It runs transcription only; it does not invoke any lesson-analysis
stage.

Check the queue without claiming work:

```sh
ops/macbook/run-transcription-helper.sh status
```

After a one-record rollout test, run a bounded batch manually:

```sh
TRANSCRIPTION_HELPER_LIMIT=1 \
TRANSCRIPTION_HELPER_MAX_SECONDS=900 \
ops/macbook/run-transcription-helper.sh
```

The helper is intentionally not scheduled. Stop using it after the initial
backlog has fallen to an acceptable level.
