output "agent_runtime_arn" {
  description = "ARN to invoke the agent (deploy/invoke.py --arn)."
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
}

output "agent_runtime_id" {
  description = "AgentCore Runtime id."
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id
}

output "code_bucket" {
  description = "S3 bucket that holds the deployment package."
  value       = aws_s3_bucket.code.bucket
}
