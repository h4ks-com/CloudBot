locals {
  services_apis = [
    "youtube.googleapis.com",
    "books.googleapis.com",
    "translate.googleapis.com",
    "geocoding-backend.googleapis.com",
    "directions-backend.googleapis.com",
    "street-view-image-backend.googleapis.com",
    "safebrowsing.googleapis.com",
  ]

  gemini_apis = [
    "generativelanguage.googleapis.com",
  ]

  meta_apis = [
    "apikeys.googleapis.com",
    "serviceusage.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "monitoring.googleapis.com",
  ]

  all_apis = toset(concat(local.services_apis, local.gemini_apis, local.meta_apis))
}

resource "google_project_service" "enabled" {
  for_each = local.all_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
