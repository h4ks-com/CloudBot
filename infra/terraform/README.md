# CloudBot Google Cloud infrastructure

Terraform stack that enables every Google API CloudBot calls and provisions
two scoped API keys, plus tooling to keep state and gcloud auth isolated
from any other gcloud configurations you have on this machine.

## Layout

| File | Purpose |
|------|---------|
| `versions.tf` | Provider + version pins |
| `variables.tf` | Inputs (`project_id`, `region`, `billing_account_id`, key display names) |
| `apis.tf` | `google_project_service` per API |
| `keys.tf` | Two `google_apikeys_key` resources (services + Gemini) |
| `budget.tf` | Optional `google_billing_budget` ($0.01 / $1 / $5 email alerts) |
| `backend.tf` | Empty `gcs` backend, configured via `backend.hcl` |
| `outputs.tf` | Sensitive key strings + enabled service list + budget flag |
| `bootstrap.sh` | Creates dedicated `cloudbot` gcloud config, state bucket, `backend.hcl`, `terraform.tfvars` |
| `tf.sh` | Wrapper that runs `terraform` with `CLOUDSDK_ACTIVE_CONFIG_NAME=cloudbot` |
| `terraform.tfvars.example`, `backend.hcl.example` | Templates |

## Quickstart

```bash
cd infra/terraform
./bootstrap.sh my-gcp-project user@example.com   # both args optional after project
./tf.sh init -backend-config=backend.hcl
./tf.sh apply

./tf.sh output -raw google_api_key   # paste into config.json api_keys.google
./tf.sh output -raw gemini_api_key   # paste into config.json api_keys.gemini
```

## Fully isolated gcloud (no global pollution)

Both scripts set `CLOUDSDK_CONFIG=./.gcloud` before running anything. That
env var tells the `gcloud` CLI to use `./.gcloud` as its *entire* config
directory — accounts, configurations, ADC, the lot — instead of
`~/.config/gcloud`. Your global gcloud state is never read or written by
this stack.

Effect:

- `bootstrap.sh` runs `gcloud auth login` and `gcloud auth application-default
  login` inside `./.gcloud/`. You log in once for the CloudBot account; the
  tokens live in this folder only.
- `tf.sh` exports `CLOUDSDK_CONFIG=./.gcloud` and points
  `GOOGLE_APPLICATION_CREDENTIALS` at the ADC file inside that folder, then
  runs `terraform`. The Google provider reads only those credentials.
- Your shell's `gcloud config` still shows whatever you had before; switching
  accounts globally affects nothing here.

Inspect the isolated env:

```bash
./gcloud.sh auth list
./gcloud.sh config list
./gcloud.sh services list --enabled
./gcloud.sh beta billing budgets list --billing-account=<id>
```

`gcloud.sh` is a thin wrapper that does `CLOUDSDK_CONFIG=./.gcloud exec
gcloud "$@"`, so any gcloud invocation against this stack is one prefix
away without touching your global state.

`./.gcloud/` is gitignored. To start clean: `rm -rf .gcloud && ./bootstrap.sh ...`.

## State storage

State lives in a Cloud Storage bucket created by `bootstrap.sh`:

- Name: `<project_id>-cloudbot-tfstate` (override as 3rd arg)
- Location: `us-central1` (always-free region; the script warns if you pick another)
- Class: Standard, uniform bucket-level access
- Versioning: on (cheap recovery for state corruption)

The bucket name is written into `backend.hcl` (gitignored). Re-running
`bootstrap.sh` is idempotent.

**Treat the state bucket like a secret.** Terraform stores `key_string`
for both API keys in cleartext inside the state file. The bucket is
created with uniform bucket-level access and only the creating account
has IAM by default — keep it that way. Don't grant `storage.objectViewer`
broadly, don't make the bucket public, and consider enabling CMEK if you
share the project with others.

## APIs and keys

**Services key → `api_keys.google`:**

| Service | Used by |
|---------|---------|
| `youtube.googleapis.com` | `youtube.py` |
| `books.googleapis.com` | `books.py` |
| `translate.googleapis.com` | `google_translate.py` |
| `geocoding-backend.googleapis.com` | `locate.py`, `gmaps.py` |
| `directions-backend.googleapis.com` | `gmaps.py` |
| `street-view-image-backend.googleapis.com` | `gmaps.py` |
| `safebrowsing.googleapis.com` | `issafe.py` (v4) |

**Gemini key → `api_keys.gemini`:**

| Service | Used by |
|---------|---------|
| `generativelanguage.googleapis.com` | `gemini.py` |

Old `api_keys.google_dev_key` and `api_keys.google_cse_id` are gone; every
plugin reads `api_keys.google`. `google_cse_id` was dead code (`google_cse.py`
actually proxies SearXNG).

## Free-tier reality (May 2026)

Everything in this stack fits within Google's perpetual free quotas for a
hobby IRC bot, with one caveat:

