output "web_url" {
  value = "https://${azurerm_linux_web_app.web.default_hostname}"
}

output "access_code" {
  description = "Send this to demo visitors. Read it with `tofu output -raw access_code`."
  value       = random_password.access_code.result
  sensitive   = true
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

# The deploy workflow reads these three as GitHub repository variables.
output "deploy_client_id" {
  value = azurerm_user_assigned_identity.deploy.client_id
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  value = var.subscription_id
}
