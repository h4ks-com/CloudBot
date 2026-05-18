#!/usr/bin/env bash
# Bootstrap an ISOLATED gcloud + terraform environment for CloudBot.
#
# Everything (account, configurations, ADC) is written into ./.gcloud — a
# directory local to this folder. Your global ~/.config/gcloud is never
# touched, so you can switch gcloud accounts freely without affecting
# CloudBot's infra runs.
#
# Also creates the Terraform state bucket on Cloud Storage (always-free
# tier) and writes backend.hcl + terraform.tfvars.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 <project_id> [account_email] [bucket_name] [region]

Defaults:
  account_email   prompts via 'gcloud auth login' if not given
  bucket_name     <project_id>-cloudbot-tfstate
  region          us-central1 (always-free GCS region)
EOF
  exit 1
}

[[ $# -lt 1 ]] && usage

PROJECT_ID="$1"
ACCOUNT="${2:-}"
BUCKET="${3:-${PROJECT_ID}-cloudbot-tfstate}"
REGION="${4:-us-central1}"

case "$REGION" in
  us-central1|us-west1|us-east1) ;;
  *) echo "WARNING: '$REGION' is outside the GCS always-free zone." >&2 ;;
esac

cd "$(dirname "$0")"

# Isolated gcloud config dir — overrides ~/.config/gcloud for this shell only.
CLOUDSDK_CONFIG="$(pwd)/.gcloud"
export CLOUDSDK_CONFIG
mkdir -p "$CLOUDSDK_CONFIG"

echo ">> Using isolated gcloud config at: $CLOUDSDK_CONFIG"
echo ">> (your global ~/.config/gcloud is untouched)"

echo ">> Setting project: $PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null
[[ -n "$ACCOUNT" ]] && gcloud config set account "$ACCOUNT" >/dev/null

ACTIVE_ACCOUNT=$(gcloud config get-value account 2>/dev/null || true)
if [[ -z "$ACTIVE_ACCOUNT" || "$ACTIVE_ACCOUNT" == "(unset)" ]]; then
  echo ">> Logging in (this writes credentials into .gcloud/, not your global config)"
  gcloud auth login ${ACCOUNT:+"$ACCOUNT"}
fi

echo ">> Setting up Application Default Credentials inside .gcloud/"
if [[ ! -f "$CLOUDSDK_CONFIG/application_default_credentials.json" ]]; then
  gcloud auth application-default login
else
  echo "   ADC already present; reusing. To re-auth: rm -f .gcloud/application_default_credentials.json && rerun this script."
fi
gcloud auth application-default set-quota-project "$PROJECT_ID" >/dev/null

echo ">> Enabling baseline APIs (storage, serviceusage, apikeys, cloudresourcemanager)"
gcloud services enable \
  storage.googleapis.com \
  serviceusage.googleapis.com \
  apikeys.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="$PROJECT_ID" >/dev/null

if gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  echo ">> State bucket gs://${BUCKET} already exists"
else
  echo ">> Creating state bucket gs://${BUCKET} in ${REGION} (Standard, free-tier)"
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access
  gcloud storage buckets update "gs://${BUCKET}" --versioning
fi

cat > backend.hcl <<EOF
bucket = "${BUCKET}"
prefix = "cloudbot/terraform/state"
EOF

echo ">> Detecting linked billing account for $PROJECT_ID"
BILLING_ACCOUNT=""
CURRENCY="USD"
BILLING_RAW=$(gcloud beta billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)' 2>/dev/null || true)
if [[ -n "$BILLING_RAW" ]]; then
  BILLING_ACCOUNT="${BILLING_RAW#billingAccounts/}"
  DETECTED_CURRENCY=$(gcloud beta billing accounts describe "$BILLING_ACCOUNT" \
    --format='value(currencyCode)' 2>/dev/null || true)
  if [[ -n "$DETECTED_CURRENCY" ]]; then
    CURRENCY="$DETECTED_CURRENCY"
  fi
  echo "   Found: $BILLING_ACCOUNT ($CURRENCY); budget alerts will fire at 1% / 100% / 500% of 1 $CURRENCY"
else
  echo "   No billing account linked; budget creation will be skipped."
fi

cat > terraform.tfvars <<EOF
project_id         = "${PROJECT_ID}"
region             = "${REGION}"
billing_account_id = "${BILLING_ACCOUNT}"
currency_code      = "${CURRENCY}"
EOF

cat <<EOF

Done. Everything authenticated lives under ./.gcloud (gitignored).

Next:
  ./tf.sh init -backend-config=backend.hcl
  ./tf.sh apply

To check what this isolated env holds:
  CLOUDSDK_CONFIG=$(pwd)/.gcloud gcloud config list
  CLOUDSDK_CONFIG=$(pwd)/.gcloud gcloud auth list
EOF
