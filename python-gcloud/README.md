# python-gcloud — Transfer Appliance Status Viewer

Python CLI that queries Transfer Appliance status across every project in a GCP organization. Uses the v1 discovery API with a `gcloud alpha` fallback.

## Install

You need **Python 3.10+** and the **Google Cloud SDK** on your PATH. Then:

```bash
gcloud components install alpha
pip install -r requirements.txt
gcloud auth application-default login
```

That's it — three commands.

> **Important:** during `gcloud auth application-default login`, gcloud will ask whether to set a quota project. **Say yes** and pick a project you own. Without one, API calls fail with `User project specified in the request is invalid`. To change it later: `gcloud auth application-default set-quota-project YOUR_PROJECT`.

## Grant IAM (one-time, at the org level)

```bash
ORG_ID=123456789
MEMBER="user:you@example.com"

gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/browser"
gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/transferappliance.viewer"
```

If `roles/transferappliance.viewer` isn't available in your org yet, fall back to `roles/viewer` per project.

## Run

```bash
# Default: table output, every project in the org
python -m gcp_appliance_status --org-id 123456789

# Specific projects only
python -m gcp_appliance_status --org-id 123456789 --projects proj-a proj-b

# Filter by state
python -m gcp_appliance_status --org-id 123456789 --state-filter ACTIVE SHIPPING

# JSON or CSV
python -m gcp_appliance_status --org-id 123456789 --format json
python -m gcp_appliance_status --org-id 123456789 --format csv > appliances.csv
```

Run `python -m gcp_appliance_status --help` for the full flag list.

## How it works

1. Lists active projects under the org via Cloud Resource Manager.
2. For each project, queries `transferappliance.googleapis.com` v1.
3. If the v1 call fails for a project, falls back to `gcloud alpha transfer appliances orders list`.
4. Aggregates and renders.

## Troubleshooting

**"No projects found in organization"** — confirm the org ID with `gcloud organizations list` and that your identity has `roles/browser`.

**"Permission denied"** — `gcloud organizations get-iam-policy $ORG_ID` to inspect bindings.

**"gcloud alpha not available"** — `gcloud components install alpha && gcloud components update`.

**Persistent API errors after the above** — the Transfer Appliance API is in early access. Reach out to your Google Cloud support representative.

## Other auth modes (CI / automation)

The default flow above uses your interactive `gcloud` login. For non-interactive environments:

- **Service account key:** `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` before running.
- **Workload Identity (GKE / Cloud Run / GCE):** attach a service account with the roles above; ADC picks up the metadata server automatically.
