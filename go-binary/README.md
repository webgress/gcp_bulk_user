# go-binary — Transfer Appliance Status Viewer

Single static binary that views Google Transfer Appliance status across every project in a GCP organization. No Python, no gcloud, no shared libraries beyond libc — drop the file in your `PATH` and run it.

Behavior-equivalent with the [`python-gcloud`](../python-gcloud/) implementation: same flags, same output schemas, same Pantheon links, same parallel scan model. The only difference is **how you authenticate**: this binary opens its own browser-based OAuth flow on first run, so you don't need a working `gcloud` install or `gcloud auth application-default login`.

## Install

Download one binary for your OS:

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

## OAuth client setup (one-time)

Because this binary doesn't ship a baked-in OAuth client (we don't want a public `client_secret` in a public binary), you supply your own. Once per workstation:

1. Open the [GCP credentials console](https://console.cloud.google.com/apis/credentials) in any project you own.
2. **Create credentials → OAuth client ID → Application type: Desktop app**. Name it whatever you like.
3. Note the **Client ID** and **Client secret**.
4. Export them in the shell that runs the binary:

   ```bash
   export GCP_OAUTH_CLIENT_ID='123456789012-abcdef....apps.googleusercontent.com'
   export GCP_OAUTH_CLIENT_SECRET='GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx'
   ```

   (Add these to your shell profile if you'd rather not paste each session.)

On first run the binary will open a browser, you'll consent once, and the resulting token is cached at:

| OS | Path |
|---|---|
| Linux / macOS | `~/.config/gcp-appliance-status/credentials.json` |
| Windows | `%APPDATA%\gcp-appliance-status\credentials.json` |

Subsequent runs refresh the token silently. To force re-consent, delete that file.

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
3. The first project in `--projects` when that flag is supplied.
4. **Otherwise it exits with status 2** and tells you to pass `--quota-project`.

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

**"OAuth credentials not configured"** — set `GCP_OAUTH_CLIENT_ID` and `GCP_OAUTH_CLIENT_SECRET` per the OAuth client setup section above.

**"A quota project is required for org-wide project discovery"** — pass `--quota-project YOUR_PROJECT` (any project you own).

**HTTP 403 from Transfer Appliance for every project** — the v1alpha1 API is in early access; reach out to your Google Cloud support representative if persistent.

**Browser doesn't open during first-run consent** — the URL is printed to stderr; copy it into a browser by hand. The local listener on `127.0.0.1:<random>` will accept the redirect.

**Force re-consent / switch Google account** — delete the credentials file shown in the table above and re-run.

See [SPEC.md](SPEC.md) for the requirements driving the implementation.
