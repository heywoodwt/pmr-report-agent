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

  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pmrdemo/providers/Microsoft.App/managedEnvironments/cae-pmrdemo"
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
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  prefix             = "pmrdemo"
  auth_client_id     = "33333333-3333-3333-3333-333333333333"
  auth_client_secret = "test-secret"
  state_passphrase   = "test-passphrase-123456"
}

run "cache_only_without_foundry" {
  command = plan

  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_NO_LLM"] == "1"
    error_message = "Without a Foundry resource the apps must run cache-only."
  }
  assert {
    condition     = length(azurerm_role_assignment.foundry) == 0
    error_message = "No Foundry role assignment without a Foundry resource."
  }
  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_EMBEDDED_WORKER"] == "0"
    error_message = "The web app must not run the worker in Azure."
  }
  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_TRUST_EASY_AUTH"] == "1"
    error_message = "With App Service authentication on, the app reads the Entra ID user from its headers."
  }
  assert {
    condition     = azurerm_storage_account.docs.shared_access_key_enabled == false
    error_message = "Storage must accept Entra ID only."
  }
  assert {
    condition     = length(azurerm_storage_account.docs.name) <= 24 && length(azurerm_key_vault.vault.name) <= 24
    error_message = "Storage account and vault names must fit Azure's 24-character limit."
  }
}

run "live_with_foundry" {
  command = plan

  variables {
    foundry_resource_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-ai/providers/Microsoft.CognitiveServices/accounts/ai-pmr"
  }

  assert {
    condition     = azurerm_linux_web_app.web.app_settings["ANTHROPIC_FOUNDRY_RESOURCE"] == "ai-pmr"
    error_message = "The Foundry resource name must come from the end of its resource ID."
  }
  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_NO_LLM"] == "0"
    error_message = "Live model calls must be on when Foundry is set."
  }
  assert {
    condition     = length(azurerm_role_assignment.foundry) == 1
    error_message = "The identity needs Cognitive Services User on the Foundry resource."
  }
}

run "live_with_api_key" {
  command = plan

  variables {
    anthropic_api_key = "sk-ant-test"
  }

  assert {
    condition     = azurerm_linux_web_app.web.app_settings["PMR_NO_LLM"] == "0"
    error_message = "Live model calls must be on when an API key is set."
  }
  assert {
    condition     = startswith(azurerm_linux_web_app.web.app_settings["ANTHROPIC_API_KEY"], "@Microsoft.KeyVault(")
    error_message = "The key must reach the app as a Key Vault reference."
  }
}

run "rejects_bad_prefix" {
  command = plan

  variables {
    prefix = "PMR-Demo"
  }

  expect_failures = [var.prefix]
}
