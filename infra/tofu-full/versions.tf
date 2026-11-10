terraform {
  required_version = ">= 1.8.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # State holds the generated database password and session key, so OpenTofu encrypts it
  # at rest. Losing the passphrase means losing the state; keep it in a password manager.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }
    state {
      method   = method.aes_gcm.state
      enforced = true
    }
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }

  # Remote state in Azure Storage with Entra ID auth. Fill in backend.hcl from
  # backend.hcl.example and run `tofu init -backend-config=backend.hcl`.
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id = var.subscription_id
  # The storage account disables shared keys, so the provider must use Entra ID for blob calls.
  storage_use_azuread = true

  features {
    key_vault {
      # Purge protection is on; never try to purge a deleted vault or secret on destroy.
      purge_soft_delete_on_destroy          = false
      purge_soft_deleted_secrets_on_destroy = false
      recover_soft_deleted_key_vaults       = true
      recover_soft_deleted_secrets          = true
    }
  }
}
