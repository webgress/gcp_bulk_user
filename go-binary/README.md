# go-binary — Transfer Appliance Status Viewer

Single static binary that views Google Transfer Appliance status across every project in a GCP organization. No Python, no gcloud, no shared libraries beyond libc — drop the file in your `PATH` and run it.

Behavior-equivalent with the [`python-gcloud`](../python-gcloud/) implementation: same flags, same output schemas, same Pantheon links, same parallel scan model.

**This binary shares Application Default Credentials (ADC) with `gcloud`.** It reads from and writes to the same file gcloud uses (`~/.config/gcloud/application_default_credentials.json`) in the same `authorized_user` JSON format. So:

- If you've already run `gcloud auth application-default login`, this binary uses that token with **no separate login**.
- If you log in via this binary first, subsequent gcloud commands use the same token.
- `gcp-appliance-status --logout` and `gcloud auth application-default revoke` are interchangeable.

## Install

The binary is pure Go — drop it on your `PATH` and run it. The only optional setup is provisioning credentials (see [Authentication](#authentication) below); a one-shot script that installs the Google Cloud SDK and runs `gcloud auth application-default login` is provided for convenience.

### Step 1 — Provision credentials (one-shot)

If you already use `gcloud auth application-default login`, skip this. Otherwise the easiest path on macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/go-binary/install.sh | bash
```

That script installs the Google Cloud SDK if missing and runs the auth flow. It does **not** download the binary itself — that's step 2.

To inspect first:

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/go-binary/install.sh
less install.sh
bash install.sh
```

Windows (PowerShell 5.1+):

```powershell
iwr -useb https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/go-binary/install.ps1 | iex
```

If execution policy blocks the script: `powershell -ExecutionPolicy Bypass -File .\install.ps1`. To inspect first, download with `iwr -OutFile install.ps1 ...` and open in Notepad before running.

### Step 2 — Download the binary

```bash
# macOS (Apple Silicon)
curl -L -o gcp-appliance-status \
  https://github.com/webgress/gcp_bulk_user/releases/latest/download/gcp-appliance-status-darwin-arm64
chmod +x gcp-appliance-status

# Linux x86_64
curl -L -o gcp-appliance-status \
  https://github.com/webgress/gcp_bulk_user/releases/latest/download/gcp-appliance-status-linux-amd64
chmod +x gcp-appliance-status
```

Build targets shipped in each release:

| Filename suffix | OS / arch |
|---|---|
| `darwin-arm64` | macOS, Apple Silicon |
| `darwin-amd64` | macOS, Intel |
| `linux-amd64`  | Linux x86_64 |
| `linux-arm64`  | Linux aarch64 |
| `windows-amd64.exe` | Windows 10/11 x86_64 |

> **Sandbox note.** This implementation is built and smoke-tested on Linux. macOS and Windows targets cross-compile cleanly but are exercised manually by the maintainer.

## Authentication

You have two ways to provision credentials. Pick whichever is more convenient — the resulting file format is identical and the two tools share it.

### Option A — use gcloud (recommended if you already have it)

```bash
gcloud auth application-default login
```

Done. This binary will pick up the same credentials on its next run. No env vars, no separate sign-in.

### Option B — use this binary's own OAuth flow (no gcloud install needed)

Provide a desktop OAuth client of your own (we don't ship one — embedding a public `client_secret` would be a footgun):

1. Open the [GCP credentials console](https://console.cloud.google.com/apis/credentials) in any project you own.
2. **Create credentials → OAuth client ID → Application type: Desktop app**. Name it whatever you like.
3. Note the **Client ID** and **Client secret**.
4. Export them in the shell that runs the binary:

   ```bash
   export GCP_OAUTH_CLIENT_ID='123456789012-abcdef....apps.googleusercontent.com'
   export GCP_OAUTH_CLIENT_SECRET='GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx'
   ```

On first run, the binary opens a browser for consent and writes the resulting credentials to the shared ADC file.

### Where the credentials live

| OS | Path |
|---|---|
| Linux / macOS | `~/.config/gcloud/application_default_credentials.json` |
| Windows | `%APPDATA%\gcloud\application_default_credentials.json` |

Override the parent directory with the standard `CLOUDSDK_CONFIG` env var (the same one gcloud honors). On POSIX the file is mode `0600`, the parent directory `0700`.

### Logging out

```bash
./gcp-appliance-status --logout
```

Same behavior as `gcloud auth application-default revoke`: revokes the refresh token at `oauth2.googleapis.com/revoke` and deletes the local file. Both tools now have nothing — log back in via either Option A or B.

## Grant IAM (one-time, at the org level)

Same bindings as the `python-gcloud` variant:

```bash
ORG_ID=123456789
MEMBER="user:you@example.com"

gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/browser"
gcloud organizations add-iam-policy-binding $ORG_ID --member="$MEMBER" --role="roles/transferappliance.viewer"
```

If `roles/transferappliance.viewer` isn't available in your org yet, fall back to `roles/viewer` per project. You also need `roles/serviceusage.serviceUsageConsumer` on the **quota project** (any project you own).

## Run

Org-wide discovery requires a `--quota-project` (any project you own that has the Service Usage API enabled). When you pass `--projects` directly the first project is used as the quota project automatically.

```bash
# Default: table output, every project in the org
./gcp-appliance-status --org-id 123456789 --quota-project my-admin-project

# Specific projects only (first project doubles as quota)
./gcp-appliance-status --org-id 123456789 --projects proj-a proj-b

# Filter by state
./gcp-appliance-status --org-id 123456789 --quota-project my-admin-project \
  --state-filter ON_SITE PROCESSING

# JSON / CSV / HTML
./gcp-appliance-status --org-id 123456789 --quota-project my-admin-project --format json
./gcp-appliance-status --org-id 123456789 --quota-project my-admin-project --format csv > appliances.csv
./gcp-appliance-status --org-id 123456789 --quota-project my-admin-project --format html --html-file report.html
```

Run `./gcp-appliance-status --help` for the full flag list.

### Quota project resolution order

Every Google Cloud API call must charge a project for quota and billing. The binary picks one in this order:

1. `--quota-project` flag.
2. `GCP_QUOTA_PROJECT` env var.
3. `quota_project_id` from the ADC file — set persistently with `gcloud auth application-default set-quota-project YOUR_PROJECT`. This is the gcloud-native path and survives across sessions.
4. The first project in `--projects` when that flag is supplied (auto-derive convenience).
5. **Otherwise it exits with status 2** and tells you how to set one.

The resolved value is sent as `X-Goog-User-Project` on every request — both the Resource Manager `projects:search` call used for org discovery and the Transfer Appliance v1alpha1 call used per project.

## How it works

1. Lists active projects under the org via Cloud Resource Manager (`v3/projects:search`, `parent:organizations/{org} state:ACTIVE`).
2. For each project, calls `transferappliance.googleapis.com/v1alpha1/projects/{p}/locations/-/appliances` with the cached OAuth token, parallelised across `--workers` goroutines.
3. Per-record fields are normalized to match the `python-gcloud` JSON shape exactly (`appliance_id`, `model`, etc.).
4. Aggregates and renders to table / JSON / CSV / HTML.

There is **no `gcloud alpha` fallback** here — the whole point of this binary is zero runtime deps. If a project's REST call fails, the failure is logged to stderr and that project is reported as errored at the end (and the process exits with status 2 if any project failed).

## Build from source

Requires Go 1.22 or later. Nothing else.

```bash
cd go-binary

make build           # ./bin/gcp-appliance-status for the host OS/arch
make release         # cross-compile all five targets into ./dist/
make test            # unit tests (offline, no GCP access needed)
make clean
```

The release builds use `CGO_ENABLED=0` and `-ldflags="-s -w"`. Stripped binaries are ~6 MB each.

## Troubleshooting

**"no credentials at … and GCP_OAUTH_CLIENT_ID and GCP_OAUTH_CLIENT_SECRET are not set"** — either run `gcloud auth application-default login` (Option A above), or set the two env vars and let this binary do its own browser flow (Option B).

**"A quota project is required for org-wide project discovery"** — pass `--quota-project YOUR_PROJECT`, or persist one with `gcloud auth application-default set-quota-project YOUR_PROJECT`.

**HTTP 403 from Transfer Appliance for every project** — the v1alpha1 API is in early access; reach out to your Google Cloud support representative if persistent.

**Browser doesn't open during first-run consent** — the URL is printed to stderr; copy it into a browser by hand. The local listener on `127.0.0.1:<random>` will accept the redirect.

**Force re-consent / switch Google account** — `gcp-appliance-status --logout` (or `gcloud auth application-default revoke`), then log back in via either option above.

See [SPEC.md](SPEC.md) for the requirements driving the implementation.
