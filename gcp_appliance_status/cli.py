"""CLI entry point for GCP Transfer Appliance status viewer."""

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.table import Table

from .appliances import get_all_appliances
from .projects import list_org_projects

DEFAULT_TZ = "America/Los_Angeles"  # PST/PDT, handles DST automatically.

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
        "--workers", type=int, default=10,
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
        state = a["state"]
        color = STATE_COLORS.get(state.upper(), "white")
        table.add_row(
            a["project"],
            a["appliance_id"],
            a["type"],
            f"[{color}]{state}[/{color}]",
            _format_ts(a["create_time"], tz),
            _format_ts(a["update_time"], tz),
        )

    console.print(table)


def render_csv(appliances: list[dict]) -> None:
    print("project,appliance_id,type,state,create_time,update_time")
    for a in appliances:
        print(f'{a["project"]},{a["appliance_id"]},{a["type"]},{a["state"]},'
              f'{a["create_time"]},{a["update_time"]}')


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
        project_ids = args.projects
        _log(f"Using {len(project_ids)} specified project(s).")
    else:
        _log(f"Discovering projects in org {args.org_id}...")
        projects = list_org_projects(args.org_id)
        if not projects:
            _log("No projects found in organization.")
            sys.exit(1)
        project_ids = [p["project_id"] for p in projects]
        _log(f"Found {len(project_ids)} project(s).")

    # Fetch appliance statuses
    _log("Querying Transfer Appliance status...")
    appliances = get_all_appliances(project_ids, max_workers=args.workers)

    # Apply state filter
    if args.state_filter:
        filter_states = {s.upper() for s in args.state_filter}
        appliances = [a for a in appliances if a["state"] in filter_states]

    if not appliances:
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


if __name__ == "__main__":
    main()
