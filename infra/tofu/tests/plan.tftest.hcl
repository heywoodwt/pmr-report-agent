# Plans the configuration against mocked providers, so it runs offline with no Azure account.
# The mocks return well-formed Azure IDs because the provider validates them even in a plan.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "11111111-1111-1111-1111-111111111111"
      object_id = "22222222-2222-2222-2222-222222222222"
    }
  }

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-pmrdemo"
      client_id    = "44444444-4444-4444-4444-444444444444"
      principal_id = "55555555-5555-5555-5555-555555555555"
    }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.OperationalInsights/workspaces/log-pmrdemo"
    }
  }

  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.Storage/storageAccounts/stpmrdemo"
    }
  }

  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-pmrdemo"
    }
  }

  mock_resource "azurerm_key_vault" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.KeyVault/vaults/kv-pmrdemo"
    }
  }

  mock_resource "azurerm_container_registry" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.ContainerRegistry/registries/crpmrdemo"
    }
  }

  mock_resource "azurerm_service_plan" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.Web/serverFarms/asp-pmrdemo"
    }
  }

  mock_resource "azurerm_linux_web_app" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.Web/sites/app-pmrdemo"
    }
  }

  mock_resource "azurerm_key_vault_secret" {
    defaults = {
      versionless_id = "https://kv-pmrdemo.vault.azure.net/secrets/name"
    }
  }
}

mock_provider "random" {}

variables {
  subscription_id  = "00000000-0000-0000-0000-000000000000"
  prefix           = "pmrdemo"
  state_passphrase = "test-passphrase-123456"
}

run "cache_only_without_key" {
  command = plan

  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_NO_LLM"] == "1"
    error_message = "Without an Anthropic key the app must run cache-only."
  }
  assert {
    condition     = length(azurerm_key_vault_secret.anthropic) == 0
    error_message = "No key secret without a key."
  }
  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_EMBEDDED_WORKER"] == "1"
    error_message = "The slim deployment runs the worker inside the web app."
  }
  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_TRUST_EASY_AUTH"] == "0"
    error_message = "Without App Service authentication the identity headers must not be trusted."
  }
  assert {
    condition     = startswith(azurerm_linux_web_app.web.app_settings["PMR_ACCESS_CODE"], "@Microsoft.KeyVault(")
    error_message = "The access code must come from Key Vault."
  }
  assert {
    condition     = azurerm_storage_account.docs.shared_access_key_enabled == false
    error_message = "Storage must accept Entra ID only."
  }
  assert {
    condition     = azurerm_federated_identity_credential.github.subject == "repo:heywoodwt@112906201/pmr-report-agent@1452906707:ref:refs/heads/main"
    error_message = "Only the main branch of this repository may deploy."
  }
  assert {
    condition     = length(azurerm_storage_account.docs.name) <= 24 && length(azurerm_key_vault.vault.name) <= 24
    error_message = "Storage account and vault names must fit Azure's 24-character limit."
  }
}

run "live_with_key" {
  command = plan

  variables {
    anthropic_api_key = "sk-ant-test"
  }

  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_NO_LLM"] == "0"
    error_message = "Live model calls must be on when a key is set."
  }
  assert {
    condition     = startswith(azurerm_linux_web_app.web.app_settings["ANTHROPIC_API_KEY"], "@Microsoft.KeyVault(")
    error_message = "The key must reach the app as a Key Vault reference, never as plain text."
  }
}

run "rejects_bad_prefix" {
  command = plan

  variables {
    prefix = "PMR-Demo"
  }

  expect_failures = [var.prefix]
}
