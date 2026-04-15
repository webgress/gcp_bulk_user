"""CLI entry point for GCP Transfer Appliance status viewer."""

import argparse
import csv
import json
import sys
from datetime import datetime
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .appliances import get_all_appliances
from .projects import list_org_projects

DEFAULT_TZ = "America/Los_Angeles"  # PST/PDT, handles DST automatically.

PANTHEON_BASE = "https://pantheon.corp.google.com"


def _appliance_url(project: str, location: str, appliance_id: str) -> str:
    # Pantheon appliance detail page. Falls back to the project home if we
    # couldn't parse a location out of the resource name.
    if not location:
        return _project_url(project)
    query = urlencode({"project": project})
    safe_location = quote(location, safe="")
    safe_appliance_id = quote(appliance_id, safe="")
    return (f"{PANTHEON_BASE}/appliances/{safe_location}/{safe_appliance_id}"
            f";tab=configuration?{query}")


def _project_url(project: str) -> str:
    return f"{PANTHEON_BASE}/home/dashboard?{urlencode({'project': project})}"

# Appliance state colors (keys are compared case-insensitively).
# Real v1alpha1 states seen so far: DRAFT, REQUESTED, PREPARING,
# SHIPPING_TO_CUSTOMER, ON_SITE, PROCESSING, WIPED, CANCELLED.
STATE_COLORS = {
    "DRAFT":                "dim",
    "REQUESTED":            "yellow",
    "PREPARING":            "yellow",
    "SHIPPING_TO_CUSTOMER": "cyan",
    "ON_SITE":              "green",
    "PROCESSING":           "magenta",
    "WIPED":                "blue",
    "CANCELLED":            "red",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="View Google Transfer Appliance status across GCP projects in an org.",
    )
    parser.add_argument(
        "--org-id", required=True,
        help="GCP organization ID (numeric).",
    )
    parser.add_argument(
        "--projects", nargs="*",
        help="Specific project IDs to query (default: auto-discover from org).",
    )
    parser.add_argument(
        "--format", choices=["table", "json", "csv"], default="table",
        dest="output_format",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--workers", type=_positive_int, default=10,
        help="Max parallel workers for API calls (default: 10).",
    )
    parser.add_argument(
        "--state-filter", nargs="*",
        help="Only show appliances in these states (e.g. ACTIVE SHIPPING).",
    )
    parser.add_argument(
        "--timezone", default=DEFAULT_TZ,
        help=f"IANA timezone for table timestamps (default: {DEFAULT_TZ}). "
             "JSON/CSV output keeps raw ISO-8601 from the API.",
    )
    return parser


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _format_ts(iso_str: str, tz: ZoneInfo) -> str:
    """Format an ISO-8601 timestamp for humans, in the given tz."""
    if not iso_str or iso_str == "N/A":
        return iso_str or "N/A"
    # Google APIs return "...Z"; fromisoformat accepts +00:00 in 3.11+,
    # so normalise manually for 3.9/3.10 compat.
    s = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso_str  # fall back to raw string if we can't parse it
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _safe_csv_cell(value: object) -> str:
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return f"'{text}"
    return text


def _dedupe_project_ids(project_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(project_ids))


def render_table(appliances: list[dict], tz: ZoneInfo) -> None:
    console = Console()
    table = Table(title="Transfer Appliance Status", show_lines=True)
    table.add_column("Project", style="bold")
    table.add_column("Appliance ID")
    table.add_column("Type")
    table.add_column("State")
    table.add_column("Created")
    table.add_column("Updated")

    for a in appliances:
        state = str(a["state"])
        color = STATE_COLORS.get(state.upper(), "white")
        project = str(a["project"])
        appliance_id = str(a["appliance_id"])
        proj_link = _project_url(project)
        app_link = _appliance_url(project, str(a.get("location", "")),
                                  appliance_id)
        project_text = Text(project, style="bold")
        project_text.stylize(f"link {proj_link}")
        appliance_text = Text(appliance_id)
        appliance_text.stylize(f"link {app_link}")
        state_text = Text(state, style=color)
        table.add_row(
            project_text,
            appliance_text,
            str(a["type"]),
            state_text,
            _format_ts(a["create_time"], tz),
            _format_ts(a["update_time"], tz),
        )

    console.print(table)


def render_csv(appliances: list[dict]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "project",
        "appliance_id",
        "type",
        "state",
        "create_time",
        "update_time",
    ])
    for a in appliances:
        writer.writerow([
            _safe_csv_cell(a["project"]),
            _safe_csv_cell(a["appliance_id"]),
            _safe_csv_cell(a["type"]),
            _safe_csv_cell(a["state"]),
            _safe_csv_cell(a["create_time"]),
            _safe_csv_cell(a["update_time"]),
        ])


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        _log(f"Unknown timezone: {args.timezone!r}. Use an IANA name like "
             "'America/Los_Angeles' or 'UTC'.")
        sys.exit(2)

    # Discover projects
    if args.projects:
        project_ids = _dedupe_project_ids(args.projects)
        _log(f"Using {len(project_ids)} specified project(s).")
    else:
        _log(f"Discovering projects in org {args.org_id}...")
        try:
            projects = list_org_projects(args.org_id)
        except Exception as e:
            _log(f"Failed to discover projects: {type(e).__name__}: {e}")
            sys.exit(2)
        if not projects:
            _log("No projects found in organization.")
            sys.exit(1)
        project_ids = _dedupe_project_ids([p["project_id"] for p in projects])
        _log(f"Found {len(project_ids)} project(s).")

    # Fetch appliance statuses
    _log("Querying Transfer Appliance status...")
    try:
        scan_results = get_all_appliances(project_ids, max_workers=args.workers)
    except Exception as e:
        _log(f"Failed to query Transfer Appliance status: {type(e).__name__}: {e}")
        sys.exit(2)
    appliances = scan_results.appliances

    # Apply state filter
    if args.state_filter:
        filter_states = {s.upper() for s in args.state_filter}
        appliances = [
            a for a in appliances
            if a["state"].upper() in filter_states
        ]

    if scan_results.errors:
        _log(f"Scan failed for {len(scan_results.errors)} project(s); "
             "results may be incomplete:")
        for error in scan_results.errors:
            _log(f"  {error['project']}: {error['error']}")

    if not appliances:
        if scan_results.errors:
            _log("No Transfer Appliances found in successfully scanned projects.")
            sys.exit(2)
        _log("No Transfer Appliances found across scanned projects.")
        sys.exit(0)

    _log(f"Found {len(appliances)} appliance(s).\n")

    # Output
    if args.output_format == "json":
        print(json.dumps(appliances, indent=2))
    elif args.output_format == "csv":
        render_csv(appliances)
    else:
        render_table(appliances, tz)

    if scan_results.errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
