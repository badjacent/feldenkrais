#!/bin/bash
# Batch decompose job submitted 2026-04-07
# 180 Jeff Haller lessons at 50% cost via Gemini Batch API

JOB=batches/sfvi1vbh8drv88yib10bp0kunnvd0hentbsd
CD="cd /Users/michaelhollander/github/feldenkrais/src/kb"

case "${1:-status}" in
  status)
    $CD && ../../.venv/bin/python -m pipeline.batch_api status "$JOB"
    ;;
  wait)
    $CD && ../../.venv/bin/python -m pipeline.batch_api wait "$JOB"
    ;;
  *)
    echo "Usage: $0 [status|wait]"
    ;;
esac
