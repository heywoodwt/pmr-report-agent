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
  description = "Image tag pushed to the registry, for example a git commit hash."
  type        = string
  default     = "latest"
}

variable "foundry_resource_id" {
  description = "Resource ID of the Azure AI Foundry resource that hosts the Claude deployment. Null: cache-only, no live model calls."
  type        = string
  default     = null
}

variable "anthropic_api_key" {
  description = "Anthropic API key, used when foundry_resource_id is null. Set it with TF_VAR_anthropic_api_key. Both null: cache-only."
  type        = string
  default     = null
  sensitive   = true
}

variable "auth_client_id" {
  description = "Client ID of the Entra ID app registration used for sign-in."
  type        = string
}

variable "auth_client_secret" {
  description = "Client secret of that app registration. Set it with TF_VAR_auth_client_secret, not in a file."
  type        = string
  sensitive   = true
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
