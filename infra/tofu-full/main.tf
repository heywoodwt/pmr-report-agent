# PMR web application on Azure, the full design: App Service for the web app, Container Apps for
# the worker, Entra ID sign-in. See docs/AZURE.md for what each resource does and the steps before
# and after `tofu apply`. infra/tofu/ is the slim demo deployment.

data "azurerm_client_config" "current" {}

locals {
  tags         = { app = "pmr" }
  rg_name      = coalesce(var.resource_group_name, "rg-${var.prefix}")
  unique       = random_string.unique.result
  foundry_name = var.foundry_resource_id == null ? "" : element(split("/", var.foundry_resource_id), -1)
  api_key      = nonsensitive(var.anthropic_api_key != null)
  image        = "pmr-web:${var.image_tag}"
  database_url = "postgresql+psycopg://${var.pg_admin}:${random_password.pg.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/pmr?sslmode=require"

  common_env = {
    PMR_STORAGE               = "azure"
    AZURE_STORAGE_ACCOUNT_URL = azurerm_storage_account.docs.primary_blob_endpoint
    AZURE_STORAGE_CONTAINER   = azurerm_storage_container.pmr.name
    # DefaultAzureCredential uses this user-assigned identity.
    AZURE_CLIENT_ID                       = azurerm_user_assigned_identity.app.client_id
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.app.connection_string
    ANTHROPIC_FOUNDRY_RESOURCE            = local.foundry_name
    PMR_NO_LLM                            = local.foundry_name == "" && !local.api_key ? "1" : "0"
  }
}

# Makes globally unique names (storage, vault, registry) stable per state.
resource "random_string" "unique" {
  length  = 8
  upper   = false
  special = false
}

# Letters and digits only, because the password goes into a URL.
resource "random_password" "pg" {
  length  = 32
  special = false
}

resource "random_password" "flask" {
  length  = 64
  special = false
}

resource "azurerm_resource_group" "pmr" {
  name     = local.rg_name
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- identity
# One user-assigned identity for the web app and the worker. It reads secrets, pulls the image,
# reads and writes documents and calls the model. No storage key or model key exists anywhere.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  tags                = local.tags
}

# ---------------------------------------------------------------- monitoring
resource "azurerm_log_analytics_workspace" "logs" {
  name                = "log-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "app" {
  name                = "appi-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.logs.id
  tags                = local.tags
}

# ---------------------------------------------------------------- documents
resource "azurerm_storage_account" "docs" {
  name                            = substr("st${var.prefix}${local.unique}", 0, 24)
  resource_group_name             = azurerm_resource_group.pmr.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  account_kind                    = "StorageV2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false # Entra ID only
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  tags                            = local.tags

  blob_properties {
    # Overwritten or deleted inputs and outputs stay recoverable, which the audit trail relies on.
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
  }
}

# storage_account_id creates the container through Azure Resource Manager, so it works with shared keys off.
resource "azurerm_storage_container" "pmr" {
  name                  = "pmr"
  storage_account_id    = azurerm_storage_account.docs.id
  container_access_type = "private"
}

