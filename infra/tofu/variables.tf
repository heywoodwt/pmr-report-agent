variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "prefix" {
  description = "Short lowercase prefix for resource names, 3 to 11 letters or digits."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,11}$", var.prefix))
    error_message = "The prefix must be 3 to 11 lowercase letters or digits."
  }
}

variable "location" {
  description = "Azure region for every resource."
  type        = string
  default     = "westus2"
}

variable "resource_group_name" {
  description = "Resource group to create. Defaults to rg-<prefix>."
  type        = string
  default     = null
}

variable "image_tag" {
  description = "Image tag the web app starts with. The deploy workflow replaces it with each commit hash."
  type        = string
  default     = "latest"
}

variable "anthropic_api_key" {
  description = "Anthropic API key for live model calls, stored in Key Vault. Set it with TF_VAR_anthropic_api_key. Null: cache-only."
  type        = string
  default     = null
  sensitive   = true
}

variable "github_repo" {
  description = <<-EOT
    The repository part of the GitHub OIDC subject. This repository's tokens use GitHub's immutable
    format, owner@owner_id/name@repo_id; older repositories use plain owner/name. The subject
    GitHub sent is printed by azure/login when sign-in fails.
  EOT
  type        = string
  default     = "heywoodwt@112906201/pmr-report-agent@1452906707"
}

variable "github_branch" {
  description = "Branch whose workflow runs may deploy."
  type        = string
  default     = "main"
}

variable "pg_admin" {
  description = "PostgreSQL administrator login."
  type        = string
  default     = "pmradmin"
}

variable "state_passphrase" {
  description = "Passphrase that encrypts the state and plan files, at least 16 characters. Set it with TF_VAR_state_passphrase."
  type        = string
  sensitive   = true
}
