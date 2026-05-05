package main

// htmlReportTemplate is a near-verbatim port of build_html_report() in
// python-gcloud's cli.py. Five tokens are substituted at render time:
//   __HEADING__         pre-escaped heading text (already HTML-safe)
//   __REPORT_JSON__     JSON array of appliance records (with </ -> <\/ guard)
//   __TZ_NAME_JSON__    JSON-quoted timezone name for the JS timeZone option
//   __GENERATED_AT__    HTML-escaped generation timestamp
//   __TZ_NAME__         HTML-escaped timezone name for the footer text
const htmlReportTemplate = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Transfer Appliance Report</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --panel: rgba(255, 253, 249, 0.92);
      --panel-border: rgba(87, 63, 34, 0.12);
      --text: #1f1a14;
      --muted: #6e6357;
      --accent: #0b6e4f;
      --accent-strong: #0a5a42;
      --chip: #efe6d7;
      --shadow: 0 24px 70px rgba(56, 41, 19, 0.14);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(208, 164, 92, 0.22), transparent 28%),
        radial-gradient(circle at top right, rgba(11, 110, 79, 0.12), transparent 32%),
        linear-gradient(180deg, #f8f3ea 0%, #efe4d2 100%);
      min-height: 100vh;
    }

    main {
      max-width: 1280px;
      margin: 0 auto;
      padding: 18px 20px 40px;
    }

    .hero {
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 10px 16px;
      padding: 6px 4px 12px;
    }

    h1 {
      margin: 0;
      font-size: 1.6rem;
      line-height: 1.1;
      color: var(--accent-strong);
      font-weight: 600;
    }

    .summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }

    .totals {
      font: 500 0.95rem/1.3 "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      color: var(--muted);
      margin-left: auto;
    }

    .totals strong {
      color: var(--text);
      font-weight: 700;
    }

    .totals .sep {
      margin: 0 8px;
      opacity: 0.5;
    }

    .summary-states {
      margin-top: 10px;
      grid-template-columns: repeat(10, minmax(0, 1fr));
      gap: 6px;
    }

    @media (max-width: 900px) {
      .summary-states {
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 14px;
      padding: 10px 14px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 72px;
    }

    .card-label {
      color: var(--muted);
      font: 600 0.7rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      word-break: break-word;
      overflow-wrap: anywhere;
    }

    .card-value {
      font-size: 1.4rem;
      line-height: 1;
      align-self: flex-start;
    }

    .state-card {
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
    }

    .state-card:hover {
      background: rgba(255, 252, 245, 0.95);
    }

    .state-card:active {
      transform: scale(0.98);
    }

    .state-card[aria-pressed="false"] {
      opacity: 0.6;
      background: rgba(228, 220, 205, 0.55);
      border-color: rgba(87, 63, 34, 0.18);
      box-shadow: none;
    }

    .state-card .card-label {
      font-size: 0.58rem;
      letter-spacing: 0.05em;
    }

    .state-card .card-value {
      font-size: 1.05rem;
    }

    .toolbar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 14px;
    }

    .field {
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 14px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
    }

    .field label {
      display: block;
      font: 600 0.76rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }

    .field input,
    .field select {
      width: 100%;
      border: 1px solid rgba(87, 63, 34, 0.16);
      border-radius: 12px;
      padding: 10px 12px;
      font: inherit;
      background: #fffdfa;
      color: var(--text);
    }

    .table-wrap {
      margin-top: 14px;
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    table {
      width: 100%;
      border-collapse: collapse;
    }

    thead {
      background: rgba(228, 214, 191, 0.55);
    }

    th, td {
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid rgba(87, 63, 34, 0.09);
      vertical-align: top;
    }

    th button {
      all: unset;
      cursor: pointer;
      font: 600 0.78rem/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }

    th button[data-active="true"] {
      color: var(--accent-strong);
    }

    tbody tr:hover {
      background: rgba(255, 252, 245, 0.9);
    }

    a {
      color: var(--accent-strong);
      text-decoration: none;
    }

    a:hover {
      text-decoration: underline;
    }

    .mono {
      font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
      font-size: 0.92rem;
    }

    .state {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font: 600 0.8rem/1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      background: #ede5d8;
      color: #5f4a33;
    }

    .footer {
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.92rem;
    }

    @media (max-width: 820px) {
      th:nth-child(5),
      th:nth-child(6),
      td:nth-child(5),
      td:nth-child(6) {
        display: none;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>__HEADING__</h1>
      <div class="totals" id="totals"></div>
    </section>

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

    <div class="footer" id="footer"></div>
  </main>

  <script id="report-data" type="application/json">__REPORT_JSON__</script>
  <script>
    const appliances = JSON.parse(document.getElementById("report-data").textContent);
    const rowsEl = document.getElementById("rows");
    const totalsEl = document.getElementById("totals");
    const summaryStatesEl = document.getElementById("summary-states");
    const footerEl = document.getElementById("footer");
    const searchEl = document.getElementById("search");
    const projectFilterEl = document.getElementById("project-filter");
    const sortButtons = Array.from(document.querySelectorAll("[data-sort]"));

    const stateColors = {
      DRAFT: "#76624a",
      REQUESTED: "#946200",
      PREPARING: "#9b5f00",
      SHIPPING_TO_CUSTOMER: "#006f8a",
      ON_SITE: "#0b6e4f",
      PROCESSING: "#7b3fb0",
      WIPED: "#1f5fbf",
      CANCELLED: "#ad2831",
    };

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

    function countBy(key) {
      const counts = new Map();
      for (const row of appliances) {
        counts.set(row[key], (counts.get(row[key]) || 0) + 1);
      }
      return counts;
    }

    const projectCounts = countBy("project");

    const projects = Array.from(projectCounts.keys()).sort();
    projectFilterEl.options[0].textContent = ` + "`All projects (${appliances.length})`" + `;
    for (const project of projects) {
      const option = document.createElement("option");
      option.value = project;
      option.textContent = ` + "`${project}: ${projectCounts.get(project)}`" + `;
      projectFilterEl.appendChild(option);
    }

    const excludedStates = new Set();

    function formatTime(value) {
      if (!value || value === "N/A") return value || "N/A";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: __TZ_NAME_JSON__,
      }).format(date);
    }

    function compareValues(left, right) {
      const a = (left ?? "").toString().toLowerCase();
      const b = (right ?? "").toString().toLowerCase();
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    }

    function applyTextFilters(rows) {
      const query = searchEl.value.trim().toLowerCase();
      const projectFilter = projectFilterEl.value;
      return rows
        .filter((row) => !projectFilter || row.project === projectFilter)
        .filter((row) => {
          if (!query) return true;
          return [
            row.project,
            row.appliance_id,
            row.model,
            row.state,
            row.create_time,
            row.update_time,
          ].some((value) => (value || "").toString().toLowerCase().includes(query));
        });
    }

    function getFilteredRows() {
      return applyTextFilters(appliances)
        .filter((row) => !excludedStates.has(row.state))
        .sort((left, right) => {
          const base = compareValues(left[sortKey], right[sortKey]);
          return sortDir === "asc" ? base : -base;
        });
    }

    function renderStateCards(counts) {
      const ordered = STATE_ORDER.map((state) => [state, counts.get(state) || 0]);
      const extras = Array.from(counts.entries())
        .filter(([state]) => !STATE_ORDER.includes(state))
        .sort((a, b) => compareValues(a[0], b[0]));

      summaryStatesEl.innerHTML = "";
      for (const [state, count] of [...ordered, ...extras]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "state-card";
        btn.dataset.state = state;
        btn.setAttribute("aria-pressed", excludedStates.has(state) ? "false" : "true");
        btn.title = excludedStates.has(state)
          ? ` + "`Click to include ${state}`" + `
          : ` + "`Click to exclude ${state}`" + `;
        btn.innerHTML = ` + "`<div class=\"card-label\">${state.replace(/_/g, \" \")}</div><div class=\"card-value\">${count}</div>`" + `;
        btn.addEventListener("click", () => {
          if (excludedStates.has(state)) {
            excludedStates.delete(state);
          } else {
            excludedStates.add(state);
          }
          renderRows();
        });
        summaryStatesEl.appendChild(btn);
      }
    }

    function renderSummary(rows) {
      const baseRows = applyTextFilters(appliances);
      const counts = new Map();
      for (const row of baseRows) {
        counts.set(row.state, (counts.get(row.state) || 0) + 1);
      }

      totalsEl.innerHTML =
        ` + "`Total Appliances: <strong>${appliances.length}</strong>`" + ` +
        ` + "`<span class=\"sep\">·</span>`" + ` +
        ` + "`<strong>${rows.length}</strong> visible`" + `;

      renderStateCards(counts);
    }

    function renderRows() {
      updateSortButtons();
      const rows = getFilteredRows();
      rowsEl.innerHTML = "";

      for (const row of rows) {
        const tr = document.createElement("tr");
        const stateColor = stateColors[row.state] || "#5f4a33";
        tr.innerHTML = ` + "`\n          <td class=\"mono\"><a href=\"${row.project_url}\" target=\"_blank\" rel=\"noopener noreferrer\">${row.project}</a></td>\n          <td class=\"mono\"><a href=\"${row.appliance_url}\" target=\"_blank\" rel=\"noopener noreferrer\">${row.appliance_id}</a></td>\n          <td class=\"mono\">${row.model}</td>\n          <td><span class=\"state\" style=\"color:${stateColor}\">${row.state}</span></td>\n          <td>${formatTime(row.create_time)}</td>\n          <td>${formatTime(row.update_time)}</td>\n        `" + `;
        rowsEl.appendChild(tr);
      }

      renderSummary(rows);
      footerEl.textContent = ` + "`${rows.length} row(s) shown · Generated __GENERATED_AT__ (__TZ_NAME__)`" + `;
    }

    for (const button of sortButtons) {
      button.addEventListener("click", () => {
        const nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortKey = nextKey;
          sortDir = "asc";
        }
        renderRows();
      });
    }

    function updateSortButtons() {
      for (const button of sortButtons) {
        const active = button.dataset.sort === sortKey;
        button.dataset.active = active ? "true" : "false";
        const suffix = active ? (sortDir === "asc" ? " ↑" : " ↓") : " ↕";
        button.textContent = ` + "`${button.dataset.label}${suffix}`" + `;
      }
    }

    searchEl.addEventListener("input", renderRows);
    projectFilterEl.addEventListener("change", renderRows);

    for (const button of sortButtons) {
      button.dataset.label = button.textContent;
    }

    updateSortButtons();
    renderRows();
  </script>
</body>
</html>
`
