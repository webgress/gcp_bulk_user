"""CLI entry point for GCP Transfer Appliance status viewer."""

from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
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
        "--format", choices=["table", "json", "csv", "html"], default="table",
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
    parser.add_argument(
        "--html-file",
        help="Write HTML output to this file. If omitted for interactive HTML "
             "output, writes to /tmp/report_<timestamp>.html and opens it.",
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


def _attach_links(appliances: list[dict]) -> list[dict]:
    enriched = []
    for appliance in appliances:
        row = dict(appliance)
        project = str(row["project"])
        row["project_url"] = _project_url(project)
        row["appliance_url"] = _appliance_url(
            project,
            str(row.get("location", "")),
            str(row["appliance_id"]),
        )
        enriched.append(row)
    return enriched


def render_table(appliances: list[dict], tz: ZoneInfo) -> None:
    # Rich may see wrapped terminals / app consoles as non-interactive and
    # suppress OSC 8 hyperlinks. Force terminal rendering plus a concrete color
    # system so Pantheon deep links remain clickable in supported terminals.
    console = Console(force_terminal=True, color_system="standard")
    table = Table(title="Transfer Appliance Status", show_lines=True)
    table.add_column("Project", style="bold")
    table.add_column("Appliance ID")
    table.add_column("Model")
    table.add_column("State")
    table.add_column("Created")
    table.add_column("Updated")

    for a in appliances:
        state = str(a["state"])
        color = STATE_COLORS.get(state.upper(), "white")
        project = str(a["project"])
        appliance_id = str(a["appliance_id"])
        proj_link = str(a.get("project_url", _project_url(project)))
        app_link = str(a.get(
            "appliance_url",
            _appliance_url(project, str(a.get("location", "")), appliance_id),
        ))
        project_text = Text(project, style="bold")
        project_text.stylize(f"link {proj_link}")
        appliance_text = Text(appliance_id)
        appliance_text.stylize(f"link {app_link}")
        state_text = Text(state, style=color)
        table.add_row(
            project_text,
            appliance_text,
            str(a["model"]),
            state_text,
            _format_ts(a["create_time"], tz),
            _format_ts(a["update_time"], tz),
        )

    console.print(table)


def render_csv(appliances: list[dict]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "project",
        "project_url",
        "appliance_id",
        "appliance_url",
        "model",
        "state",
        "create_time",
        "update_time",
    ])
    for a in appliances:
        writer.writerow([
            _safe_csv_cell(a["project"]),
            _safe_csv_cell(a["project_url"]),
            _safe_csv_cell(a["appliance_id"]),
            _safe_csv_cell(a["appliance_url"]),
            _safe_csv_cell(a["model"]),
            _safe_csv_cell(a["state"]),
            _safe_csv_cell(a["create_time"]),
            _safe_csv_cell(a["update_time"]),
        ])


