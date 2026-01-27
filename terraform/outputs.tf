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

output "youtube_lambda_name" {
  description = "YouTube importer Lambda function name"
  value       = length(aws_lambda_function.youtube) > 0 ? aws_lambda_function.youtube[0].function_name : null
}

output "youtube_lambda_arn" {
  description = "YouTube importer Lambda function ARN"
  value       = length(aws_lambda_function.youtube) > 0 ? aws_lambda_function.youtube[0].arn : null
}

output "web_lambda_name" {
  description = "Web Lambda function name"
  value       = aws_lambda_function.web.function_name
}

output "web_api_url" {
  description = "Web API Gateway URL (public endpoint)"
  value       = aws_apigatewayv2_api.web.api_endpoint
}
