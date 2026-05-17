resource "google_apikeys_key" "services" {
  name         = "cloudbot-services"
  display_name = var.services_key_display_name
  project      = var.project_id

  restrictions {
    dynamic "api_targets" {
      for_each = local.services_apis
      content {
        service = api_targets.value
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_apikeys_key" "gemini" {
  name         = "cloudbot-gemini"
  display_name = var.gemini_key_display_name
  project      = var.project_id

  restrictions {
    dynamic "api_targets" {
      for_each = local.gemini_apis
      content {
        service = api_targets.value
      }
    }
  }

  depends_on = [google_project_service.enabled]
}
