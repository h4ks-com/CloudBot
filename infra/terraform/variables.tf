variable "project_id" {
  description = "Google Cloud project ID hosting CloudBot keys and APIs."
  type        = string
}

variable "region" {
  description = "Default region for Google Cloud resources."
  type        = string
  default     = "us-central1"
}

variable "billing_account_id" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX). Leave empty to skip budget creation. Auto-detected by bootstrap.sh."
  type        = string
  default     = ""
}

variable "budget_amount_usd" {
  description = "Reference budget amount. Thresholds fire as a percent of this. Keep at 1 so the 1%/100%/500% thresholds map to ~0.01 / 1 / 5 of currency_code."
  type        = number
  default     = 1
}

variable "currency_code" {
  description = "Budget currency. Must match the billing account currency. Auto-detected by bootstrap.sh."
  type        = string
  default     = "USD"
}

variable "alert_emails" {
  description = "Extra email addresses to notify on budget thresholds (besides billing admins). Each address must click the verification email from cloud-monitoring-notifications-noreply@google.com before alerts deliver."
  type        = list(string)
  default     = []
}

variable "notify_billing_admins" {
  description = "When false, suppresses the default billing-admin emails and only alert_emails get notified."
  type        = bool
  default     = true
}

variable "services_key_display_name" {
  description = "Display name for the unified CloudBot Google services API key."
  type        = string
  default     = "CloudBot services (YouTube, Books, Translate, Maps, SB)"

  validation {
    condition     = length(var.services_key_display_name) <= 63
    error_message = "Google API Keys display_name must be 63 characters or fewer."
  }
}

variable "gemini_key_display_name" {
  description = "Display name for the CloudBot Gemini API key."
  type        = string
  default     = "CloudBot Gemini"

  validation {
    condition     = length(var.gemini_key_display_name) <= 63
    error_message = "Google API Keys display_name must be 63 characters or fewer."
  }
}
