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

## Install — Windows (recommended)

Uses Google's official, code-signed installer (signed by **Google LLC**), which most corporate antivirus / endpoint protection allow. ~10 minutes total. **Do not** use Python from the Microsoft Store — its sandbox redirects writes under `%APPDATA%`, which silently breaks the credential hand-off.

### Step 1 — Install the Google Cloud SDK

1. Download: <https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe>
2. Double-click `GoogleCloudSDKInstaller.exe`.
3. Click **Next** through the wizard. Accept the defaults — *Single user*, **Bundled Python** (important — avoids the Store-Python trap), *Add gcloud to PATH*.
4. Leave **"Run gcloud init"** checked and click **Finish**. A blue *Google Cloud SDK Shell* window opens automatically.

### Step 2 — Install Python 3.10+

1. Download: <https://www.python.org/downloads/>
2. Run the installer. **Tick "Add python.exe to PATH"** at the bottom of the first screen.
3. Click **Install Now** and accept defaults.

### Step 3 — Authenticate

In the Cloud SDK Shell from Step 1 (or open it later from **Start → Google Cloud SDK Shell**):

```cmd
gcloud auth application-default login
```

Sign in in the browser, click **Allow**. When asked *"Do you want to set a quota project?"*, type **`y`** and pick a project you own.

The last line should be:

```
Credentials saved to file: [C:\Users\<you>\AppData\Roaming\gcloud\application_default_credentials.json]
```

### Step 4 — Download and install the tool

In the Cloud SDK Shell:

```cmd
curl -L -o gcp_bulk_user.zip https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.zip
tar -xf gcp_bulk_user.zip
cd gcp_bulk_user-main\python-gcloud

python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

gcloud components install alpha
```

(Both `curl` and `tar` are bundled with Windows 10 1803+ and the Cloud SDK Shell.)

### Step 5 — Run

```cmd
python -m gcp_appliance_status --org-id YOUR_ORG_ID
```

You should see a table of Transfer Appliance status. Subsequent runs only need:

```cmd
cd %USERPROFILE%\gcp_bulk_user-main\python-gcloud
.venv\Scripts\activate
python -m gcp_appliance_status --org-id YOUR_ORG_ID
```

### Troubleshooting (Windows)

**`User project specified in the request is invalid`** — re-run `gcloud auth application-default set-quota-project YOUR_PROJECT`.

**`ModuleNotFoundError: No module named 'gcp_appliance_status'`** — the virtualenv isn't active. Run `.venv\Scripts\activate` from the `python-gcloud` directory.

**No `gcloud` directory in `%APPDATA%` after running auth** — you ran `gcloud` from Microsoft Store Python. Find the misplaced credentials:
```powershell
Get-ChildItem "$env:LOCALAPPDATA\Packages\PythonSoftwareFoundation.*" -Recurse -Filter "application_default_credentials.json" -ErrorAction SilentlyContinue
```
Uninstall Store Python (or open the Cloud SDK Shell *fresh*, which uses bundled Python) and re-run `gcloud auth application-default login`.

## Install — Windows (advanced, scripted)

> **Not recommended for non-technical users.** Uses a PowerShell script + the gcloud zip distribution rather than Google's signed installer. Known to fail when the user has Microsoft Store Python on PATH (the Store sandbox redirects gcloud's writes under `%APPDATA%`, breaking the credential hand-off). Prefer the [recommended Windows flow](#install--windows-recommended) above.

```powershell
iwr -useb https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.ps1 | iex
```

If execution policy blocks the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

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
