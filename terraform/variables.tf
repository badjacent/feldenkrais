variable "environment" {
  description = "Environment name (dev, test, prod)"
  type        = string
  default     = "test"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "grace_period_seconds" {
  description = "Grace period for processing files (seconds)"
  type        = number
  default     = 300
}
