#!/usr/bin/env bash
# Run terraform with an ISOLATED gcloud config (./.gcloud) so it always
# uses the CloudBot account/project regardless of your global gcloud state.
# Your shell's gcloud, your other projects, your global ADC — none of that
# is touched or read by this run.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d ".gcloud" ]]; then
  echo "./.gcloud not found. Run ./bootstrap.sh <project_id> first." >&2
  exit 1
fi

export CLOUDSDK_CONFIG="$(pwd)/.gcloud"
export GOOGLE_APPLICATION_CREDENTIALS="$CLOUDSDK_CONFIG/application_default_credentials.json"

if [[ ! -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
  echo "ADC missing at $GOOGLE_APPLICATION_CREDENTIALS. Run ./bootstrap.sh again." >&2
  exit 1
fi

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
  echo "No project set in isolated config. Re-run ./bootstrap.sh." >&2
  exit 1
fi
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_PROJECT="$PROJECT_ID"

exec terraform "$@"
