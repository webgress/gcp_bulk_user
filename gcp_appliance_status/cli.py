"""CLI entry point for GCP Transfer Appliance status viewer."""

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

from .appliances import get_all_appliances
from .projects import list_org_projects

STATE_COLORS = {
    "ACTIVE": "green",
    "PREPARING": "yellow",
    "SHIPPING": "cyan",
    "IN_TRANSIT": "cyan",
    "DELIVERED": "blue",
    "DATA_UPLOAD": "magenta",
    "PROCESSING": "magenta",
    "COMPLETED": "green",
    "CANCELLED": "red",
    "FAILED": "red",
    "UNKNOWN": "dim",
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
    return parser


def render_table(appliances: list[dict], console: Console) -> None:
    table = Table(title="Transfer Appliance Status", show_lines=True)
    table.add_column("Project", style="bold")
    table.add_column("Order ID")
    table.add_column("Type")
    table.add_column("State")
    table.add_column("Created")
    table.add_column("Updated")

    for a in appliances:
        state = a["state"]
        color = STATE_COLORS.get(state, "white")
        table.add_row(
            a["project"],
            a["order_id"],
            a["type"],
            f"[{color}]{state}[/{color}]",
            a["create_time"],
            a["update_time"],
        )

    console.print(table)


def render_csv(appliances: list[dict]) -> None:
    print("project,order_id,type,state,create_time,update_time")
    for a in appliances:
        print(f'{a["project"]},{a["order_id"]},{a["type"]},{a["state"]},'
              f'{a["create_time"]},{a["update_time"]}')


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    console = Console(stderr=True)

    # Discover projects
    if args.projects:
        project_ids = args.projects
        console.print(f"Using {len(project_ids)} specified project(s).")
    else:
        console.print(f"Discovering projects in org {args.org_id}...")
        projects = list_org_projects(args.org_id)
        if not projects:
            console.print("[red]No projects found in organization.[/red]")
            sys.exit(1)
        project_ids = [p["project_id"] for p in projects]
        console.print(f"Found {len(project_ids)} project(s).")

    # Fetch appliance statuses
    console.print("Querying Transfer Appliance status...")
    appliances = get_all_appliances(project_ids, max_workers=args.workers)

    # Apply state filter
    if args.state_filter:
        filter_states = {s.upper() for s in args.state_filter}
        appliances = [a for a in appliances if a["state"] in filter_states]

    if not appliances:
        console.print("[yellow]No Transfer Appliances found across scanned projects.[/yellow]")
        sys.exit(0)

    console.print(f"Found {len(appliances)} appliance(s).\n")

    # Output
    if args.output_format == "json":
        print(json.dumps(appliances, indent=2))
    elif args.output_format == "csv":
        render_csv(appliances)
    else:
        output_console = Console()
        render_table(appliances, output_console)


if __name__ == "__main__":
    main()
