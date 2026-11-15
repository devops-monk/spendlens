mod "spendlens" {
  title       = "spendlens"
  description = "FinOps controls for AWS used by the spendlens CLI: tag compliance, rightsizing and waste."
  color       = "#E6007E"
  categories  = ["aws", "cost", "finops", "tags"]
  database    = var.database

  require {
    plugin "aws" {
      min_version = "1.0.0"
    }
  }
}
