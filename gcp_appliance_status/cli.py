"""CLI entry point for GCP Transfer Appliance status viewer."""

import argparse
import json
import sys

from .appliances import get_all_appliances
from .projects import list_org_projects


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


def render_table(appliances: list[dict]) -> None:
    headers = ["Project", "Appliance ID", "Type", "State", "Created", "Updated"]
    rows = [
        [a["project"], a["appliance_id"], a["type"], a["state"],
         a["create_time"], a["update_time"]]
        for a in appliances
    ]
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    sep = "  "
    print(sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print(sep.join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print(sep.join(str(r[i]).ljust(widths[i]) for i in range(len(headers))))


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
        render_table(appliances)


if __name__ == "__main__":
    main()
