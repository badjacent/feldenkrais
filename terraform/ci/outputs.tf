output "role_arn" {
  description = "ARN of the GitHub Actions deploy role — set as AWS_ROLE_ARN secret in GitHub"
  value       = aws_iam_role.github_deploy.arn
}

output "state_bucket" {
  description = "S3 bucket for Terraform state"
  value       = aws_s3_bucket.terraform_state.id
}

output "lock_table" {
  description = "DynamoDB table for Terraform state locking"
  value       = aws_dynamodb_table.terraform_lock.name
}
