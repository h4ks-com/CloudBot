data "google_project" "this" {
  count      = var.billing_account_id == "" ? 0 : 1
  project_id = var.project_id
}

resource "google_monitoring_notification_channel" "budget_alerts" {
  for_each = var.billing_account_id == "" ? toset([]) : toset(var.alert_emails)

  project      = var.project_id
  display_name = "CloudBot budget alert (${each.value})"
  type         = "email"

  labels = {
    email_address = each.value
  }

  depends_on = [google_project_service.enabled]
}

resource "google_billing_budget" "cloudbot" {
  count = var.billing_account_id == "" ? 0 : 1

  billing_account = var.billing_account_id
  display_name    = "CloudBot zero-cost canary"

  budget_filter {
    projects = ["projects/${data.google_project.this[0].number}"]
  }

  amount {
    specified_amount {
      currency_code = var.currency_code
      units         = tostring(var.budget_amount_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.01
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 5.0
  }

  all_updates_rule {
    monitoring_notification_channels = [
      for c in google_monitoring_notification_channel.budget_alerts : c.id
    ]
    disable_default_iam_recipients = !var.notify_billing_admins
  }

  depends_on = [google_project_service.enabled]
}
