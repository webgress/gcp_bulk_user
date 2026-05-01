# go-binary — Transfer Appliance Status Viewer

> **Status: not yet built.** A sandbox agent will produce this implementation from `SPEC.md`. When it lands, this README will document install and usage.

## Intended install (target experience)

Download one binary for your OS:

```bash
# macOS (Apple Silicon)
curl -L -o gcp-appliance-status https://github.com/yuriy/gcp_bulk_user/releases/latest/download/gcp-appliance-status-darwin-arm64
chmod +x gcp-appliance-status
./gcp-appliance-status --org-id 123456789
```

No Python, no gcloud, no package manager. The binary handles its own OAuth flow on first run.

See [SPEC.md](SPEC.md) for the full requirements driving the implementation.


Note: macos & windows test environments are not available in this implementation sandbox, I will test things manually. Use linux version locally.