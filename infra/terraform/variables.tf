variable "region" {
  description = "AWS Region for every resource. Amazon Nova must be available here."
  type        = string
  default     = "us-east-1"
}

variable "agent_name" {
  description = "AgentCore Runtime name (letters, numbers and underscores)."
  type        = string
  default     = "kg_pipeline_docs"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.agent_name))
    error_message = "agent_name must start with a letter and contain only letters, numbers and underscores (max 48)."
  }
}

variable "package_path" {
  description = "Zip built by deploy/build_package.py, relative to this folder."
  type        = string
  default     = "../../build/agentcore_package.zip"
}

variable "bedrock_model_id" {
  description = "Bedrock inference profile used by the agents."
  type        = string
  default     = "us.amazon.nova-2-lite-v1:0"
}

variable "idle_session_timeout_seconds" {
  description = "How long an idle session (and its in-memory graph) stays alive."
  type        = number
  default     = 900
}

variable "log_retention_days" {
  description = "Retention of the runtime log group (logs and spans)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs retention value (1, 3, 5, 7, 14, 30, ...)."
  }
}

variable "max_session_lifetime_seconds" {
  description = "Maximum lifetime of a session."
  type        = number
  default     = 3600
}
