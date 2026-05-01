# GCP Transfer Appliance Status Viewer

CLI tool to view Transfer Appliance status across all projects in a GCP organization.

This repo contains **two implementations** of the same tool. Pick the one that matches how you want to install and run it.

| Folder | Install model | Runtime deps | Best for |
|---|---|---|---|
| [python-gcloud/](python-gcloud/) | `pip install -r requirements.txt` | Python 3.10+, gcloud SDK | Use this if your environment already has python and gcloud installed. |
| [go-binary/](go-binary/) | Download one binary | None | Same process and output as the python implementation. Convenient single file download, no dependencies to install. |

Both produce identical output and accept the same CLI flags. They differ only in install footprint.

## Which should I use?

- **Lowest effort:** `go-binary`.
- **Already have Python + gcloud:** `python-gcloud`.

## Required permissions

The identity running this tool needs three IAM bindings. Grant once and use with any implementation:

| Role | Scope | Why |
|---|---|---|
| `roles/browser` | Organization | List projects in the org via Cloud Resource Manager |
| `roles/transferappliance.viewer` | Organization (or per-project) | Read Transfer Appliance order status |
| `roles/serviceusage.serviceUsageConsumer` | The **quota project** (see below) | Allow API calls to be billed against that project |

If `roles/transferappliance.viewer` isn't yet available in your environment, fall back to `roles/viewer` on each project being queried.

### About the quota project

Every Google Cloud API call has to be charged against a project for quota and billing purposes — this is the **quota project**, and it's separate from the projects you're querying. Practically any project you own will work here.

- When listing projects org-wide, you must pass `--quota-project=YOUR_ADMIN_PROJECT` explicitly.
- When you pass `--projects proj-a proj-b ...` directly, the first project in the list is used as the quota project automatically (override with `--quota-project` if you want).
- The `python-gcloud` variant inherits the quota project from your `gcloud auth application-default login` session, so the flag is optional there.

> **Note on API cost and quota:** this tool's API usage is trivial — one read call per project against free management APIs (Cloud Resource Manager + Transfer Appliance), returning a few KB per project. A typical run against a 100-project org makes ~100 calls. There is **no billable cost**, and quota concerns only arise if you script this to run on a tight schedule (the default Resource Manager quota is 600 reads/minute). For interactive use you can ignore both.

See each folder's `README.md` for full install and usage instructions.
