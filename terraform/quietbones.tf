# =============================================================================
# QuietBones — Landing page + email signup
# =============================================================================

variable "quietbones_domain" {
  description = "Domain for QuietBones landing page"
  type        = string
  default     = "quietbones.com"
}

# --- S3 bucket for audio files ---

resource "aws_s3_bucket" "quietbones" {
  bucket = "quietbones-audio-${var.environment}"
}

resource "aws_s3_bucket_public_access_block" "quietbones" {
  bucket                  = aws_s3_bucket.quietbones.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Lambda ---

data "archive_file" "quietbones" {
  type        = "zip"
  source_dir  = "${path.module}/../quietbones/lambda"
  output_path = "${path.module}/lambda_quietbones.zip"
}

resource "aws_lambda_function" "quietbones" {
  function_name    = "quietbones-${var.environment}"
  filename         = data.archive_file.quietbones.output_path
  source_code_hash = data.archive_file.quietbones.output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128
  role             = aws_iam_role.quietbones.arn

  layers = [
    length(aws_lambda_layer_version.python_deps) > 0 ? aws_lambda_layer_version.python_deps[0].arn : null,
  ]

  environment {
    variables = {
      DATABASE_URL      = var.database_url
      QUIETBONES_BUCKET = aws_s3_bucket.quietbones.bucket
    }
  }
}

# --- IAM ---

resource "aws_iam_role" "quietbones" {
  name = "quietbones-lambda-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "quietbones_logs" {
  role       = aws_iam_role.quietbones.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "quietbones_s3" {
  name = "quietbones-s3-access"
  role = aws_iam_role.quietbones.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.quietbones.arn}/*"
    }]
  })
}

# --- API Gateway ---

resource "aws_apigatewayv2_api" "quietbones" {
  name          = "quietbones-${var.environment}"
  protocol_type = "HTTP"
  target        = aws_lambda_function.quietbones.arn
}

resource "aws_lambda_permission" "quietbones_api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.quietbones.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.quietbones.execution_arn}/*"
}

# --- Route 53 + ACM ---

data "aws_route53_zone" "quietbones" {
  count = var.quietbones_domain != "" ? 1 : 0
  name  = var.quietbones_domain
}

resource "aws_acm_certificate" "quietbones" {
  count             = var.quietbones_domain != "" ? 1 : 0
  domain_name       = var.quietbones_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "quietbones_cert_validation" {
  for_each = var.quietbones_domain != "" ? {
    for dvo in aws_acm_certificate.quietbones[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.quietbones[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "quietbones" {
  count                   = var.quietbones_domain != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.quietbones[0].arn
  validation_record_fqdns = [for record in aws_route53_record.quietbones_cert_validation : record.fqdn]
}

resource "aws_apigatewayv2_domain_name" "quietbones" {
  count       = var.quietbones_domain != "" ? 1 : 0
  domain_name = var.quietbones_domain

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.quietbones[0].certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "quietbones" {
  count       = var.quietbones_domain != "" ? 1 : 0
  api_id      = aws_apigatewayv2_api.quietbones.id
  domain_name = aws_apigatewayv2_domain_name.quietbones[0].domain_name
  stage       = "$default"
}

resource "aws_route53_record" "quietbones" {
  count   = var.quietbones_domain != "" ? 1 : 0
  zone_id = data.aws_route53_zone.quietbones[0].zone_id
  name    = var.quietbones_domain
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.quietbones[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.quietbones[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}
