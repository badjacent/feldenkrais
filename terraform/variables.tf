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
  description = "ARN of FFmpeg Lambda layer"
  type        = string
  default     = "arn:aws:lambda:us-east-1:355854622119:layer:ffmpeg:1"
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

variable "google_api_key" {
  description = "Google API key for Gemini"
  type        = string
  sensitive   = true
  default     = ""
}

variable "anthropic_api_key" {
  description = "Anthropic API key for Claude"
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key for GPT"
  type        = string
  sensitive   = true
  default     = ""
}

variable "domain_name" {
  description = "Custom domain name for the web app"
  type        = string
  default     = "aionyourside.net"
}

variable "deploy_sha" {
  description = "Git commit SHA of the current deploy"
  type        = string
  default     = "local"
}

variable "search_domain_name" {
  description = "Subdomain for the pass0b search demo site"
  type        = string
  default     = ""
}

variable "finder_domain_name" {
  description = "Subdomain for the external lesson finder (finder.becomecomfortable.com)"
  type        = string
  default     = ""
}

variable "github_client_id" {
  description = "GitHub OAuth app client ID"
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_client_secret" {
  description = "GitHub OAuth app client secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_oauth_client_id" {
  description = "Google OAuth client ID"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth client secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "cookie_secret" {
  description = "Secret key for signing session cookies"
  type        = string
  sensitive   = true
  default     = ""
}