# ---------------------------------------------------------------- database
resource "azurerm_postgresql_flexible_server" "db" {
  name                          = "psql-${var.prefix}-${local.unique}"
  resource_group_name           = azurerm_resource_group.pmr.name
  location                      = var.location
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  backup_retention_days         = 14
  geo_redundant_backup_enabled  = false
  administrator_login           = var.pg_admin
  administrator_password        = random_password.pg.result
  public_network_access_enabled = true
  tags                          = local.tags

  lifecycle {
    # Azure picks the availability zone; do not fight it on later applies.
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "pmr" {
  name      = "pmr"
  server_id = azurerm_postgresql_flexible_server.db.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Public access limited to Azure services. A private endpoint is the next step for production.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------------------------------------------------------------- secrets
resource "azurerm_key_vault" "vault" {
  name                       = substr("kv-${var.prefix}-${local.unique}", 0, 24)
  resource_group_name        = azurerm_resource_group.pmr.name
  location                   = var.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 30
  purge_protection_enabled   = true
  tags                       = local.tags
}

# OpenTofu writes secrets through the vault's data plane, so whoever runs
# `tofu apply` needs to be able to set them.
resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.vault.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.vault.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

resource "azurerm_key_vault_secret" "flask" {
  name         = "flask-secret-key"
  value        = random_password.flask.result
  key_vault_id = azurerm_key_vault.vault.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

resource "azurerm_key_vault_secret" "auth" {
  name         = "auth-client-secret"
  value        = var.auth_client_secret
  key_vault_id = azurerm_key_vault.vault.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

# Used only when Foundry is not set: the Anthropic API instead of Claude in the Azure tenant.
resource "azurerm_key_vault_secret" "anthropic" {
  count        = local.api_key ? 1 : 0
  name         = "anthropic-api-key"
  value        = var.anthropic_api_key
  key_vault_id = azurerm_key_vault.vault.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

# ---------------------------------------------------------------- image registry
resource "azurerm_container_registry" "acr" {
  name                = substr("cr${var.prefix}${local.unique}", 0, 50)
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

# ---------------------------------------------------------------- role assignments
locals {
  app_roles = {
    pull    = { scope = azurerm_container_registry.acr.id, role = "AcrPull" }
    blobs   = { scope = azurerm_storage_account.docs.id, role = "Storage Blob Data Contributor" }
    secrets = { scope = azurerm_key_vault.vault.id, role = "Key Vault Secrets User" }
  }
}

resource "azurerm_role_assignment" "app" {
  for_each             = local.app_roles
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# Replaces the manual Foundry step in docs/AZURE.md when the resource ID is given.
resource "azurerm_role_assignment" "foundry" {
  count                = var.foundry_resource_id == null ? 0 : 1
  scope                = var.foundry_resource_id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# ---------------------------------------------------------------- web app (reviewer-facing)
resource "azurerm_service_plan" "web" {
  name                = "asp-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "P0v3"
  tags                = local.tags
}

resource "azurerm_linux_web_app" "web" {
  name                            = "app-${var.prefix}-${local.unique}"
  resource_group_name             = azurerm_resource_group.pmr.name
  location                        = var.location
  service_plan_id                 = azurerm_service_plan.web.id
  https_only                      = true
  key_vault_reference_identity_id = azurerm_user_assigned_identity.app.id
  tags                            = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  site_config {
    always_on                                     = true
    ftps_state                                    = "Disabled"
    minimum_tls_version                           = "1.2"
    health_check_path                             = "/health"
    health_check_eviction_time_in_min             = 5
    container_registry_use_managed_identity       = true
    container_registry_managed_identity_client_id = azurerm_user_assigned_identity.app.client_id

    application_stack {
      docker_registry_url = "https://${azurerm_container_registry.acr.login_server}"
      docker_image_name   = local.image
    }
  }

  app_settings = merge(local.common_env, {
    WEBSITES_PORT = "8000"
    # The worker is its own Container App; the web app only queues runs.
    PMR_EMBEDDED_WORKER                      = "0"
    PMR_TRUST_EASY_AUTH                      = "1"
    DATABASE_URL                             = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.database_url.versionless_id})"
    FLASK_SECRET_KEY                         = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.flask.versionless_id})"
    MICROSOFT_PROVIDER_AUTHENTICATION_SECRET = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.auth.versionless_id})"
    ANTHROPIC_API_KEY                        = local.api_key ? "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.anthropic[0].versionless_id})" : ""
  })

  # Every request is signed in with Microsoft Entra ID before it reaches the app. The app reads
  # the user's name from the X-MS-CLIENT-PRINCIPAL-NAME header and records it on each action.
  auth_settings_v2 {
    auth_enabled           = true
    require_authentication = true
    unauthenticated_action = "RedirectToLoginPage"
    default_provider       = "azureactivedirectory"

    active_directory_v2 {
      client_id                  = var.auth_client_id
      client_secret_setting_name = "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET"
      tenant_auth_endpoint       = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
    }

    login {
      token_store_enabled = true
    }
  }

  depends_on = [azurerm_role_assignment.app]
}

# ---------------------------------------------------------------- worker (background runs)
resource "azurerm_container_app_environment" "env" {
  name                       = "cae-${var.prefix}"
  resource_group_name        = azurerm_resource_group.pmr.name
  location                   = var.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
  tags                       = local.tags
}

resource "azurerm_container_app" "worker" {
  name                         = "ca-${var.prefix}-worker"
  resource_group_name          = azurerm_resource_group.pmr.name
  container_app_environment_id = azurerm_container_app_environment.env.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  # No ingress block: the worker takes work from the database, it does not serve requests.
  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_url.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.api_key ? [1] : []
    content {
      name                = "anthropic-api-key"
      key_vault_secret_id = azurerm_key_vault_secret.anthropic[0].versionless_id
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  template {
    # Runs are claimed with a conditional update, so more replicas are safe if volume grows.
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "worker"
      image   = "${azurerm_container_registry.acr.login_server}/${local.image}"
      command = ["python", "-m", "pmr_web.worker"]
      cpu     = 1.0
      memory  = "2Gi"

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      dynamic "env" {
        for_each = local.api_key ? [1] : []
        content {
          name        = "ANTHROPIC_API_KEY"
          secret_name = "anthropic-api-key"
        }
      }
    }
  }

  depends_on = [azurerm_role_assignment.app]
}
