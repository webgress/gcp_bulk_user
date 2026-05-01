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

This binary is **interoperable with gcloud's Application Default Credentials (ADC)**. It reads from and writes to the same file gcloud uses, in the same format, at the same path. Practical effect:

- If the user has already run `gcloud auth application-default login`, this binary uses that token with **no separate login**.
- If the user logs in via this binary first, subsequent gcloud commands (e.g. `gcloud auth application-default print-access-token`) will use the same token.
- Either tool's logout invalidates the token for the other.

We are not building a parallel auth system; we are an automation on top of the standard ADC file.

#### Config directory resolution

Use the same precedence as gcloud:

1. `$CLOUDSDK_CONFIG` if set — the credentials file lives at `$CLOUDSDK_CONFIG/application_default_credentials.json`.
2. Else on Linux/macOS: `~/.config/gcloud/application_default_credentials.json`.
3. Else on Windows: `%APPDATA%\gcloud\application_default_credentials.json`.

Do not invent a separate `gcp-appliance-status/` directory. Use `gcloud/` exactly.

#### File format (gcloud-compatible authorized_user)

Read and write the standard ADC JSON schema for user credentials:

```json
{
  "type": "authorized_user",
  "client_id": "...",
  "client_secret": "...",
  "refresh_token": "...",
  "quota_project_id": "..."
}
```

`quota_project_id` is optional in the file. When present, it provides the default quota project (see resolution rules below). The Go `golang.org/x/oauth2/google.CredentialsFromJSON` helper parses this format natively — use it rather than rolling your own parser.

#### OAuth flow on first login

If the file is missing or has no `refresh_token`:

- Read OAuth client ID/secret from env vars `GCP_OAUTH_CLIENT_ID` / `GCP_OAUTH_CLIENT_SECRET`. (See the prior `python-uv` SPEC for why we don't ship a client ID.)
- Open a local HTTP listener on a random port, open the browser to the consent URL, capture the code on redirect, exchange for a refresh token.
- Write the file with mode `0600` and parent directory mode `0700` on POSIX. (Windows inherits user-private ACLs from `%APPDATA%` — no explicit mode needed.) These match gcloud's own permissions.
- Scope: `https://www.googleapis.com/auth/cloud-platform`.

#### Logout — match `gcloud auth application-default revoke`

Add a `--logout` flag. Behavior must mirror `gcloud auth application-default revoke`:

1. Read the refresh token from the credentials file. If the file is missing or has no token, print `Already logged out.` to stdout and exit `0`.
2. POST to `https://oauth2.googleapis.com/revoke` with body `token=<refresh_token>` and `Content-Type: application/x-www-form-urlencoded`. This invalidates the token server-side so a stolen file copy cannot be used.
3. Whether the revoke call succeeds or fails (e.g. token already revoked → 400), delete the local credentials file.
4. On revoke success: print `Logged out.` and exit `0`.
5. On revoke HTTP failure: print the HTTP status to stderr but still delete the file and exit `0` — the local state is what the user controls; server-side revocation is best-effort. (gcloud behaves the same way.)

`--logout` does not perform any other API calls and does not require `--org-id`.

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
| `--org-id` (required, except with `--logout`) | Org ID to scan |
| `--projects` | Space-separated project list; skips org-wide discovery |
| `--quota-project` | Project to charge API quota against (see resolution rules below) |
| `--state-filter` | Filter results by state (e.g. `ACTIVE SHIPPING`) |
| `--format` | `table` (default), `json`, `csv` |
| `--workers` | Default 10 |
| `--logout` | Delete the cached OAuth token and exit (see Auth → Logout) |

#### Quota project resolution

Every Google API call needs a quota project (separate from the projects being queried). Resolve in this order:

1. If `--quota-project` is set, use it.
2. Else if env var `GCP_QUOTA_PROJECT` is set, use it.
3. Else if the ADC file has `quota_project_id` (set by `gcloud auth application-default set-quota-project`), use it. This is the gcloud-native path — explicit user choice persisted in the standard file.
4. Else if `--projects` is provided, use the **first project** in that list as a convenience auto-derivation.
5. Else (org-wide discovery with nothing set anywhere), exit with status 2 and this message to stderr:
   ```
   A quota project is required for org-wide project discovery.
   Pass --quota-project YOUR_PROJECT, or set one persistently with:
       gcloud auth application-default set-quota-project YOUR_PROJECT
   Alternatively, restrict this run with --projects to auto-derive one from the list.
   ```

Apply the resolved value by setting the `X-Goog-User-Project` header on every outbound request — both the Resource Manager Go client (use a custom HTTP client; `option.WithRequestReason` won't do this; wrap the transport) and the hand-rolled Transfer Appliance call.

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
12. After a successful login, `stat -c '%a' ~/.config/gcloud/application_default_credentials.json` (Linux) or `stat -f '%A' …` (macOS) returns `600`. Parent directory is `700`.
13. `gcp-appliance-status --logout` revokes via the OAuth2 endpoint, deletes the file, and prints `Logged out.`. Running again prints `Already logged out.`. Both exit `0`. Neither requires `--org-id`.
14. **gcloud interop A:** running `gcloud auth application-default login` followed by `gcp-appliance-status --org-id X --quota-project Y` succeeds with no separate login from the binary.
15. **gcloud interop B:** running `gcp-appliance-status` first (going through its own OAuth flow), then `gcloud auth application-default print-access-token`, prints a valid token without re-prompting.
16. **gcloud interop C:** `gcp-appliance-status --logout` followed by `gcloud auth application-default print-access-token` errors with "no credentials" (or equivalent) — the revoke + delete properly invalidates state for both tools.
17. **Quota-project ADC path:** `gcloud auth application-default set-quota-project Y` followed by `gcp-appliance-status --org-id X` (no `--quota-project`, no `--projects`, no env var) succeeds and uses `Y` as the quota project.

Report each criterion as pass/fail in your final message.

## Changelog

- **2026-05-01:** initial spec.
- **2026-05-01:** added `--quota-project` flag with auto-derivation from `--projects`.
- **2026-05-01:** token cache honors `XDG_CONFIG_HOME`; require `0600` perms on cache file and `0700` on its directory; added `--logout` flag.
- **2026-05-01:** **dropped XDG; aligned fully with gcloud ADC.** Token cache lives at `~/.config/gcloud/application_default_credentials.json` (gcloud's path), uses gcloud's `authorized_user` JSON schema, honors `CLOUDSDK_CONFIG` for the parent dir override. `--logout` now revokes server-side via the OAuth2 endpoint to match `gcloud auth application-default revoke`. Quota project resolution gained a step 3 that reads `quota_project_id` from the ADC file (set by `gcloud auth application-default set-quota-project`).
- **2026-05-01:** initial implementation landed in commit `20f52fa` ("Implement go-binary Transfer Appliance status viewer"). It was built against an earlier version of this SPEC and uses `~/.config/gcp-appliance-status/credentials.json` with `XDG_CONFIG_HOME` support — i.e. the implementation does **not** yet match the gcloud-ADC alignment described above. **Next-iteration scope** for the sandbox: migrate the cache to gcloud's ADC path/format/`CLOUDSDK_CONFIG`, add server-side revoke on `--logout`, and add the ADC `quota_project_id` step (new step 3) to quota-project resolution. CLI surface, output formats (table/JSON/CSV/HTML), and the Transfer Appliance fetch logic must remain unchanged.
