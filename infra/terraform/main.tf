data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  package_key  = "${var.agent_name}/agentcore_package.zip"
  log_group_ns = "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes"
  # The model id without the "us." / "global." inference profile prefix.
  foundation_model_id = join(".", slice(split(".", var.bedrock_model_id), 1, length(split(".", var.bedrock_model_id))))
}

# ---------------------------------------------------------------------------
# S3: deployment package (direct code deployment, no container needed)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "code" {
  bucket_prefix = "kg-agentcore-code-"
  force_destroy = true # demo bucket: `terraform destroy` also removes the uploaded package
}

resource "aws_s3_bucket_public_access_block" "code" {
  bucket                  = aws_s3_bucket.code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "code" {
  bucket = aws_s3_bucket.code.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "package" {
  bucket      = aws_s3_bucket.code.id
  key         = local.package_key
  source      = var.package_path
  source_hash = filemd5(var.package_path)

  depends_on = [aws_s3_bucket_versioning.code]
}

# ---------------------------------------------------------------------------
# IAM: execution role assumed by AgentCore Runtime (least privilege)
# Based on https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "assume_role" {
  statement {
    sid     = "AssumeRolePolicy"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    # Confused-deputy protection: only AgentCore resources of this account can assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:*"]
    }
  }
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "ReadDeploymentPackage"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.code.arn}/${local.package_key}"]
  }

  statement {
    sid     = "BedrockNovaInvocation"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      # Cross-Region inference profile and the foundation model it routes to.
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
      "arn:aws:bedrock:*::foundation-model/${local.foundation_model_id}",
    ]
  }

  statement {
    sid       = "LogGroups"
    actions   = ["logs:DescribeLogStreams", "logs:CreateLogGroup"]
    resources = ["${local.log_group_ns}/*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid       = "LogEvents"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_group_ns}/*:log-stream:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid = "WorkloadAccessToken"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default/workload-identity/${var.agent_name}-*",
    ]
  }
}

resource "aws_iam_role" "runtime" {
  name_prefix        = "kg-agentcore-runtime-"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy" "runtime" {
  name   = "kg-agentcore-runtime"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

# ---------------------------------------------------------------------------
# AgentCore Runtime
# ---------------------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "agent" {
  agent_runtime_name = var.agent_name
  description        = "ADK incremental knowledge-graph extraction (kg_pipeline_docs) on Amazon Nova"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    code_configuration {
      # "opentelemetry-instrument" runs the agent with ADOT, which exports ADK's spans to CloudWatch.
      entry_point = ["opentelemetry-instrument", "agentcore_app.py"]
      runtime     = "PYTHON_3_13"
      code {
        s3 {
          bucket     = aws_s3_bucket.code.id
          prefix     = aws_s3_object.package.key
          version_id = aws_s3_object.package.version_id # a new package version updates the runtime
        }
      }
    }
  }

  environment_variables = {
    MODEL_PROVIDER   = "bedrock"
    BEDROCK_MODEL_ID = var.bedrock_model_id
    AWS_REGION_NAME  = var.region # read by LiteLLM for Bedrock calls
    LOG_LEVEL        = "INFO"
    # Unified telemetry: spans go to the "spans" stream of the runtime log group (needs ADOT >= 0.18).
    UNIFIED_TRACES_DESTINATION_ENABLED = "true"
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = var.idle_session_timeout_seconds
    max_lifetime                 = var.max_session_lifetime_seconds
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  # The role policy must exist before the runtime tries to read the package.
  depends_on = [aws_iam_role_policy.runtime]
}

# ---------------------------------------------------------------------------
# Runtime log group retention
# AgentCore creates /aws/bedrock-agentcore/runtimes/<runtime_id>-DEFAULT together with the runtime,
# so an aws_cloudwatch_log_group resource would conflict (and the name is unknown before creation).
# Account-level retention policies need at least 30 days, so the retention is set with the AWS CLI.
# The destroy step also deletes the log group, which `terraform destroy` would otherwise leave behind.
# ---------------------------------------------------------------------------
resource "terraform_data" "runtime_log_retention" {
  triggers_replace = [aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id, var.log_retention_days]

  input = {
    region    = var.region
    log_group = "/aws/bedrock-agentcore/runtimes/${aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id}-DEFAULT"
  }

  provisioner "local-exec" {
    command = "aws logs put-retention-policy --region ${self.input.region} --log-group-name ${self.input.log_group} --retention-in-days ${var.log_retention_days}"
  }

  provisioner "local-exec" {
    when       = destroy
    command    = "aws logs delete-log-group --region ${self.input.region} --log-group-name ${self.input.log_group}"
    on_failure = continue # the log group may already be gone
  }
}
