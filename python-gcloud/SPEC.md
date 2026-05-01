# SPEC — python-gcloud

This folder is the **baseline / "as is" implementation**. The code already works.

## Sandbox task

Verify the install instructions in `README.md` work end-to-end on a clean machine. **Do not change behavior.** No refactors, no dependency bumps, no auth-flow changes.

## Allowed changes

- Tighten wording in `README.md` if a step is ambiguous.
- Add missing troubleshooting entries discovered during the verification run.
- Pin `requirements.txt` versions if the unpinned upper bounds cause install failures.

## Forbidden changes

- Do not modify any `.py` file in `gcp_appliance_status/`.
- Do not remove the `gcloud alpha` fallback in `appliances.py`.
- Do not change the CLI surface (flags, output formats).

## Acceptance criteria

On a fresh macOS or Linux box with only Python 3.10+ and gcloud SDK installed:

1. The four commands in the **Install** section of `README.md` complete without error.
2. `python -m gcp_appliance_status --help` prints usage.
3. `python -m gcp_appliance_status --org-id <real-org> --format json` returns valid JSON (non-empty if the org has appliances).
4. Same command with `--format csv` produces a parseable CSV.

Report results as a checklist in your final message. If any step fails, fix the README — not the code.
