# GCP Transfer Appliance Status Viewer

CLI tool to view the status of Google Transfer Appliances across multiple GCP projects within a single organization.

## Prerequisites

- Python 3.10+
- [Google Cloud SDK (`gcloud`)](https://cloud.google.com/sdk/docs/install) installed and on your PATH
- `gcloud` alpha components installed:
  ```bash
  gcloud components install alpha
  ```

## Installation

```bash
cd gcp_bulk_user
pip install -r requirements.txt
```

## Authentication

The tool uses [Application Default Credentials (ADC)](https://cloud.google.com/docs/authentication/application-default-credentials). Choose one of the methods below.

### Option A: User credentials (interactive / local development)

```bash
gcloud auth application-default login
```

This opens a browser for OAuth consent. The resulting credentials are stored at `~/.config/gcloud/application_default_credentials.json` and are picked up automatically.

### Option B: Service account key (CI / automation)

1. Create a service account in a central admin project:
   ```bash
   gcloud iam service-accounts create appliance-viewer \
     --display-name="Transfer Appliance Viewer" \
     --project=YOUR_ADMIN_PROJECT
   ```

2. Export a key:
   ```bash
   gcloud iam service-accounts keys create sa-key.json \
     --iam-account=appliance-viewer@YOUR_ADMIN_PROJECT.iam.gserviceaccount.com
   ```

3. Point ADC to the key:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
   ```

### Option C: Workload Identity (GKE / Cloud Run / GCE)

If running on Google Cloud infrastructure, attach a service account with the required roles to the workload. ADC will use the metadata server automatically — no key file needed.

## Required IAM Permissions

The authenticated identity (user or service account) needs these roles granted **at the organization level** so it can discover projects and read appliance status across all of them:

| Role | Purpose |
|------|---------|
| `roles/browser` | List projects in the organization via Resource Manager |
| `roles/transferappliance.viewer` | Read Transfer Appliance order status |

Grant them with:

```bash
ORG_ID=123456789
MEMBER="user:you@example.com"  # or serviceAccount:sa@project.iam.gserviceaccount.com

gcloud organizations add-iam-policy-binding $ORG_ID \
  --member="$MEMBER" \
  --role="roles/browser"

gcloud organizations add-iam-policy-binding $ORG_ID \
  --member="$MEMBER" \
  --role="roles/transferappliance.viewer"
```

If `roles/transferappliance.viewer` is not available in your environment (the API is still alpha), use the broader `roles/viewer` on each project or rely on the `gcloud alpha` fallback path, which uses your `gcloud auth login` session.

## Usage

```bash
# View all appliances across every project in the org
python -m gcp_appliance_status --org-id 123456789

# Query specific projects only (skip org-wide discovery)
python -m gcp_appliance_status --org-id 123456789 --projects proj-a proj-b proj-c

# Filter by appliance state
python -m gcp_appliance_status --org-id 123456789 --state-filter ACTIVE SHIPPING

# Output as JSON (for piping to jq, scripts, etc.)
python -m gcp_appliance_status --org-id 123456789 --format json

# Output as CSV (for spreadsheets)
python -m gcp_appliance_status --org-id 123456789 --format csv > appliances.csv

# Control parallelism (default: 10 threads)
python -m gcp_appliance_status --org-id 123456789 --workers 20
```

## Output Formats

### Table (default)

Color-coded terminal table using `rich`:

```
┌─────────────┬───────────┬──────┬──────────┬─────────────┬─────────────┐
│ Project     │ Order ID  │ Type │ State    │ Created     │ Updated     │
├─────────────┼───────────┼──────┼──────────┼─────────────┼─────────────┤
│ proj-alpha  │ order-001 │ TA40 │ SHIPPING │ 2026-01-15  │ 2026-03-20  │
│ proj-beta   │ order-042 │ TA300│ ACTIVE   │ 2026-02-01  │ 2026-03-24  │
└─────────────┴───────────┴──────┴──────────┴─────────────┴─────────────┘
```

### JSON

```bash
python -m gcp_appliance_status --org-id 123456789 --format json | jq '.[] | .state'
```

### CSV

```bash
python -m gcp_appliance_status --org-id 123456789 --format csv > report.csv
```

## How It Works

1. **Project discovery** — queries the Cloud Resource Manager API to list all active projects under the given org ID.
2. **Appliance status** — for each project, queries the `transferappliance.googleapis.com` discovery API. If that fails (API not enabled, permissions, etc.), falls back to `gcloud alpha transfer appliances orders list`.
3. **Aggregation** — results from all projects are merged and displayed in the chosen format.

## Troubleshooting

**"No projects found in organization"**
- Verify your org ID: `gcloud organizations list`
- Ensure the authenticated identity has `roles/browser` at the org level.

**No appliances returned for a project**
- The Transfer Appliance API may not be enabled. Enable it:
  ```bash
  gcloud services enable transferappliance.googleapis.com --project=PROJECT_ID
  ```
- Verify the `gcloud alpha` fallback works:
  ```bash
  gcloud alpha transfer appliances orders list --project=PROJECT_ID
  ```

**Permission denied errors**
- Check IAM bindings:
  ```bash
  gcloud organizations get-iam-policy ORG_ID --filter="bindings.members:MEMBER"
  ```

**gcloud alpha not available**
- Install alpha components: `gcloud components install alpha`
- Update gcloud: `gcloud components update`
