terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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

  # Note: reserved_concurrent_executions = 1 would prevent race conditions,
  # but requires sufficient account concurrency quota. Removed for now.
  # reserved_concurrent_executions = 1

  environment {
    variables = {
      KB_BUCKET         = aws_s3_bucket.main.id
      KB_ENV            = var.environment
      KB_GRACE_PERIOD   = var.grace_period_seconds
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
