"""CLI entry point for GCP Transfer Appliance status viewer."""

from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .appliances import get_all_appliances
from .projects import list_org_projects
from .storage import LOOKBACK_DAYS, ProjectStorageResult, get_all_storage

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
    return (f"{PANTHEON_BASE}/appliances/{safe_location}/{safe_appliance_id}/details"
            f";tab=configuration?{query}")


def _project_url(project: str) -> str:
    return f"{PANTHEON_BASE}/appliances?{urlencode({'project': project})}"

# Appliance state colors (keys are compared case-insensitively).
# Real v1alpha1 states seen so far: DRAFT, REQUESTED, PREPARING,
# SHIPPING_TO_CUSTOMER, ON_SITE, PROCESSING, WIPED, CANCELLED.
# States that mean the appliance cycle is complete (no longer active).
INACTIVE_APPLIANCE_STATES = frozenset({"WIPED", "CANCELLED"})

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
    parser.add_argument(
        "--no-storage", action="store_true",
        help="Skip GCS storage queries (faster; omits storage data from output).",
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


def _build_project_summaries(
    all_appliances: list[dict],
    storage_results: dict[str, ProjectStorageResult],
    project_ids: list[str],
) -> dict:
    """Return a per-project summary dict keyed by project ID.

    Status is "active" when at least one appliance is not yet wiped/cancelled
    OR when the project has non-zero current storage.
    """
    by_project: dict[str, list[dict]] = {}
    for a in all_appliances:
        by_project.setdefault(a["project"], []).append(a)

    summaries: dict = {}
    for pid in project_ids:
        project_appliances = by_project.get(pid, [])
        storage = storage_results.get(pid)

        has_active_appliance = any(
            a["state"].upper() not in INACTIVE_APPLIANCE_STATES
            for a in project_appliances
        )
        has_storage = storage is not None and storage.current_bytes > 0
        is_active = has_active_appliance or has_storage

        storage_dict = None
        if storage is not None:
            storage_dict = {
                "current_bytes": storage.current_bytes,
                "high_watermark_bytes": storage.high_watermark_bytes,
                "fill_date": storage.fill_date,
                "empty_date": storage.empty_date,
                "error": storage.error,
            }

        summaries[pid] = {
            "status": "active" if is_active else "inactive",
            "appliance_count": len(project_appliances),
            "appliance_states": sorted({a["state"] for a in project_appliances}),
            "storage": storage_dict,
        }

    return summaries


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


def build_html_report(
    appliances: list[dict],
    project_summaries: dict,
    org_id: str,
    tz_name: str,
) -> str:
    storage_window_start = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).isoformat()
    report_data = {
        "appliances": appliances,
        "projects": project_summaries,
        "storage_window_start": storage_window_start,
        "storage_window_days": LOOKBACK_DAYS,
    }
    report_json = json.dumps(report_data, indent=2).replace("</", "<\\/")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    title = "Transfer Appliance Report"
    heading = f"{title} — org {html.escape(org_id)}"
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
      padding: 18px 20px 40px;
    }}

    .hero {{
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 10px 16px;
      padding: 6px 4px 12px;
    }}

    h1 {{
      margin: 0;
      font-size: 1.6rem;
      line-height: 1.1;
      color: var(--accent-strong);
      font-weight: 600;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }}

    .totals {{
      font: 500 0.95rem/1.3 "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      color: var(--muted);
      margin-left: auto;
    }}

    .totals strong {{
      color: var(--text);
      font-weight: 700;
    }}

    .totals .sep {{
      margin: 0 8px;
      opacity: 0.5;
    }}

    .summary-states {{
      margin-top: 10px;
      grid-template-columns: repeat(10, minmax(0, 1fr));
      gap: 6px;
    }}

    @media (max-width: 900px) {{
      .summary-states {{
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }}
    }}

    .card {{
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 14px;
      padding: 10px 14px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 72px;
    }}

    .card-label {{
      color: var(--muted);
      font: 600 0.7rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      word-break: break-word;
      overflow-wrap: anywhere;
    }}

    .card-value {{
      font-size: 1.4rem;
      line-height: 1;
      align-self: flex-start;
    }}

    .state-card {{
      all: unset;
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 12px;
      padding: 6px 8px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 60px;
      cursor: pointer;
      transition: opacity 0.15s ease, transform 0.05s ease, background 0.15s ease;
      box-sizing: border-box;
    }}

    .state-card:hover {{
      background: rgba(255, 252, 245, 0.95);
    }}

    .state-card:active {{
      transform: scale(0.98);
    }}

    .state-card[aria-pressed="false"] {{
      opacity: 0.6;
      background: rgba(228, 220, 205, 0.55);
      border-color: rgba(87, 63, 34, 0.18);
      box-shadow: none;
    }}

    .state-card .card-label {{
      font-size: 0.58rem;
      letter-spacing: 0.05em;
    }}

    .state-card .card-value {{
      font-size: 1.05rem;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}

    .field {{
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 14px;
      padding: 10px 12px;
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
      margin-top: 14px;
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

    th button[data-active="true"] {{
      color: var(--accent-strong);
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

    [hidden] {{ display: none !important; }}

    .view-tabs {{
      display: flex;
      gap: 6px;
      margin: 14px 0 0;
    }}

    .tab-btn {{
      all: unset;
      cursor: pointer;
      padding: 8px 20px;
      border-radius: 10px;
      font: 600 0.82rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.06em;
      color: var(--muted);
      border: 1px solid transparent;
      transition: background 0.15s, color 0.15s;
      box-sizing: border-box;
    }}

    .tab-btn[aria-selected="true"] {{
      background: var(--panel);
      border-color: var(--panel-border);
      color: var(--accent-strong);
      box-shadow: var(--shadow);
    }}

    .tab-btn:hover:not([aria-selected="true"]) {{
      background: rgba(255, 252, 245, 0.5);
    }}

    .status-filter-bar {{
      display: flex;
      gap: 8px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}

    .status-filter-btn {{
      all: unset;
      cursor: pointer;
      padding: 8px 18px;
      border-radius: 10px;
      font: 600 0.78rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.07em;
      border: 1px solid var(--panel-border);
      background: var(--panel);
      box-shadow: var(--shadow);
      color: var(--muted);
      transition: opacity 0.15s, background 0.15s;
      box-sizing: border-box;
    }}

    .status-filter-btn[aria-pressed="true"] {{
      color: var(--accent-strong);
    }}

    .status-filter-btn[aria-pressed="false"] {{
      opacity: 0.55;
      background: rgba(228, 220, 205, 0.55);
      box-shadow: none;
    }}

    @media (max-width: 820px) {{
      #view-appliances th:nth-child(5),
      #view-appliances th:nth-child(6),
      #view-appliances td:nth-child(5),
      #view-appliances td:nth-child(6) {{
        display: none;
      }}
      #view-projects th:nth-child(4),
      #view-projects th:nth-child(5),
      #view-projects td:nth-child(4),
      #view-projects td:nth-child(5) {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{heading}</h1>
      <div class="totals" id="totals"></div>
    </section>

    <div class="view-tabs" role="tablist">
      <button class="tab-btn" role="tab" aria-selected="true"
              data-view="appliances" id="tab-appliances">Appliances</button>
      <button class="tab-btn" role="tab" aria-selected="false"
              data-view="projects" id="tab-projects">Projects</button>
    </div>

    <!-- Appliances view -->
    <div id="view-appliances" role="tabpanel">
      <section class="summary summary-states" id="summary-states"></section>

      <section class="toolbar">
        <div class="field">
          <label for="search">Search</label>
          <input id="search" type="search" placeholder="Project, appliance ID, model, state">
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
    </div>

    <!-- Projects view -->
    <div id="view-projects" role="tabpanel" hidden>
      <div class="status-filter-bar" id="status-filter-bar">
        <button class="status-filter-btn" data-status="active"
                aria-pressed="true">Active</button>
        <button class="status-filter-btn" data-status="inactive"
                aria-pressed="true">Inactive</button>
      </div>

      <section class="toolbar">
        <div class="field">
          <label for="proj-search">Search</label>
          <input id="proj-search" type="search" placeholder="Project name">
        </div>
      </section>

      <section class="table-wrap">
        <table>
          <thead>
            <tr>
              <th><button data-proj-sort="project">Project</button></th>
              <th><button data-proj-sort="status">Status</button></th>
              <th>Appliances</th>
              <th><button data-proj-sort="current_bytes">Storage Now</button></th>
              <th><button data-proj-sort="high_watermark_bytes">High Watermark</button></th>
              <th><button data-proj-sort="fill_date">Fill Date</button></th>
              <th><button data-proj-sort="empty_date">Empty Date</button></th>
            </tr>
          </thead>
          <tbody id="proj-rows"></tbody>
        </table>
      </section>
    </div>

    <div class="footer" id="footer"></div>
  </main>

  <script id="report-data" type="application/json">{report_json}</script>
  <script>
    const reportData = JSON.parse(document.getElementById("report-data").textContent);
    const appliances = reportData.appliances || [];
    const projectSummaries = reportData.projects || {{}};
    const storageWindowStart = reportData.storage_window_start || null;
    const storageWindowDays = reportData.storage_window_days || 0;

    const rowsEl = document.getElementById("rows");
    const totalsEl = document.getElementById("totals");
    const summaryStatesEl = document.getElementById("summary-states");
    const footerEl = document.getElementById("footer");
    const searchEl = document.getElementById("search");
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

    const STATE_ORDER = [
      "DRAFT",
      "REQUESTED",
      "AWAITING_INVENTORY",
      "PREPARING",
      "SHIPPING_TO_CUSTOMER",
      "ON_SITE",
      "SHIPPING_TO_GOOGLE",
      "PROCESSING",
      "WIPED",
      "CANCELLED",
    ];

    let sortKey = "update_time";
    let sortDir = "desc";

    function escapeHtml(s) {{
      return String(s ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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

    function formatBytes(bytes) {{
      if (bytes === null || bytes === undefined) return "—";
      if (bytes === 0) return "0 B";
      const k = 1024;
      const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
      const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
      return (bytes / Math.pow(k, i)).toFixed(1) + " " + units[i];
    }}

    function compareValues(left, right) {{
      const a = (left ?? "").toString().toLowerCase();
      const b = (right ?? "").toString().toLowerCase();
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    }}

    // ── Appliances view ────────────────────────────────────────────────────

    function countBy(key) {{
      const counts = new Map();
      for (const row of appliances) {{
        counts.set(row[key], (counts.get(row[key]) || 0) + 1);
      }}
      return counts;
    }}

    const projectCounts = countBy("project");
    const projectIds = Array.from(projectCounts.keys()).sort();

    projectFilterEl.options[0].textContent = `All projects (${{appliances.length}})`;
    for (const project of projectIds) {{
      const option = document.createElement("option");
      option.value = project;
      const status = projectSummaries[project]?.status ?? "";
      const statusTag = status ? ` [${{status}}]` : "";
      option.textContent = `${{project}}${{statusTag}}: ${{projectCounts.get(project)}}`;
      projectFilterEl.appendChild(option);
    }}

    const excludedStates = new Set();

    function applyTextFilters(rows) {{
      const query = searchEl.value.trim().toLowerCase();
      const projectFilter = projectFilterEl.value;
      return rows
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
        }});
    }}

    function getFilteredRows() {{
      return applyTextFilters(appliances)
        .filter((row) => !excludedStates.has(row.state))
        .sort((left, right) => {{
          const base = compareValues(left[sortKey], right[sortKey]);
          return sortDir === "asc" ? base : -base;
        }});
    }}

    function renderStateCards(counts) {{
      const ordered = STATE_ORDER.map((state) => [state, counts.get(state) || 0]);
      const extras = Array.from(counts.entries())
        .filter(([state]) => !STATE_ORDER.includes(state))
        .sort((a, b) => compareValues(a[0], b[0]));

      summaryStatesEl.innerHTML = "";
      for (const [state, count] of [...ordered, ...extras]) {{
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "state-card";
        btn.dataset.state = state;
        btn.setAttribute("aria-pressed", excludedStates.has(state) ? "false" : "true");
        btn.title = excludedStates.has(state)
          ? `Click to include ${{state}}`
          : `Click to exclude ${{state}}`;
        btn.innerHTML = `<div class="card-label">${{state.replace(/_/g, " ")}}</div><div class="card-value">${{count}}</div>`;
        btn.addEventListener("click", () => {{
          if (excludedStates.has(state)) {{
            excludedStates.delete(state);
          }} else {{
            excludedStates.add(state);
          }}
          renderRows();
        }});
        summaryStatesEl.appendChild(btn);
      }}
    }}

    function renderSummary(rows) {{
      const baseRows = applyTextFilters(appliances);
      const counts = new Map();
      for (const row of baseRows) {{
        counts.set(row.state, (counts.get(row.state) || 0) + 1);
      }}

      totalsEl.innerHTML =
        `Total Appliances: <strong>${{appliances.length}}</strong>` +
        `<span class="sep">·</span>` +
        `<strong>${{rows.length}}</strong> visible`;

      renderStateCards(counts);
    }}

    function renderRows() {{
      updateSortButtons();
      const rows = getFilteredRows();
      rowsEl.innerHTML = "";

      for (const row of rows) {{
        const tr = document.createElement("tr");
        const stateColor = stateColors[row.state] || "#5f4a33";
        tr.innerHTML = `
          <td class="mono"><a href="${{row.project_url}}" target="_blank" rel="noopener noreferrer">${{row.project}}</a></td>
          <td class="mono"><a href="${{row.appliance_url}}" target="_blank" rel="noopener noreferrer">${{row.appliance_id}}</a></td>
          <td class="mono">${{row.model}}</td>
          <td><span class="state" style="color:${{stateColor}}">${{row.state}}</span></td>
          <td>${{formatTime(row.create_time)}}</td>
          <td>${{formatTime(row.update_time)}}</td>
        `;
        rowsEl.appendChild(tr);
      }}

      renderSummary(rows);
      footerEl.textContent = `${{rows.length}} row(s) shown · Generated {html.escape(generated_at)} ({html.escape(tz_name)})`;
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

    function updateSortButtons() {{
      for (const button of sortButtons) {{
        const active = button.dataset.sort === sortKey;
        button.dataset.active = active ? "true" : "false";
        const suffix = active ? (sortDir === "asc" ? " ↑" : " ↓") : " ↕";
        button.textContent = `${{button.dataset.label}}${{suffix}}`;
      }}
    }}

    searchEl.addEventListener("input", renderRows);
    projectFilterEl.addEventListener("change", renderRows);

    for (const button of sortButtons) {{
      button.dataset.label = button.textContent;
    }}

    // ── Projects view ──────────────────────────────────────────────────────

    const projStatusFilter = new Set(["active", "inactive"]);
    let projSortKey = "project";
    let projSortDir = "asc";

    const projSearchEl = document.getElementById("proj-search");
    const projRowsEl = document.getElementById("proj-rows");
    const projSortButtons = Array.from(document.querySelectorAll("[data-proj-sort]"));

    function projUrl(projectId) {{
      return `https://pantheon.corp.google.com/appliances?project=${{encodeURIComponent(projectId)}}`;
    }}

    function getFilteredProjects() {{
      const q = projSearchEl.value.trim().toLowerCase();
      return Object.entries(projectSummaries)
        .filter(([id, proj]) => {{
          if (!projStatusFilter.has(proj.status)) return false;
          if (q) return id.toLowerCase().includes(q);
          return true;
        }})
        .sort(([idA, a], [idB, b]) => {{
          let av, bv;
          switch (projSortKey) {{
            case "project":             av = idA;   bv = idB;   break;
            case "status":              av = a.status;  bv = b.status;  break;
            case "current_bytes":
              av = a.storage?.current_bytes ?? -1;
              bv = b.storage?.current_bytes ?? -1;
              break;
            case "high_watermark_bytes":
              av = a.storage?.high_watermark_bytes ?? -1;
              bv = b.storage?.high_watermark_bytes ?? -1;
              break;
            case "fill_date":
              av = a.storage?.fill_date ?? "";
              bv = b.storage?.fill_date ?? "";
              break;
            case "empty_date":
              av = a.storage?.empty_date ?? "";
              bv = b.storage?.empty_date ?? "";
              break;
            default: av = idA; bv = idB;
          }}
          const cmp = typeof av === "number"
            ? av - bv
            : String(av ?? "").toLowerCase().localeCompare(String(bv ?? "").toLowerCase());
          return projSortDir === "asc" ? cmp : -cmp;
        }});
    }}

    function renderProjectRows() {{
      updateProjSortButtons();
      const rows = getFilteredProjects();
      projRowsEl.innerHTML = "";

      for (const [id, proj] of rows) {{
        const tr = document.createElement("tr");
        const storage = proj.storage || {{}};
        const isActive = proj.status === "active";
        const statusColor = isActive ? "#0b6e4f" : "#6e6357";

        const statesList = (proj.appliance_states || []).join(", ") || "—";
        const applianceText = proj.appliance_count > 0
          ? `${{proj.appliance_count}} · ${{statesList}}`
          : "0";

        const storErr = storage.error
          ? ` <span title="${{escapeHtml(storage.error)}}" style="cursor:help">⚠</span>`
          : "";
        const storNow = storage.current_bytes != null
          ? formatBytes(storage.current_bytes) + storErr
          : "—";
        const storHWM = storage.high_watermark_bytes
          ? formatBytes(storage.high_watermark_bytes)
          : "—";
        let fillStr;
        if (storage.fill_date) {{
          fillStr = formatTime(storage.fill_date);
        }} else if ((storage.high_watermark_bytes || 0) > 0 || (storage.current_bytes || 0) > 0) {{
          // Data was already present at the start of the monitoring window —
          // we can't tell exactly when it rose, so anchor to the lookback edge.
          const tip = `Storage was already non-zero ${{storageWindowDays}} days ago; ` +
                      `actual fill date is unknown.`;
          const anchor = storageWindowStart ? formatTime(storageWindowStart) : "the lookback window";
          fillStr = `<em title="${{escapeHtml(tip)}}" style="color:var(--muted);cursor:help">before ${{anchor}}</em>`;
        }} else {{
          fillStr = "—";
        }}
        let emptyStr;
        if (storage.empty_date) {{
          emptyStr = formatTime(storage.empty_date);
        }} else if (storage.current_bytes > 0) {{
          emptyStr = `<em style="color:var(--accent)">still active</em>`;
        }} else {{
          emptyStr = "—";
        }}

        tr.innerHTML = `
          <td class="mono"><a href="${{projUrl(id)}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(id)}}</a></td>
          <td><span class="state" style="color:${{statusColor}}">${{proj.status}}</span></td>
          <td class="mono" style="font-size:0.85rem">${{applianceText}}</td>
          <td class="mono">${{storNow}}</td>
          <td class="mono">${{storHWM}}</td>
          <td>${{fillStr}}</td>
          <td>${{emptyStr}}</td>
        `;
        projRowsEl.appendChild(tr);
      }}

      // Update active/inactive filter button labels
      const allProjIds = Object.keys(projectSummaries);
      const activeCnt   = allProjIds.filter(i => projectSummaries[i].status === "active").length;
      const inactiveCnt = allProjIds.length - activeCnt;
      document.querySelector('[data-status="active"]').textContent   = `Active (${{activeCnt}})`;
      document.querySelector('[data-status="inactive"]').textContent = `Inactive (${{inactiveCnt}})`;

      // Update totals bar
      totalsEl.innerHTML =
        `Total Projects: <strong>${{allProjIds.length}}</strong>` +
        `<span class="sep">·</span>` +
        `<strong>${{rows.length}}</strong> visible` +
        `<span class="sep">·</span>` +
        `<strong>${{activeCnt}}</strong> active, <strong>${{inactiveCnt}}</strong> inactive`;

      footerEl.textContent = `${{rows.length}} project(s) shown · Generated {html.escape(generated_at)} ({html.escape(tz_name)})`;
    }}

    function updateProjSortButtons() {{
      for (const btn of projSortButtons) {{
        const active = btn.dataset.projSort === projSortKey;
        btn.dataset.active = active ? "true" : "false";
        const suffix = active ? (projSortDir === "asc" ? " ↑" : " ↓") : " ↕";
        btn.textContent = (btn.dataset.projLabel ?? btn.textContent.replace(/ [↑↓↕]$/, "")) + suffix;
        if (!btn.dataset.projLabel) btn.dataset.projLabel = btn.textContent.replace(/ [↑↓↕]$/, "");
      }}
    }}

    for (const btn of projSortButtons) {{
      btn.addEventListener("click", () => {{
        const nextKey = btn.dataset.projSort;
        if (projSortKey === nextKey) {{
          projSortDir = projSortDir === "asc" ? "desc" : "asc";
        }} else {{
          projSortKey = nextKey;
          projSortDir = "asc";
        }}
        renderProjectRows();
      }});
    }}

    for (const btn of document.querySelectorAll(".status-filter-btn")) {{
      btn.addEventListener("click", () => {{
        const status = btn.dataset.status;
        if (projStatusFilter.has(status)) {{
          if (projStatusFilter.size > 1) {{
            projStatusFilter.delete(status);
            btn.setAttribute("aria-pressed", "false");
          }}
        }} else {{
          projStatusFilter.add(status);
          btn.setAttribute("aria-pressed", "true");
        }}
        renderProjectRows();
      }});
    }}

    projSearchEl.addEventListener("input", renderProjectRows);

    // ── Tab switching ──────────────────────────────────────────────────────

    const viewAppliances = document.getElementById("view-appliances");
    const viewProjects   = document.getElementById("view-projects");
    const tabBtns = Array.from(document.querySelectorAll(".tab-btn"));

    function switchTab(view) {{
      for (const btn of tabBtns) {{
        btn.setAttribute("aria-selected", btn.dataset.view === view ? "true" : "false");
      }}
      if (view === "appliances") {{
        viewAppliances.removeAttribute("hidden");
        viewProjects.setAttribute("hidden", "");
        renderRows();
      }} else {{
        viewAppliances.setAttribute("hidden", "");
        viewProjects.removeAttribute("hidden");
        renderProjectRows();
      }}
    }}

    for (const btn of tabBtns) {{
      btn.addEventListener("click", () => switchTab(btn.dataset.view));
    }}

    // ── Initial render ─────────────────────────────────────────────────────

    updateSortButtons();
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


def render_html(appliances: list[dict], project_summaries: dict,
                org_id: str, tz_name: str, html_file: Optional[str]) -> None:
    document = build_html_report(appliances, project_summaries, org_id, tz_name)
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

    # Fetch GCS storage usage (unless --no-storage)
    if args.no_storage:
        storage_results: dict = {}
    else:
        _log("Querying GCS storage usage...")
        try:
            storage_results = get_all_storage(project_ids, max_workers=args.workers)
        except Exception as e:
            _log(f"Warning: storage query failed: {type(e).__name__}: {e}")
            storage_results = {}

    # Build per-project summaries (using unfiltered appliances for status).
    project_summaries = _build_project_summaries(
        scan_results.appliances, storage_results, project_ids
    )

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
        output = {"appliances": appliances, "projects": project_summaries}
        print(json.dumps(output, indent=2))
    elif args.output_format == "csv":
        render_csv(appliances)
    elif args.output_format == "html":
        render_html(appliances, project_summaries, args.org_id, args.timezone,
                    args.html_file)
    else:
        render_table(appliances, tz)

    if scan_results.errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
