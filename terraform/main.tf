terraform {
  required_version = ">= 1.0"

  backend "s3" {
    bucket         = "feldenkrais-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "feldenkrais-terraform-lock"
    encrypt        = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  bucket_name = "feldenkrais-${var.environment}"
}

# -----------------------------------------------------------------------------
# S3 Bucket
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "main" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Create folder structure by uploading empty objects
resource "aws_s3_object" "staging_folder" {
  bucket = aws_s3_bucket.main.id
  key    = "staging/"
}

resource "aws_s3_object" "archive_folder" {
  bucket = aws_s3_bucket.main.id
  key    = "archive/"
}

resource "aws_s3_object" "errors_folder" {
  bucket = aws_s3_bucket.main.id
  key    = "errors/"
}

resource "aws_s3_object" "db_folder" {
  bucket = aws_s3_bucket.main.id
  key    = "db/"
}

# -----------------------------------------------------------------------------
# IAM Role for Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "loader_lambda" {
  name = "feldenkrais-loader-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "loader_lambda_s3" {
  name = "s3-access"
  role = aws_iam_role.loader_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "loader_lambda_logs" {
  role       = aws_iam_role.loader_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

data "archive_file" "loader_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src/kb"
  output_path = "${path.module}/lambda_loader.zip"
}

resource "aws_lambda_function" "loader" {
  filename         = data.archive_file.loader_lambda.output_path
  function_name    = "feldenkrais-loader-${var.environment}"
  role             = aws_iam_role.loader_lambda.arn
  handler          = "loader.handler"
  source_code_hash = data.archive_file.loader_lambda.output_base64sha256
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512

  layers = compact([
    length(aws_lambda_layer_version.python_deps) > 0 ? aws_lambda_layer_version.python_deps[0].arn : null,
  ])

  # Note: reserved_concurrent_executions = 1 would prevent race conditions,
  # but requires sufficient account concurrency quota.
  # Instead, we use a UNIQUE constraint in the database.

  environment {
    variables = {
      KB_BUCKET       = aws_s3_bucket.main.id
      KB_ENV          = var.environment
      KB_GRACE_PERIOD = var.grace_period_seconds
      DATABASE_URL    = var.database_url
    }
  }
}

# -----------------------------------------------------------------------------
# S3 Event Trigger
# -----------------------------------------------------------------------------

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.loader.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.main.arn
}

resource "aws_s3_bucket_notification" "staging_trigger" {
  bucket = aws_s3_bucket.main.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.loader.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "staging/"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}

