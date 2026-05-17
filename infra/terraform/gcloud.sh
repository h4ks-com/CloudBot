#!/usr/bin/env bash
# Run gcloud against the isolated CloudBot config dir (./.gcloud), so commands
# operate on the CloudBot account/project without touching your global gcloud.
#
#   ./gcloud.sh config list
#   ./gcloud.sh services list --enabled
#   ./gcloud.sh beta billing budgets list --billing-account=<id>

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d ".gcloud" ]]; then
  echo "./.gcloud not found. Run ./bootstrap.sh <project_id> first." >&2
  exit 1
fi

export CLOUDSDK_CONFIG="$(pwd)/.gcloud"
exec gcloud "$@"
