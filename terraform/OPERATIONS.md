# Operations Guide

## Scheduled Lambda Triggers

All Lambda functions can be triggered on a schedule via EventBridge. These schedules can be enabled/disabled via the `enable_scheduled_triggers` variable.

### Current Schedules (when enabled)
- **Loader**: Every 1 minute - processes files in staging/
- **Transcription**: Every 1 minute - creates batches and processes results
- **YouTube**: Every 5 minutes - processes YouTube queue

### Disable All Schedules
```bash
cd terraform
terraform apply -var="enable_scheduled_triggers=false" \
  -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

### Re-enable All Schedules
```bash
cd terraform
terraform apply -var="enable_scheduled_triggers=true" \
  -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

Or simply omit the variable (defaults to true):
```bash
cd terraform
terraform apply -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

## Manual Lambda Invocation

Lambdas can still be invoked manually when schedules are disabled:

### Loader
```bash
aws lambda invoke --function-name feldenkrais-loader-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /tmp/out.json
```

### Transcription
```bash
# Process completed batches and create new ones
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "create_batch"}' /tmp/out.json

# Retry failed jobs
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "retry_failed"}' /tmp/out.json

# Process specific batch results
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "process_results", "batch_id": "batch_xxx"}' /tmp/out.json
```

### YouTube
```bash
# Process queue
aws lambda invoke --function-name feldenkrais-youtube-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "process", "limit": 5}' /tmp/out.json

# Add URL to queue
aws lambda invoke --function-name feldenkrais-youtube-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "add", "url": "https://youtube.com/watch?v=xxx"}' /tmp/out.json
```