# Scheduled trigger to process any remaining files in staging/
resource "aws_cloudwatch_event_rule" "loader_schedule" {
  count               = var.enable_scheduled_triggers ? 1 : 0
  name                = "feldenkrais-loader-${var.environment}"
  description         = "Trigger loader Lambda periodically to process remaining files"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "loader_target" {
  count     = var.enable_scheduled_triggers ? 1 : 0
  rule      = aws_cloudwatch_event_rule.loader_schedule[0].name
  target_id = "loader"
  arn       = aws_lambda_function.loader.arn
}

resource "aws_lambda_permission" "allow_eventbridge_loader" {
  count         = var.enable_scheduled_triggers ? 1 : 0
  statement_id  = "AllowEventBridgeInvokeLoader"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.loader.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.loader_schedule[0].arn
}

# -----------------------------------------------------------------------------
# Transcription Lambda
# -----------------------------------------------------------------------------

# Build Python dependencies layer
# Run: ./build_deps_layer.sh before terraform apply
resource "aws_lambda_layer_version" "python_deps" {
  count = fileexists("${path.module}/lambda_deps.zip") ? 1 : 0

  filename            = "${path.module}/lambda_deps.zip"
  layer_name          = "feldenkrais-deps-${var.environment}"
  compatible_runtimes = ["python3.12"]
  description         = "Python dependencies for feldenkrais Lambda functions"
}

# IAM Role for Transcription Lambda
resource "aws_iam_role" "transcription_lambda" {
  name = "feldenkrais-transcription-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "transcription_lambda_s3" {
  name = "s3-access"
  role = aws_iam_role.transcription_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "transcription_lambda_logs" {
  role       = aws_iam_role.transcription_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "transcription_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src/kb"
  output_path = "${path.module}/lambda_transcription.zip"
}

resource "aws_lambda_function" "transcription" {
  count = var.groq_api_key != "" ? 1 : 0

  filename         = data.archive_file.transcription_lambda.output_path
  function_name    = "feldenkrais-transcription-${var.environment}"
  role             = aws_iam_role.transcription_lambda.arn
  handler          = "transcribe.handler"
  source_code_hash = data.archive_file.transcription_lambda.output_base64sha256
  runtime          = "python3.12"
  timeout          = 900 # 15 minutes max for long transcriptions
  memory_size      = 1024

  ephemeral_storage {
    size = 2048 # 2GB for downloading and chunking multiple audio files
  }

  # Note: reserved_concurrent_executions = 1 would prevent race conditions,
  # but account lacks sufficient concurrency quota. Using database-level
  # locking instead (transcription_job records prevent duplicate processing).

  layers = compact([
    length(aws_lambda_layer_version.python_deps) > 0 ? aws_lambda_layer_version.python_deps[0].arn : null,
    var.ffmpeg_layer_arn != "" ? var.ffmpeg_layer_arn : null
  ])

  environment {
    variables = {
      KB_BUCKET    = aws_s3_bucket.main.id
      KB_ENV       = var.environment
      GROQ_API_KEY = var.groq_api_key
      DATABASE_URL = var.database_url
      PATH         = "/opt/bin:/var/lang/bin:/usr/local/bin:/usr/bin:/bin"
    }
  }
}

# EventBridge rule to trigger transcription periodically
resource "aws_cloudwatch_event_rule" "transcription_schedule" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  name                = "feldenkrais-transcription-${var.environment}"
  description         = "Trigger transcription Lambda periodically"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "transcription_target" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  rule      = aws_cloudwatch_event_rule.transcription_schedule[0].name
  target_id = "transcription"
  arn       = aws_lambda_function.transcription[0].arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transcription[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.transcription_schedule[0].arn
}

# -----------------------------------------------------------------------------
# Scraper Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "scraper_lambda" {
  name = "feldenkrais-scraper-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scraper_lambda_s3" {
  name = "s3-access"
  role = aws_iam_role.scraper_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.main.arn}/staging/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "scraper_lambda_logs" {
  role       = aws_iam_role.scraper_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "scraper_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src/kb"
  output_path = "${path.module}/lambda_scraper.zip"
}

resource "aws_lambda_function" "scraper" {
  filename         = data.archive_file.scraper_lambda.output_path
  function_name    = "feldenkrais-scraper-${var.environment}"
  role             = aws_iam_role.scraper_lambda.arn
  handler          = "scraper.handler"
  source_code_hash = data.archive_file.scraper_lambda.output_base64sha256
  runtime          = "python3.12"
  timeout          = 600  # 10 minutes for large downloads + chunking
  memory_size      = 1024 # More memory for ffmpeg chunking

  # Use Python deps layer (requests, beautifulsoup4) and ffmpeg for chunking
  layers = compact([
    length(aws_lambda_layer_version.python_deps) > 0 ? aws_lambda_layer_version.python_deps[0].arn : null,
    var.ffmpeg_layer_arn != "" ? var.ffmpeg_layer_arn : null
  ])

  environment {
    variables = {
      KB_BUCKET    = aws_s3_bucket.main.id
      KB_ENV       = var.environment
      DATABASE_URL = var.database_url
      PATH         = "/opt/bin:/var/lang/bin:/usr/local/bin:/usr/bin:/bin"
    }
  }
}

# -----------------------------------------------------------------------------
# YouTube Importer Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "youtube_lambda" {
  name = "feldenkrais-youtube-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "youtube_lambda_s3" {
  name = "s3-access"
  role = aws_iam_role.youtube_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "youtube_lambda_logs" {
  role       = aws_iam_role.youtube_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "youtube_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src/kb"
  output_path = "${path.module}/lambda_youtube.zip"
}

resource "aws_lambda_function" "youtube" {
  count = var.groq_api_key != "" ? 1 : 0 # Reuse same condition as transcription

  filename         = data.archive_file.youtube_lambda.output_path
  function_name    = "feldenkrais-youtube-${var.environment}"
  role             = aws_iam_role.youtube_lambda.arn
  handler          = "youtube.handler"
  source_code_hash = data.archive_file.youtube_lambda.output_base64sha256
  runtime          = "python3.12"
  timeout          = 900 # 15 minutes max for large video downloads
  memory_size      = 1024

  # Use Python deps layer (includes yt-dlp) and ffmpeg for audio extraction
  layers = compact([
    length(aws_lambda_layer_version.python_deps) > 0 ? aws_lambda_layer_version.python_deps[0].arn : null,
    var.ffmpeg_layer_arn != "" ? var.ffmpeg_layer_arn : null
  ])

  environment {
    variables = {
      KB_BUCKET    = aws_s3_bucket.main.id
      KB_ENV       = var.environment
      DATABASE_URL = var.database_url
      PATH         = "/opt/bin:/var/lang/bin:/usr/local/bin:/usr/bin:/bin"
    }
  }
}

# EventBridge rule to process YouTube queue periodically
resource "aws_cloudwatch_event_rule" "youtube_schedule" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  name                = "feldenkrais-youtube-${var.environment}"
  description         = "Trigger YouTube importer Lambda periodically to process queue"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "youtube_target" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  rule      = aws_cloudwatch_event_rule.youtube_schedule[0].name
  target_id = "youtube"
  arn       = aws_lambda_function.youtube[0].arn

  input = jsonencode({
    action = "process"
    limit  = 1
  })
}

resource "aws_lambda_permission" "allow_eventbridge_youtube" {
  count = var.groq_api_key != "" && var.enable_scheduled_triggers ? 1 : 0

  statement_id  = "AllowEventBridgeInvokeYouTube"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.youtube[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.youtube_schedule[0].arn
}

# -----------------------------------------------------------------------------
# Web Lambda (Lesson Analyzer)
# -----------------------------------------------------------------------------

# Separate dependencies layer for web Lambda (FastAPI, LLM clients, etc.)
resource "aws_lambda_layer_version" "web_deps" {
  count               = fileexists("${path.module}/lambda_web_deps.zip") ? 1 : 0
  filename            = "${path.module}/lambda_web_deps.zip"
  layer_name          = "feldenkrais-web-deps-${var.environment}"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = filebase64sha256("${path.module}/lambda_web_deps.zip")
  description         = "Python dependencies for web Lambda (FastAPI, Mangum, LLM clients)"
}

resource "aws_iam_role" "web_lambda" {
  name = "feldenkrais-web-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "web_lambda_logs" {
  role       = aws_iam_role.web_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# S3 access for web Lambda (for async job storage)
resource "aws_iam_role_policy" "web_lambda_s3" {
  name = "s3-access"
  role = aws_iam_role.web_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.main.arn}/web-jobs/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.main.arn}/archive/*"
      }
    ]
  })
}

# Allow web Lambda to invoke itself asynchronously
resource "aws_iam_role_policy" "web_lambda_invoke" {
  name = "lambda-invoke"
  role = aws_iam_role.web_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.web.arn
      }
    ]
  })
}

# Package web Lambda code (web.py, llm.py, schemas.py, and data files)
data "archive_file" "web_lambda" {
  type        = "zip"
  output_path = "${path.module}/lambda_web.zip"

  source {
    content  = file("${path.module}/../src/kb/web.py")
    filename = "web.py"
  }
  source {
    content  = file("${path.module}/../src/kb/llm.py")
    filename = "llm.py"
  }
  # Framework version pointer (from terraform/data/ deployment copy)
  source {
    content  = file("${path.module}/data/frameworks.json")
    filename = "data/frameworks.json"
  }
  # Schemas
  source {
    content  = file("${path.module}/../data/pass_a.schema.json")
    filename = "data/pass_a.schema.json"
  }
  source {
    content  = file("${path.module}/data/pass_b.schema.json")
    filename = "data/pass_b.schema.json"
  }
  # Versioned framework prompts (v1) - from terraform/data/ deployment copy
  source {
    content  = file("${path.module}/data/frameworks/v1/frameworks.json")
    filename = "data/frameworks/v1/frameworks.json"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/pass_a.md")
    filename = "data/frameworks/v1/pass_a.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/pass_b.md")
    filename = "data/frameworks/v1/pass_b.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/vl1.md")
    filename = "data/frameworks/v1/vl1.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/vl2.md")
    filename = "data/frameworks/v1/vl2.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/vl3.md")
    filename = "data/frameworks/v1/vl3.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/vl4.md")
    filename = "data/frameworks/v1/vl4.md"
  }
  source {
    content  = file("${path.module}/data/frameworks/v1/vl5.md")
    filename = "data/frameworks/v1/vl5.md"
  }
  # Database module
  source {
    content  = file("${path.module}/../src/kb/database.py")
    filename = "database.py"
  }
  # Storage module
  source {
    content  = file("${path.module}/../src/kb/storage.py")
    filename = "storage.py"
  }
  # Extracted modules (required by web.py)
  source {
    content  = file("${path.module}/../src/kb/cache_helpers.py")
    filename = "cache_helpers.py"
  }
  source {
    content  = file("${path.module}/../src/kb/llm_config.py")
    filename = "llm_config.py"
  }
  source {
    content  = file("${path.module}/../src/kb/analysis.py")
    filename = "analysis.py"
  }
  source {
    content  = file("${path.module}/../src/kb/config.py")
    filename = "config.py"
  }
  source {
    content  = file("${path.module}/../src/kb/auth.py")
    filename = "auth.py"
  }
  # Pass 0b data files (prompts and schemas)
  source {
    content  = file("${path.module}/../data/pass_0b.md")
    filename = "data/pass_0b.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b.schema.json")
    filename = "data/pass_0b.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_condensed.md")
    filename = "data/pass_0b_condensed.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_condensed.schema.json")
    filename = "data/pass_0b_condensed.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_condensed_instruction.md")
    filename = "data/pass_0b_condensed_instruction.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_condensed_segment.md")
    filename = "data/pass_0b_condensed_segment.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_lang.md")
    filename = "data/pass_0b_lang.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_lang.schema.json")
    filename = "data/pass_0b_lang.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_segmentation.md")
    filename = "data/pass_0b_segmentation.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_segmentation.schema.json")
    filename = "data/pass_0b_segmentation.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_word_lang.md")
    filename = "data/pass_0b_word_lang.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_word_lang.schema.json")
    filename = "data/pass_0b_word_lang.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_concepts.md")
    filename = "data/pass_0b_concepts.md"
  }
  source {
    content  = file("${path.module}/../data/pass_0b_concepts.schema.json")
    filename = "data/pass_0b_concepts.schema.json"
  }
  source {
    content  = file("${path.module}/../data/pass_0.schema.json")
    filename = "data/pass_0.schema.json"
  }
  # Exploration agent (from terraform/data/ deployment copy)
  source {
    content  = file("${path.module}/data/explore_init.md")
    filename = "data/explore_init.md"
  }
  source {
    content  = file("${path.module}/data/explore_round.md")
    filename = "data/explore_round.md"
  }
  source {
    content  = file("${path.module}/data/explore.schema.json")
    filename = "data/explore.schema.json"
  }
  # Pipeline package (required by web.py)
  source {
    content  = file("${path.module}/../src/kb/pipeline/__init__.py")
    filename = "pipeline/__init__.py"
  }
  source {
    content  = file("${path.module}/../src/kb/pipeline/engine.py")
    filename = "pipeline/engine.py"
  }
  # Note: Lesson files are loaded dynamically from S3 or user input
}

resource "aws_lambda_function" "web" {
  filename         = data.archive_file.web_lambda.output_path
  function_name    = "feldenkrais-web-${var.environment}"
  role             = aws_iam_role.web_lambda.arn
  handler          = "web.handler"
  source_code_hash = data.archive_file.web_lambda.output_base64sha256
  runtime          = "python3.12"
  timeout          = 900 # 15 minutes for LLM calls
  memory_size      = 512

  layers = compact([
    length(aws_lambda_layer_version.web_deps) > 0 ? aws_lambda_layer_version.web_deps[0].arn : null,
  ])

  environment {
    variables = {
      GOOGLE_API_KEY                = var.google_api_key
      ANTHROPIC_API_KEY             = var.anthropic_api_key
      OPENAI_API_KEY                = var.openai_api_key
      DATABASE_URL                  = var.database_url
      KB_BUCKET                     = aws_s3_bucket.main.id
      AWS_LAMBDA_FUNCTION_NAME_SELF = "feldenkrais-web-${var.environment}"
      DEPLOY_SHA                    = var.deploy_sha
      SEARCH_DOMAIN                 = var.search_domain_name
      GITHUB_CLIENT_ID              = var.github_client_id
      GITHUB_CLIENT_SECRET          = var.github_client_secret
      GOOGLE_OAUTH_CLIENT_ID        = var.google_oauth_client_id
      GOOGLE_OAUTH_CLIENT_SECRET    = var.google_oauth_client_secret
      COOKIE_SECRET                 = var.cookie_secret
    }
  }
}

# HTTP API Gateway (public HTTPS endpoint)
resource "aws_apigatewayv2_api" "web" {
  name          = "feldenkrais-web-${var.environment}"
  protocol_type = "HTTP"
  target        = aws_lambda_function.web.arn
}

resource "aws_lambda_permission" "web_api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.web.execution_arn}/*"
}

# -----------------------------------------------------------------------------
# Custom Domain (Route 53 + ACM + API Gateway)
# -----------------------------------------------------------------------------

# Look up the Route 53 hosted zone (auto-created when domain was registered)
data "aws_route53_zone" "main" {
  count = var.domain_name != "" ? 1 : 0
  name  = var.domain_name
}

# ACM certificate
resource "aws_acm_certificate" "web" {
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# DNS validation record
resource "aws_route53_record" "cert_validation" {
  for_each = var.domain_name != "" ? {
    for dvo in aws_acm_certificate.web[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

# Wait for certificate validation
resource "aws_acm_certificate_validation" "web" {
  count                   = var.domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.web[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

# API Gateway custom domain
resource "aws_apigatewayv2_domain_name" "web" {
  count       = var.domain_name != "" ? 1 : 0
  domain_name = var.domain_name

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.web[0].certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

# Map the custom domain to the API
resource "aws_apigatewayv2_api_mapping" "web" {
  count       = var.domain_name != "" ? 1 : 0
  api_id      = aws_apigatewayv2_api.web.id
  domain_name = aws_apigatewayv2_domain_name.web[0].domain_name
  stage       = "$default"
}

# Route 53 alias record pointing domain to API Gateway
resource "aws_route53_record" "web" {
  count   = var.domain_name != "" ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.web[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.web[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}

# -----------------------------------------------------------------------------
# Search Subdomain (Route 53 + ACM + API Gateway) — same Lambda, different host
# -----------------------------------------------------------------------------

locals {
  search_parent_domain = var.search_domain_name != "" ? join(".", slice(split(".", var.search_domain_name), 1, length(split(".", var.search_domain_name)))) : ""
}

data "aws_route53_zone" "search" {
  count = var.search_domain_name != "" ? 1 : 0
  name  = local.search_parent_domain
}

resource "aws_acm_certificate" "search" {
  count             = var.search_domain_name != "" ? 1 : 0
  domain_name       = var.search_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "search_cert_validation" {
  for_each = var.search_domain_name != "" ? {
    for dvo in aws_acm_certificate.search[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.search[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "search" {
  count                   = var.search_domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.search[0].arn
  validation_record_fqdns = [for record in aws_route53_record.search_cert_validation : record.fqdn]
}

resource "aws_apigatewayv2_domain_name" "search" {
  count       = var.search_domain_name != "" ? 1 : 0
  domain_name = var.search_domain_name

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.search[0].certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "search" {
  count       = var.search_domain_name != "" ? 1 : 0
  api_id      = aws_apigatewayv2_api.web.id
  domain_name = aws_apigatewayv2_domain_name.search[0].domain_name
  stage       = "$default"
}

resource "aws_route53_record" "search" {
  count   = var.search_domain_name != "" ? 1 : 0
  zone_id = data.aws_route53_zone.search[0].zone_id
  name    = var.search_domain_name
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.search[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.search[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
