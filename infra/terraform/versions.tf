terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # aws_bedrockagentcore_agent_runtime exists since 6.17 and code_configuration since 6.22.
      version = "~> 6.22"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "adk-knowledge-graph"
      ManagedBy = "terraform"
    }
  }
}