def build_html_report(appliances: list[dict], org_id: str, tz_name: str) -> str:
    report_json = json.dumps(appliances, indent=2).replace("</", "<\\/")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    title = f"Transfer Appliance Status - org {org_id}"
    state_options = "".join(
        f'<option value="{html.escape(state)}">{html.escape(state)}</option>'
        for state in sorted({str(a["state"]) for a in appliances}, key=str.upper)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --panel: rgba(255, 253, 249, 0.92);
      --panel-border: rgba(87, 63, 34, 0.12);
      --text: #1f1a14;
      --muted: #6e6357;
      --accent: #0b6e4f;
      --accent-strong: #0a5a42;
      --chip: #efe6d7;
      --shadow: 0 24px 70px rgba(56, 41, 19, 0.14);
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(208, 164, 92, 0.22), transparent 28%),
        radial-gradient(circle at top right, rgba(11, 110, 79, 0.12), transparent 32%),
        linear-gradient(180deg, #f8f3ea 0%, #efe4d2 100%);
      min-height: 100vh;
    }}

    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}

    .hero {{
      background: linear-gradient(135deg, rgba(255,255,255,0.72), rgba(255,248,238,0.9));
      border: 1px solid var(--panel-border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 28px;
      backdrop-filter: blur(10px);
    }}

    .eyebrow {{
      font: 600 0.8rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-strong);
      margin-bottom: 12px;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 3.3rem);
      line-height: 0.98;
      max-width: 10ch;
    }}

    .subhead {{
      margin: 14px 0 0;
      color: var(--muted);
      max-width: 72ch;
      font-size: 1rem;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}

    .meta span {{
      background: var(--chip);
      border-radius: 999px;
      padding: 8px 12px;
      font: 500 0.86rem/1.1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      color: #5f4a33;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}

    .card {{
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 20px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}

    .card-label {{
      color: var(--muted);
      font: 600 0.76rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .card-value {{
      margin-top: 10px;
      font-size: 2rem;
      line-height: 1;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 22px;
    }}

    .field {{
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 18px;
      padding: 14px;
      box-shadow: var(--shadow);
    }}

    .field label {{
      display: block;
      font: 600 0.76rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}

    .field input,
    .field select {{
      width: 100%;
      border: 1px solid rgba(87, 63, 34, 0.16);
      border-radius: 12px;
      padding: 10px 12px;
      font: inherit;
      background: #fffdfa;
      color: var(--text);
    }}

    .table-wrap {{
      margin-top: 22px;
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
    }}

    thead {{
      background: rgba(228, 214, 191, 0.55);
    }}

    th, td {{
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid rgba(87, 63, 34, 0.09);
      vertical-align: top;
    }}

    th button {{
      all: unset;
      cursor: pointer;
      font: 600 0.78rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}

    tbody tr:hover {{
      background: rgba(255, 252, 245, 0.9);
    }}

    a {{
      color: var(--accent-strong);
      text-decoration: none;
    }}

    a:hover {{
      text-decoration: underline;
    }}

    .mono {{
      font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
      font-size: 0.92rem;
    }}

    .state {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font: 600 0.8rem/1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      background: #ede5d8;
      color: #5f4a33;
    }}

    .footer {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.92rem;
    }}

    @media (max-width: 820px) {{
      th:nth-child(5),
      th:nth-child(6),
      td:nth-child(5),
      td:nth-child(6) {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="eyebrow">Transfer Appliance Report</div>
      <h1>{html.escape(title)}</h1>
      <p class="subhead">
        Interactive Pantheon-linked inventory view generated by <code>gcp_appliance_status</code>.
        Filter, sort, and open project or appliance detail pages directly from the report.
      </p>
      <div class="meta">
        <span>Generated {html.escape(generated_at)}</span>
        <span>Timezone {html.escape(tz_name)}</span>
        <span>{len(appliances)} appliance(s)</span>
      </div>
    </section>

    <section class="summary" id="summary"></section>

    <section class="toolbar">
      <div class="field">
        <label for="search">Search</label>
        <input id="search" type="search" placeholder="Project, appliance ID, model, state">
      </div>
      <div class="field">
        <label for="state-filter">State</label>
        <select id="state-filter">
          <option value="">All states</option>
          {state_options}
        </select>
      </div>
      <div class="field">
        <label for="project-filter">Project</label>
        <select id="project-filter">
          <option value="">All projects</option>
        </select>
      </div>
    </section>

    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th><button data-sort="project">Project</button></th>
            <th><button data-sort="appliance_id">Appliance ID</button></th>
            <th><button data-sort="model">Model</button></th>
            <th><button data-sort="state">State</button></th>
            <th><button data-sort="create_time">Created</button></th>
            <th><button data-sort="update_time">Updated</button></th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </section>

    <div class="footer" id="footer"></div>
  </main>

  <script id="report-data" type="application/json">{report_json}</script>
  <script>
    const appliances = JSON.parse(document.getElementById("report-data").textContent);
    const rowsEl = document.getElementById("rows");
    const summaryEl = document.getElementById("summary");
    const footerEl = document.getElementById("footer");
    const searchEl = document.getElementById("search");
    const stateFilterEl = document.getElementById("state-filter");
    const projectFilterEl = document.getElementById("project-filter");
    const sortButtons = Array.from(document.querySelectorAll("[data-sort]"));

    const stateColors = {{
      DRAFT: "#76624a",
      REQUESTED: "#946200",
      PREPARING: "#9b5f00",
      SHIPPING_TO_CUSTOMER: "#006f8a",
      ON_SITE: "#0b6e4f",
      PROCESSING: "#7b3fb0",
      WIPED: "#1f5fbf",
      CANCELLED: "#ad2831",
    }};

    let sortKey = "project";
    let sortDir = "asc";

    const projects = Array.from(new Set(appliances.map((row) => row.project))).sort();
    for (const project of projects) {{
      const option = document.createElement("option");
      option.value = project;
      option.textContent = project;
      projectFilterEl.appendChild(option);
    }}

    function formatTime(value) {{
      if (!value || value === "N/A") return value || "N/A";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat(undefined, {{
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: {json.dumps(tz_name)},
      }}).format(date);
    }}

    function compareValues(left, right) {{
      const a = (left ?? "").toString().toLowerCase();
      const b = (right ?? "").toString().toLowerCase();
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    }}

    function getFilteredRows() {{
      const query = searchEl.value.trim().toLowerCase();
      const stateFilter = stateFilterEl.value;
      const projectFilter = projectFilterEl.value;

      return appliances
        .filter((row) => !stateFilter || row.state === stateFilter)
        .filter((row) => !projectFilter || row.project === projectFilter)
        .filter((row) => {{
          if (!query) return true;
          return [
            row.project,
            row.appliance_id,
            row.model,
            row.state,
            row.create_time,
            row.update_time,
          ].some((value) => (value || "").toString().toLowerCase().includes(query));
        }})
        .sort((left, right) => {{
          const base = compareValues(left[sortKey], right[sortKey]);
          return sortDir === "asc" ? base : -base;
        }});
    }}

    function renderSummary(rows) {{
      const counts = new Map();
      for (const row of rows) {{
        counts.set(row.state, (counts.get(row.state) || 0) + 1);
      }}

      const cards = [
        {{ label: "Visible rows", value: rows.length.toString() }},
        ...Array.from(counts.entries())
          .sort((a, b) => compareValues(a[0], b[0]))
          .map(([state, count]) => ({{ label: state, value: count.toString() }})),
      ];

      summaryEl.innerHTML = "";
      for (const card of cards) {{
        const el = document.createElement("article");
        el.className = "card";
        el.innerHTML = `<div class="card-label">${{card.label}}</div><div class="card-value">${{card.value}}</div>`;
        summaryEl.appendChild(el);
      }}
    }}

    function renderRows() {{
      const rows = getFilteredRows();
      rowsEl.innerHTML = "";

      for (const row of rows) {{
        const tr = document.createElement("tr");
        const stateColor = stateColors[row.state] || "#5f4a33";
        tr.innerHTML = `
          <td class="mono"><a href="${{row.project_url}}">${{row.project}}</a></td>
          <td class="mono"><a href="${{row.appliance_url}}">${{row.appliance_id}}</a></td>
          <td class="mono">${{row.model}}</td>
          <td><span class="state" style="color:${{stateColor}}">${{row.state}}</span></td>
          <td>${{formatTime(row.create_time)}}</td>
          <td>${{formatTime(row.update_time)}}</td>
        `;
        rowsEl.appendChild(tr);
      }}

      renderSummary(rows);
      footerEl.textContent = `${{rows.length}} row(s) shown. Click Project or Appliance ID to open Pantheon.`;
    }}

    for (const button of sortButtons) {{
      button.addEventListener("click", () => {{
        const nextKey = button.dataset.sort;
        if (sortKey === nextKey) {{
          sortDir = sortDir === "asc" ? "desc" : "asc";
        }} else {{
          sortKey = nextKey;
          sortDir = "asc";
        }}
        renderRows();
      }});
    }}

    searchEl.addEventListener("input", renderRows);
    stateFilterEl.addEventListener("change", renderRows);
    projectFilterEl.addEventListener("change", renderRows);

    renderRows();
  </script>
</body>
</html>
"""


def _default_html_report_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("/tmp") / f"report_{timestamp}.html"


def _write_html_report(path: Path, document: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def render_html(appliances: list[dict], org_id: str, tz_name: str,
                html_file: Optional[str]) -> None:
    document = build_html_report(appliances, org_id, tz_name)
    if html_file:
        path = Path(html_file).expanduser()
        _write_html_report(path, document)
        _log(f"Wrote HTML report to {path}")
        return

    if sys.stdout.isatty():
        path = _default_html_report_path()
        _write_html_report(path, document)
        _log(f"Wrote HTML report to {path}")
        result = subprocess.run(["open", str(path)], check=False)
        if result.returncode != 0:
            _log(f"Failed to open HTML report automatically (rc={result.returncode}).")
        return

    print(document)


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

    appliances = _attach_links(appliances)

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
    elif args.output_format == "html":
        render_html(appliances, args.org_id, args.timezone, args.html_file)
    else:
        render_table(appliances, tz)

    if scan_results.errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
