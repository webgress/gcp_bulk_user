# python-gcloud — Transfer Appliance Status Viewer

Python CLI that queries Transfer Appliance status across every project in a GCP organization. Uses the v1 discovery API with a `gcloud alpha` fallback.

## Install (macOS / Linux)

One command. The script downloads the source, installs the Google Cloud SDK if missing, creates a virtualenv, installs Python deps, and runs `gcloud auth application-default login`. Re-run it any time to refresh.

```bash
curl -fsSL https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.sh | bash
```

Prefer to read it before running? Same script, two-step:

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.sh
less install.sh        # inspect
bash install.sh
```

**Only prerequisite:** Python 3.10 or newer (`python3 --version`). Install from <https://www.python.org/downloads/> if missing. The script handles everything else.

When the script reaches the auth step, gcloud asks whether to set a quota project — **say yes** and pick a project you own. Without one, API calls fail with `User project specified in the request is invalid`. To change later: `gcloud auth application-default set-quota-project YOUR_PROJECT`.

After it finishes, the script prints the exact next command to run.

## Install (Windows, PowerShell 5.1+)

```powershell
iwr -useb https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.ps1 | iex
```

To inspect first:

```powershell
iwr -useb -OutFile install.ps1 https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.ps1
notepad install.ps1
.\install.ps1
```

If execution policy blocks the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**Only prerequisite:** Python 3.10 or newer (`python --version`). Install from <https://www.python.org/downloads/> and tick "Add Python to PATH" during setup. The script handles everything else (gcloud SDK, virtualenv, deps, auth).

## Grant IAM (one-time, at the org level)

```bash
ORG_ID=123456789
MEMBER="user:you@example.com"

gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/browser"
gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/transferappliance.viewer"
```

If `roles/transferappliance.viewer` isn't available in your org yet, fall back to `roles/viewer` per project.

## Run

Run from the `python-gcloud` directory with your virtualenv active.

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

**`ModuleNotFoundError: No module named 'gcp_appliance_status'`** — you're not inside the `python-gcloud` directory, or the virtualenv isn't active. Re-run `source .venv/bin/activate` (or `.\.venv\Scripts\Activate.ps1` on Windows) and try again.

**`pip` or `python` not found** — Python isn't on your PATH. Reinstall from <https://www.python.org/downloads/> and tick "Add Python to PATH" during setup.

## Updating to a newer version

Re-run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.sh | bash
```

It re-downloads the source and reinstalls Python dependencies. Existing gcloud install and credentials are reused.

## Manual install (fallback)

If you can't or don't want to run the script — Windows users, or anyone who prefers explicit steps — do the following.

### macOS / Linux

```bash
# 1. Install gcloud SDK if missing: https://cloud.google.com/sdk/docs/install
gcloud components install alpha

# 2. Download source (no GitHub login)
curl -L -o gcp_bulk_user.tar.gz \
  https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.tar.gz
tar -xzf gcp_bulk_user.tar.gz
cd gcp_bulk_user-main/python-gcloud

# 3. Virtualenv + deps
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Auth (say YES to quota project, pick one you own)
gcloud auth application-default login
```

### Windows (PowerShell)

```powershell
# 1. Install gcloud SDK if missing: https://cloud.google.com/sdk/docs/install
gcloud components install alpha

# 2. Download source zip
Invoke-WebRequest `
  -Uri "https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.zip" `
  -OutFile "gcp_bulk_user.zip"
Expand-Archive -Path .\gcp_bulk_user.zip -DestinationPath .
Set-Location .\gcp_bulk_user-main\python-gcloud

# 3. Virtualenv + deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Auth (say YES to quota project, pick one you own)
gcloud auth application-default login
```

## Other auth modes (CI / automation)

The default flow above uses your interactive `gcloud` login. For non-interactive environments:

- **Service account key:** `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` before running.
- **Workload Identity (GKE / Cloud Run / GCE):** attach a service account with the roles above; ADC picks up the metadata server automatically.
