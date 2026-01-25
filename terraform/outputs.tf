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

output "transcription_lambda_name" {
  description = "Transcription Lambda function name"
  value       = length(aws_lambda_function.transcription) > 0 ? aws_lambda_function.transcription[0].function_name : null
}

output "transcription_lambda_arn" {
  description = "Transcription Lambda function ARN"
  value       = length(aws_lambda_function.transcription) > 0 ? aws_lambda_function.transcription[0].arn : null
}

output "scraper_lambda_name" {
  description = "Scraper Lambda function name"
  value       = aws_lambda_function.scraper.function_name
}

output "scraper_lambda_arn" {
  description = "Scraper Lambda function ARN"
  value       = aws_lambda_function.scraper.arn
}
