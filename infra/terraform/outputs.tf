output "google_api_key" {
  description = "Value for api_keys.google in config.json"
  value       = google_apikeys_key.services.key_string
  sensitive   = true
}

output "gemini_api_key" {
  description = "Value for api_keys.gemini in config.json"
  value       = google_apikeys_key.gemini.key_string
  sensitive   = true
}

output "enabled_services" {
  description = "Google Cloud APIs enabled by this stack."
  value       = sort([for s in google_project_service.enabled : s.service])
}

output "budget_configured" {
  description = "Whether a billing budget with email alerts was created."
  value       = length(google_billing_budget.cloudbot) > 0
}
