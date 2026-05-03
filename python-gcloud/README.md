# python-gcloud — Transfer Appliance Status Viewer

Python CLI that queries Transfer Appliance status across every project in a GCP organization. Uses the v1 discovery API with a `gcloud alpha` fallback.

## Prerequisites

You need both of these on your `PATH` before installing:

1. **Python 3.10 or newer** — verify with `python --version` (or `python3 --version`). Install from <https://www.python.org/downloads/> if missing.
2. **Google Cloud SDK (`gcloud`)** — verify with `gcloud --version`. Install from <https://cloud.google.com/sdk/docs/install> if missing.

No GitHub account, no `git`, and no compiled binaries are required. Everything below uses standard tools that are already approved on most corporate laptops (`curl`/`tar` on macOS/Linux, `Invoke-WebRequest`/`Expand-Archive` in PowerShell).

## Install — macOS / Linux

```bash
# 1. Download the source tarball (no GitHub login required)
curl -L -o gcp_bulk_user.tar.gz \
  https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.tar.gz

# 2. Extract and enter the python-gcloud directory
tar -xzf gcp_bulk_user.tar.gz
cd gcp_bulk_user-main/python-gcloud

# 3. (Recommended) create an isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Make sure the gcloud alpha component is present
gcloud components install alpha

# 6. Authenticate. Say YES when asked to set a quota project, and pick a project you own.
gcloud auth application-default login
```

## Install — Windows (PowerShell)

```powershell
# 1. Download the source zip
Invoke-WebRequest `
  -Uri "https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.zip" `
  -OutFile "gcp_bulk_user.zip"

# 2. Extract and enter the python-gcloud directory
Expand-Archive -Path .\gcp_bulk_user.zip -DestinationPath .
Set-Location .\gcp_bulk_user-main\python-gcloud

# 3. (Recommended) create an isolated virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 4. Install Python dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Make sure the gcloud alpha component is present
gcloud components install alpha

# 6. Authenticate. Say YES when asked to set a quota project, and pick a project you own.
gcloud auth application-default login
```

> **Important — quota project:** during `gcloud auth application-default login`, gcloud asks whether to set a quota project. **Say yes** and pick a project you own. Without one, API calls fail with `User project specified in the request is invalid`. To change it later:
>
> ```bash
> gcloud auth application-default set-quota-project YOUR_PROJECT
> ```

## Verify the install

From the `python-gcloud` directory (with the virtualenv active), run:

```bash
python -m gcp_appliance_status --help
```

You should see the CLI help text. If you get `ModuleNotFoundError`, your virtualenv isn't active or step 4 didn't complete.

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

Re-run the download step and reinstall dependencies:

```bash
# macOS / Linux
curl -L -o gcp_bulk_user.tar.gz \
  https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.tar.gz
tar -xzf gcp_bulk_user.tar.gz
cd gcp_bulk_user-main/python-gcloud
source .venv/bin/activate    # if you created one previously
pip install -r requirements.txt
```

## Other auth modes (CI / automation)

The default flow above uses your interactive `gcloud` login. For non-interactive environments:

- **Service account key:** `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` before running.
- **Workload Identity (GKE / Cloud Run / GCE):** attach a service account with the roles above; ADC picks up the metadata server automatically.
