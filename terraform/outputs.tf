output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.main.id
}

output "lambda_function_name" {
  description = "Loader Lambda function name"
  value       = aws_lambda_function.loader.function_name
}

output "lambda_function_arn" {
  description = "Loader Lambda function ARN"
  value       = aws_lambda_function.loader.arn
}
