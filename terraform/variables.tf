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

variable "groq_api_key" {
  description = "Groq API key for transcription"
  type        = string
  sensitive   = true
  default     = ""
}

variable "ffmpeg_layer_arn" {
  description = "ARN of FFmpeg Lambda layer (deploy from serverlessrepo or use public layer)"
  type        = string
  default     = ""
}

variable "database_url" {
  description = "Neon PostgreSQL connection string"
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_scheduled_triggers" {
  description = "Enable scheduled EventBridge triggers for all Lambdas. Set to false to disable all schedules."
  type        = bool
  default     = true
}