| API | Free quota | Verdict |
|---|---|---|
| YouTube Data v3 | 10,000 units/day | always free |
| Books API | ~1,000 req/day default | always free |
| Cloud Translation v2/v3 | **500K chars/month** (never expires) | always free |
| Maps Essentials (Geocoding, Directions, Street View Static, Maps Static) | **10K calls/SKU/month** since Mar 2025 | always free |
| Safe Browsing v4 | unlimited non-commercial | free |
| Gemini Flash text models | 10 RPM / 250 RPD free tier | free for low volume |
| Gemini image (`gemini-2.5-flash-image`, used by `gemini.py`) | ~15 RPM / ~500 RPD free tier on Free-tier projects; paid pricing (~$0.039/image) only past quota or on paid tier | free for low volume |
| Cloud Storage Standard, us-central1/west1/east1 | 5 GB-mo + 5K Class A + 50K Class B | always free |
| API Keys / Service Usage management | — | free |

If you want strictly $0:

- Keep the project on the Free tier (don't let billing auto-upgrade it).
  Image generation stays free up to ~500 RPD; over that the API returns
  quota errors instead of charging — unless the project is on a paid tier.
- Stay under 10K monthly calls on each Maps SKU and 500K monthly chars on
  Translate (trivial for an IRC channel).
- Keep the state bucket in `us-central1`/`us-west1`/`us-east1` Standard
  class — `bootstrap.sh` defaults there.
- Don't enable billing-required APIs you don't use. The stack only enables
  what CloudBot actually calls.

## Credit-card requirement

Per-API reality:

| API | Billing/CC required to enable? |
|---|---|
| Gemini (`generativelanguage.googleapis.com`) | **No.** Free tier works on a billing-less project. Adding a CC promotes you to Tier 1 (more quota). |
| YouTube Data v3 | No. |
| Books API | No. |
| Safe Browsing v4 (non-commercial) | No. |
| Maps Platform (Geocoding, Directions, Street View, Maps Static) | **Yes, billing account must be linked.** $0 charged while under 10K calls/SKU/month. |
| Cloud Translation v2/v3 | **Yes.** $0 under 500K chars/month. |

Two ways to handle this if you want zero-CC:

1. **Mixed setup**: link a billing account but rely on free quotas + set a
   $0.01 budget alert. Free quotas hold; you get a warning long before any
   real charge.
2. **CC-free subset**: keep `bootstrap.sh` running with a billing-less
   project and remove Maps + Translate from `apis.tf`'s `services_apis`
   list. The plugins (`gmaps.py`, `google_translate.py`, `locate.py`)
   will respond with their existing "no key" or API-error messages.

The plugin layer enforces its own per-day Gemini cap (450 RPD in
`plugins/gemini.py`, comfortably under the ~500 free-tier ceiling) so
heavy channel use can't accidentally push you off the free tier.

As of Feb 2026, default GCS buckets need the Blaze plan; always-free
quotas still apply on top of that.

## Notifications & runaway protection

Two layers, both non-intrusive:

1. **In-bot daily caps** (RAM-only, reset on bot restart):
   - `plugins/gemini.py`: 450 img/day (under ~500 free)
   - `plugins/google_translate.py`: 16,000 chars/day (under 500K/mo)
   - `plugins/books.py`: 800 req/day
   - `plugins/gmaps.py`: 30 req/hour + 300 req/day across all Maps SKUs
   - Past the cap the plugin returns an error string and **does not call Google**, so no charge or quota burn is possible.

2. **Cloud billing budget** (created by `budget.tf` when bootstrap detects a linked billing account):
   - Reference budget of `1` unit in the billing-account currency (auto-detected)
   - Email alerts at 1% / 100% / 500% of current spend (~0.01 / 1 / 5)
   - Default recipients: Billing Account Administrators on the billing account
   - Extra recipients: set `alert_emails` in `terraform.tfvars` (each must click the verification email from `cloud-monitoring-notifications-noreply@google.com` once). Set `notify_billing_admins = false` to suppress the default admin emails.
   - **Notifies only; does not auto-disable.** Disabling billing kills every API including Maps/Translate, which is more disruptive than the overage itself for hobby volume.

If you want hard-stop on overage, replace the budget's email path with a
Pub/Sub topic + Cloud Function that calls `projects.disableBilling`.
Heavy hammer; not enabled by default.

## Sources

- Google Maps Platform free tier (10K/SKU/month, Essentials): <https://mapsplatform.google.com/resources/blog/start-building-today-with-up-to-10-000-monthly-free-calls-per-product/>
- Cloud Translation pricing (500K chars/month, no expiry): <https://cloud.google.com/translate/pricing>
- Gemini API pricing and free tier: <https://ai.google.dev/gemini-api/docs/pricing>
- Gemini API rate limits: <https://ai.google.dev/gemini-api/docs/rate-limits>
- GCS Always Free (5 GB Standard in US regions): <https://cloud.google.com/storage/pricing>
- GCP Free Tier overview: <https://cloud.google.com/free>
