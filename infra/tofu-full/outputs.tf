output "web_url" {
  value = "https://${azurerm_linux_web_app.web.default_hostname}"
}

output "auth_redirect_uri" {
  description = "Add this to the Entra app registration's web redirect URIs."
  value       = "https://${azurerm_linux_web_app.web.default_hostname}/.auth/login/aad/callback"
}

output "resource_group" {
  value = azurerm_resource_group.pmr.name
}

output "registry" {
  value = azurerm_container_registry.acr.name
}

output "web_app" {
  value = azurerm_linux_web_app.web.name
}

output "identity_principal_id" {
  value = azurerm_user_assigned_identity.app.principal_id
}

output "storage_account" {
  value = azurerm_storage_account.docs.name
}
