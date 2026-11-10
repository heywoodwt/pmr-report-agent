# PMR web application on Azure, slim demo deployment: one App Service runs the web app and the
# worker thread. infra/tofu-full/ is the two-app design described in docs/AZURE.md.

data "azurerm_client_config" "current" {}

locals {
  tags         = { app = "pmr" }
  rg_name      = coalesce(var.resource_group_name, "rg-${var.prefix}")
  unique       = random_string.unique.result
  image        = "pmr-web:${var.image_tag}"
  live_model   = nonsensitive(var.anthropic_api_key != null)
  database_url = "postgresql+psycopg://${var.pg_admin}:${random_password.pg.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/pmr?sslmode=require"
}

# Makes globally unique names (storage, vault, registry, web app) stable per state.
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

# The shared code demo visitors type with their name. Lowercase and digits, easy to send by email.
resource "random_password" "access_code" {
  length  = 12
  upper   = false
  special = false
}

resource "azurerm_resource_group" "pmr" {
  name     = local.rg_name
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- identities
# The web app's identity: reads secrets, pulls the image, reads and writes documents.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  tags                = local.tags
}

# GitHub Actions signs in as this identity through OpenID Connect, so no Azure secret is stored in GitHub.
resource "azurerm_user_assigned_identity" "deploy" {
  name                = "id-${var.prefix}-deploy"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "github" {
  name                      = "github-${var.github_branch}"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:${var.github_repo}:ref:refs/heads/${var.github_branch}"
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
  account_replication_type        = "LRS"
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
  backup_retention_days         = 7
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

# Public access limited to Azure services.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------------------------------------------------------------- secrets
# Purge protection is off so the demo can be torn down and rebuilt without waiting out retention.
resource "azurerm_key_vault" "vault" {
  name                       = substr("kv-${var.prefix}-${local.unique}", 0, 24)
  resource_group_name        = azurerm_resource_group.pmr.name
  location                   = var.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
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

resource "azurerm_key_vault_secret" "access_code" {
  name         = "access-code"
  value        = random_password.access_code.result
  key_vault_id = azurerm_key_vault.vault.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}

resource "azurerm_key_vault_secret" "anthropic" {
  count        = local.live_model ? 1 : 0
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

# The deploy workflow pushes an image and points the web app at it; nothing more.
locals {
  deploy_roles = {
    push = { scope = azurerm_container_registry.acr.id, role = "AcrPush" }
    web  = { scope = azurerm_linux_web_app.web.id, role = "Website Contributor" }
  }
}

resource "azurerm_role_assignment" "deploy" {
  for_each             = local.deploy_roles
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
  principal_type       = "ServicePrincipal"
}

# ---------------------------------------------------------------- web app and worker
resource "azurerm_service_plan" "web" {
  name                = "asp-${var.prefix}"
  resource_group_name = azurerm_resource_group.pmr.name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "B1"
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
    # The worker thread lives in the web process, so the process must not be unloaded when idle.
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

  app_settings = {
    WEBSITES_PORT                       = "8000"
    WEBSITES_CONTAINER_START_TIME_LIMIT = "600"
    PMR_STORAGE                         = "azure"
    AZURE_STORAGE_ACCOUNT_URL           = azurerm_storage_account.docs.primary_blob_endpoint
    AZURE_STORAGE_CONTAINER             = azurerm_storage_container.pmr.name
    # DefaultAzureCredential uses this user-assigned identity.
    AZURE_CLIENT_ID                       = azurerm_user_assigned_identity.app.client_id
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.app.connection_string
    # One instance runs the queue in a thread; runs are still claimed through the database.
    PMR_EMBEDDED_WORKER = "1"
    # No App Service authentication: the name sign-in plus the shared access code admits visitors.
    PMR_TRUST_EASY_AUTH = "0"
    PMR_ACCESS_CODE     = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.access_code.versionless_id})"
    DATABASE_URL        = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.database_url.versionless_id})"
    FLASK_SECRET_KEY    = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.flask.versionless_id})"
    ANTHROPIC_API_KEY   = local.live_model ? "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.anthropic[0].versionless_id})" : ""
    PMR_NO_LLM          = local.live_model ? "0" : "1"
  }

  lifecycle {
    # The deploy workflow sets the image tag to each commit; later applies must not roll it back.
    ignore_changes = [site_config[0].application_stack[0].docker_image_name]
  }

  depends_on = [azurerm_role_assignment.app]
}
