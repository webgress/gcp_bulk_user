# SPEC — go-binary

Reimplement the Transfer Appliance status viewer in Go as a single static binary with zero runtime dependencies.

## Reference implementation

The Python reference is at `../python-gcloud/gcp_appliance_status/`. Read it first — it defines behavior, CLI flags, output formats, field normalization, and the parallel-fetch model. The Go binary must be behavior-equivalent.

## Goals

1. Single static binary per OS/arch. No external runtime (no Python, no gcloud, no shared libs beyond libc).
2. Cross-compile targets: `darwin-arm64`, `darwin-amd64`, `linux-amd64`, `linux-arm64`, `windows-amd64`.
3. Identical CLI surface and output to the Python reference (flags, table/JSON/CSV formats, field names).

## Non-goals

- No `gcloud alpha` fallback. v1 REST only.
- No new output schemas. The only new CLI flag permitted is `--quota-project` (see CLI section).
- No config files. All state is the OAuth token cache.

## Required design

### Project layout

```
go-binary/
├── go.mod
├── go.sum
├── main.go                # entry point, flag parsing, output rendering
├── auth.go                # OAuth flow + token cache
├── projects.go            # Resource Manager: list projects under an org
├── appliances.go          # Transfer Appliance v1 REST calls
├── Makefile               # cross-compile targets
└── README.md
```

Module path: `github.com/yuriy/gcp_bulk_user/go-binary` (or whatever the actual repo path resolves to).

### Auth

Use `golang.org/x/oauth2/google` for the OAuth flow. Same model as `python-uv`:

- Read OAuth client ID/secret from env vars `GCP_OAUTH_CLIENT_ID` / `GCP_OAUTH_CLIENT_SECRET`. (See the `python-uv` SPEC for why we don't ship a client ID.)
- On first run, open a local HTTP listener on a random port, open the browser to the consent URL, capture the code on redirect, exchange for a token.
- Cache the token at `~/.config/gcp-appliance-status/credentials.json` (Linux/macOS) or `%APPDATA%\gcp-appliance-status\credentials.json` (Windows).
- Refresh automatically on subsequent runs.
- Scope: `https://www.googleapis.com/auth/cloud-platform`.

### Project discovery

Use `cloud.google.com/go/resourcemanager/apiv3`. Filter to `state = ACTIVE` under `organizations/{ORG_ID}`. Same logic as `../python-gcloud/gcp_appliance_status/projects.py`.

### Appliance fetch

There is **no Google-published Go client for Transfer Appliance v1**. Hand-roll the REST call:

- Endpoint: `GET https://transferappliance.googleapis.com/v1/projects/{project}/locations/-/orders`
- Auth: `Authorization: Bearer {access_token}` from the OAuth2 token source.
- Response: parse `orders` array. Field names match the JSON in `../python-gcloud/gcp_appliance_status/appliances.py` (`name`, `state`, `applianceType`, `createTime`, `updateTime`).
- On non-2xx response, print to stderr and continue:
  ```
  Project {project_id}: Transfer Appliance API returned {status}.
  The v1 API is in early access — please contact your Google Cloud support representative if this persists.
  ```
- Run project queries in parallel with a worker pool. Default `--workers 10`, configurable via flag (matches Python).

### CLI

Use the standard library `flag` package or `github.com/spf13/pflag` for GNU-style long flags. Required parity with Python:

| Flag | Behavior |
|---|---|
| `--org-id` (required) | Org ID to scan |
| `--projects` | Space-separated project list; skips org-wide discovery |
| `--quota-project` | Project to charge API quota against (see resolution rules below) |
| `--state-filter` | Filter results by state (e.g. `ACTIVE SHIPPING`) |
| `--format` | `table` (default), `json`, `csv` |
| `--workers` | Default 10 |

#### Quota project resolution

Every Google API call needs a quota project (separate from the projects being queried). Resolve in this order:

1. If `--quota-project` is set, use it.
2. Else if env var `GCP_QUOTA_PROJECT` is set, use it.
3. Else if `--projects` is provided, use the **first project** in that list.
4. Else (org-wide discovery with no quota project), exit with status 2 and this message to stderr:
   ```
   A quota project is required for org-wide project discovery.
   Pass --quota-project YOUR_PROJECT (a project you own that has the
   Service Usage API enabled), or restrict this run with --projects to
   auto-derive one from the list.
   ```

Apply the resolved value by setting the `X-Goog-User-Project` header on every outbound request — both the Resource Manager Go client (use a custom HTTP client / `option.WithRequestReason` won't do this; wrap the transport) and the hand-rolled Transfer Appliance call.

### Output

- **table:** use `github.com/jedib0t/go-pretty/v6/table` (or `pterm`). Match column order from the Python reference: Project, Order ID, Type, State, Created, Updated.
- **json:** marshal the same shape the Python tool produces. Sort by project then order_id for stable output.
- **csv:** standard library `encoding/csv`. Same columns as table.

### Build

`Makefile` with these targets:

```
make build          # build for host OS/arch into ./bin/gcp-appliance-status
make release        # cross-compile all five targets into ./dist/
make clean
```

Use `CGO_ENABLED=0` and `-ldflags="-s -w"` for small static binaries.

## Acceptance criteria

On a clean machine with only Go 1.22+ installed for building (and nothing for running):

1. `make build` produces a working binary in `./bin/`.
2. `make release` produces five binaries in `./dist/` and each runs `--help` successfully on its target platform.
3. The host binary copied to a machine with **no Go, no Python, no gcloud** runs `--help` without error.
4. First run with valid `GCP_OAUTH_CLIENT_ID/SECRET` opens a browser, completes OAuth, caches the token, prints results.
5. Second run uses the cached token without re-prompting.
6. JSON output, sorted, matches `python-gcloud`'s JSON output for the same org.
7. CSV output is parseable by `csvkit` / `awk -F,` / spreadsheet apps.
8. `ldd ./bin/gcp-appliance-status` (Linux) shows no dynamic deps beyond libc, or `otool -L` (macOS) shows only system libraries.
9. Binary size under 20 MB stripped.
10. Running `--org-id X` with no `--projects` and no `--quota-project` exits with status 2 and the documented error — no API calls made.
11. Running `--projects proj-a proj-b` with no `--quota-project` succeeds and uses `proj-a` as the quota project.

Report each criterion as pass/fail in your final message.
