from __future__ import annotations


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Novel Pipeline Monitor</title>
  <style>
    :root {
      --bg: #f5f2eb;
      --surface: #fffdf8;
      --surface-2: #ebe7dc;
      --ink: #1b1f23;
      --muted: #626b73;
      --line: #d8d0c2;
      --line-strong: #b8ad9c;
      --green: #237a57;
      --green-soft: #dbeee4;
      --red: #b54242;
      --red-soft: #f4dfdd;
      --amber: #9b6418;
      --amber-soft: #f2e5cb;
      --blue: #2f638f;
      --blue-soft: #dbe8f2;
      --violet: #6d5a8d;
      --shadow: 0 18px 48px rgba(38, 34, 28, 0.12);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      background:
        linear-gradient(135deg, rgba(35, 122, 87, 0.08), transparent 36%),
        linear-gradient(315deg, rgba(181, 66, 66, 0.08), transparent 30%),
        var(--bg);
    }
    button, input, select { font: inherit; }
    .shell {
      display: grid;
      grid-template-columns: minmax(320px, 390px) minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
      border-right: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.88);
      backdrop-filter: blur(16px);
    }
    .brand {
      padding: 20px;
      border-bottom: 1px solid var(--line);
    }
    .brand-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .brand h1 {
      margin: 0;
      font-size: 21px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.58);
      font-size: 12px;
      white-space: nowrap;
    }
    .filters {
      display: grid;
      gap: 10px;
    }
    .search-row {
      display: grid;
      grid-template-columns: 1fr 116px;
      gap: 8px;
    }
    .control {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--ink);
      padding: 8px 10px;
      outline: none;
    }
    .control:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(47, 99, 143, 0.14);
    }
    .segments {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 6px;
    }
    .segment {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 253, 248, 0.72);
      color: var(--muted);
      cursor: pointer;
    }
    .segment.active {
      border-color: var(--ink);
      background: var(--ink);
      color: var(--surface);
    }
    .run-list {
      display: grid;
      gap: 8px;
      padding: 14px;
      overflow: auto;
    }
    .run-card {
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 253, 248, 0.82);
      padding: 12px;
      cursor: pointer;
    }
    .run-card:hover, .run-card.active {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(47, 99, 143, 0.10);
    }
    .run-card.failed { border-left: 5px solid var(--red); }
    .run-card.completed { border-left: 5px solid var(--green); }
    .run-card.running { border-left: 5px solid var(--blue); }
    .run-card.stale { border-left: 5px solid var(--amber); }
    .run-topline {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .run-name {
      font-size: 13px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .run-meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .bar {
      height: 8px;
      border-radius: 999px;
      overflow: hidden;
      background: var(--surface-2);
      border: 1px solid var(--line);
    }
    .bar-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--blue), var(--green));
      transition: width 0.2s ease;
    }
    .main {
      min-width: 0;
      padding: 22px;
      overflow: auto;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 16px;
    }
    .title-block h2 {
      margin: 0 0 6px;
      font-size: 26px;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .subtle {
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 38px;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--ink);
      cursor: pointer;
      padding: 8px 12px;
    }
    .button.primary {
      border-color: var(--ink);
      background: var(--ink);
      color: var(--surface);
    }
    .button:hover { border-color: var(--blue); }
    .overview {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 253, 248, 0.78);
      padding: 12px;
      box-shadow: 0 8px 22px rgba(38, 34, 28, 0.06);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }
    .metric-value {
      font-size: 24px;
      font-weight: 800;
      line-height: 1;
    }
    .metric.good .metric-value { color: var(--green); }
    .metric.bad .metric-value { color: var(--red); }
    .metric.warn .metric-value { color: var(--amber); }
    .detail-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
      gap: 14px;
      align-items: start;
    }
    .panel {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 253, 248, 0.88);
      box-shadow: var(--shadow);
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .panel-body { padding: 14px; }
    .status-line {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 14px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .badge.completed { color: var(--green); background: var(--green-soft); border-color: #a9d2bd; }
    .badge.failed { color: var(--red); background: var(--red-soft); border-color: #e2b7b2; }
    .badge.running { color: var(--blue); background: var(--blue-soft); border-color: #abc8df; }
    .badge.stale { color: var(--amber); background: var(--amber-soft); border-color: #ddc596; }
    .kv {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 9px 12px;
      color: var(--muted);
      font-size: 13px;
    }
    .kv strong {
      color: var(--ink);
      font-weight: 700;
    }
    .mono {
      font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .tabs {
      display: flex;
      gap: 6px;
      padding: 10px 10px 0;
      border-bottom: 1px solid var(--line);
      overflow-x: auto;
    }
    .tab {
      min-height: 34px;
      border: 1px solid transparent;
      border-bottom: 0;
      border-radius: 8px 8px 0 0;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      padding: 7px 11px;
      white-space: nowrap;
    }
    .tab.active {
      border-color: var(--line);
      background: var(--surface);
      color: var(--ink);
      font-weight: 700;
    }
    pre {
      margin: 0;
      max-height: 520px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #1f2329;
      color: #f0eee8;
      padding: 13px;
      line-height: 1.48;
      font-size: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .empty {
      border: 1px dashed var(--line-strong);
      border-radius: var(--radius);
      color: var(--muted);
      background: rgba(255, 253, 248, 0.62);
      padding: 24px;
      text-align: center;
    }
    .timeline {
      display: grid;
      gap: 9px;
    }
    .log-row {
      display: grid;
      grid-template-columns: 106px 76px minmax(0, 1fr);
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding: 9px 0;
      font-size: 13px;
    }
    .log-row:last-child { border-bottom: 0; }
    .level-error { color: var(--red); font-weight: 700; }
    .level-warning { color: var(--amber); font-weight: 700; }
    .level-info { color: var(--blue); font-weight: 700; }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      display: none;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--ink);
      color: var(--surface);
      padding: 10px 12px;
      box-shadow: var(--shadow);
      z-index: 10;
    }
    .toast.show { display: block; }
    @media (max-width: 1160px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { min-height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      .run-list { max-height: 360px; }
      .overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 700px) {
      .main { padding: 14px; }
      .brand { padding: 14px; }
      .search-row { grid-template-columns: 1fr; }
      .segments { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .topbar { display: grid; }
      .actions { justify-content: stretch; }
      .button { flex: 1; }
      .overview { grid-template-columns: 1fr; }
      .kv { grid-template-columns: 1fr; }
      .log-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">
          <h1>Novel Pipeline Monitor</h1>
          <span class="pill" id="rootBadge">loading</span>
        </div>
        <div class="filters">
          <div class="search-row">
            <input class="control" id="searchInput" placeholder="Search run, stage, message" />
            <select class="control" id="sortSelect" title="Sort">
              <option value="updated_desc">Updated desc</option>
              <option value="started_desc">Started desc</option>
              <option value="failed_first">Failed first</option>
              <option value="progress_asc">Progress asc</option>
            </select>
          </div>
          <div class="segments" id="statusSegments">
            <button class="segment active" data-status="all">All</button>
            <button class="segment" data-status="running">Running</button>
            <button class="segment" data-status="failed">Failed</button>
            <button class="segment" data-status="completed">Done</button>
            <button class="segment" data-status="stale">Stale</button>
          </div>
        </div>
      </div>
      <div class="run-list" id="runList"></div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="title-block">
          <h2 id="detailTitle">Run detail</h2>
          <div class="subtle" id="detailMeta">Waiting for run data.</div>
        </div>
        <div class="actions">
          <select class="control" id="refreshSelect" title="Refresh interval">
            <option value="2000">2s</option>
            <option value="5000">5s</option>
            <option value="10000">10s</option>
            <option value="30000">30s</option>
          </select>
          <button class="button" id="pauseButton" title="Pause refresh">Pause</button>
          <button class="button primary" id="refreshButton" title="Refresh now">Refresh</button>
        </div>
      </div>
      <div class="overview" id="overview"></div>
      <div id="detailRoot" class="empty">No run selected.</div>
    </main>
  </div>
  <div class="toast" id="toast"></div>
<script>
const state = {
  runs: [],
  selectedDir: "",
  selectedStatus: "all",
  selectedTab: "logs",
  paused: false,
  timer: null,
  dataRoot: "",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function toDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatTime(value) {
  const date = toDate(value);
  return date ? date.toLocaleString() : "-";
}

function ageSeconds(value) {
  const date = toDate(value);
  return date ? Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000)) : null;
}

function formatAge(value) {
  const seconds = ageSeconds(value);
  if (seconds === null) return "-";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function progressPct(run) {
  return Math.max(0, Math.min(100, Math.round(Number(run.progress_ratio || 0) * 100)));
}

function isStale(run) {
  if ((run.status || "") !== "running") return false;
  const seconds = ageSeconds(run.updated_at);
  return seconds !== null && seconds > 1200;
}

function statusKey(run) {
  return isStale(run) ? "stale" : (run.status || "unknown");
}

function compactPath(path) {
  const text = String(path || "");
  if (text.length <= 68) return text;
  return `${text.slice(0, 24)}...${text.slice(-38)}`;
}

function visibleRuns() {
  const query = document.getElementById("searchInput").value.trim().toLowerCase();
  const sortMode = document.getElementById("sortSelect").value;
  const status = state.selectedStatus;
  const rows = state.runs.filter((run) => {
    const key = statusKey(run);
    if (status !== "all" && key !== status) return false;
    if (!query) return true;
    const haystack = [
      run.output_dir_relative,
      run.stage,
      run.status,
      run.last_message,
      run.current_item,
      run.run_id,
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
  rows.sort((a, b) => {
    if (sortMode === "started_desc") return String(b.started_at || "").localeCompare(String(a.started_at || ""));
    if (sortMode === "failed_first") {
      const af = statusKey(a) === "failed" ? 1 : 0;
      const bf = statusKey(b) === "failed" ? 1 : 0;
      return bf - af || String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    }
    if (sortMode === "progress_asc") return Number(a.progress_ratio || 0) - Number(b.progress_ratio || 0);
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
  return rows;
}

function summarizeRuns() {
  const summary = { total: state.runs.length, running: 0, completed: 0, failed: 0, stale: 0 };
  for (const run of state.runs) {
    const key = statusKey(run);
    if (key in summary) summary[key] += 1;
  }
  return summary;
}

function renderOverview() {
  const summary = summarizeRuns();
  document.getElementById("overview").innerHTML = `
    <div class="metric"><div class="metric-label">Runs</div><div class="metric-value">${summary.total}</div></div>
    <div class="metric"><div class="metric-label">Running</div><div class="metric-value">${summary.running}</div></div>
    <div class="metric warn"><div class="metric-label">Stale</div><div class="metric-value">${summary.stale}</div></div>
    <div class="metric bad"><div class="metric-label">Failed</div><div class="metric-value">${summary.failed}</div></div>
    <div class="metric good"><div class="metric-label">Completed</div><div class="metric-value">${summary.completed}</div></div>
  `;
}

function renderRuns() {
  const list = document.getElementById("runList");
  const rows = visibleRuns();
  document.getElementById("rootBadge").textContent = `${state.runs.length} runs`;
  if (!state.selectedDir && rows.length) state.selectedDir = rows[0].output_dir_relative;
  if (!rows.length) {
    list.innerHTML = '<div class="empty">No matching runs.</div>';
    return;
  }
  list.innerHTML = rows.map((run) => {
    const key = statusKey(run);
    const active = run.output_dir_relative === state.selectedDir ? "active" : "";
    return `
      <button class="run-card ${key} ${active}" data-dir="${escapeHtml(run.output_dir_relative)}">
        <div class="run-topline">
          <span class="badge ${key}">${escapeHtml(key)}</span>
          <span class="run-meta">${progressPct(run)}%</span>
        </div>
        <div class="run-name">${escapeHtml(compactPath(run.output_dir_relative))}</div>
        <div class="bar"><div class="bar-fill" style="width:${progressPct(run)}%"></div></div>
        <div class="run-meta">${escapeHtml(run.stage || "-")} | ${formatAge(run.updated_at)}</div>
        <div class="run-meta">${escapeHtml(run.last_message || "")}</div>
      </button>`;
  }).join("");
  for (const node of list.querySelectorAll(".run-card")) {
    node.addEventListener("click", () => {
      state.selectedDir = node.dataset.dir || "";
      state.selectedTab = "logs";
      renderRuns();
      fetchDetail();
    });
  }
}

function renderStatusPanel(run) {
  const key = statusKey(run);
  const countSource = run.counter_source === "artifacts" ? "artifacts" : "run_status";
  const outputCounts = run.counter_source === "artifacts"
    ? `<strong>Tracked outputs</strong><span>${run.output_files_count || 0} files, ${run.manifest_count || 0} manifest rows</span>`
    : "";
  return `
    <section class="panel">
      <div class="panel-head">
        <h3 class="panel-title">Status</h3>
        <span class="pill">${progressPct(run)}%</span>
      </div>
      <div class="panel-body">
        <div class="status-line">
          <span class="badge ${key}">${escapeHtml(key)}</span>
          <span class="pill">${escapeHtml(run.stage || "-")}</span>
          <span class="pill">${escapeHtml(countSource)}</span>
        </div>
        <div class="bar"><div class="bar-fill" style="width:${progressPct(run)}%"></div></div>
        <div class="kv" style="margin-top:14px">
          <strong>Current item</strong><span>${escapeHtml(run.current_item || "-")}</span>
          <strong>Last message</strong><span>${escapeHtml(run.last_message || "-")}</span>
          <strong>Started</strong><span>${formatTime(run.started_at)}</span>
          <strong>Updated</strong><span>${formatTime(run.updated_at)} (${formatAge(run.updated_at)})</span>
          <strong>Finished</strong><span>${formatTime(run.finished_at)}</span>
          <strong>Output</strong><span class="mono">${escapeHtml(run.output_dir || "")}</span>
          <strong>Source</strong><span class="mono">${escapeHtml(run.source_dir || "")}</span>
          ${outputCounts}
        </div>
      </div>
    </section>`;
}

function renderCounterPanel(run) {
  return `
    <section class="panel">
      <div class="panel-head">
        <h3 class="panel-title">Counters</h3>
        <span class="pill">${escapeHtml(run.item_label || "item")}</span>
      </div>
      <div class="panel-body">
        <table>
          <tbody>
            <tr><th>Total</th><td>${run.total_items || 0}</td></tr>
            <tr><th>Processed</th><td>${run.processed_items || 0}</td></tr>
            <tr><th>Pending</th><td>${run.pending_items || 0}</td></tr>
            <tr><th>Success</th><td>${run.success_count || 0}</td></tr>
            <tr><th>Failed</th><td>${run.failure_count || 0}</td></tr>
            <tr><th>Skipped</th><td>${run.skipped_count || 0}</td></tr>
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderLogs(logs) {
  if (!logs.length) return '<div class="empty">No logs recorded.</div>';
  return `<div class="timeline">${logs.map((row) => {
    const level = row.level || "info";
    return `
      <div class="log-row">
        <div class="subtle">${formatTime(row.timestamp)}</div>
        <div class="level-${escapeHtml(level)}">${escapeHtml(level)}</div>
        <div>${escapeHtml(row.message || "")}${row.item ? ` <span class="subtle">(${escapeHtml(row.item)})</span>` : ""}</div>
      </div>`;
  }).join("")}</div>`;
}

function renderFailures(failures) {
  if (!failures.length) return '<div class="empty">No failures recorded.</div>';
  return `
    <table>
      <thead><tr><th>Item</th><th>Error</th><th>Message</th></tr></thead>
      <tbody>
        ${failures.map((row) => `
          <tr>
            <td>${escapeHtml(row.source_file || row.window_id || row.output_file || row.build_id || "-")}</td>
            <td>${escapeHtml(row.error_type || "-")}</td>
            <td>${escapeHtml(row.error_message || "-")}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

function renderTabContent(payload) {
  const run = payload.run || {};
  if (state.selectedTab === "failures") return renderFailures(payload.failures || []);
  if (state.selectedTab === "metadata") return `<pre>${escapeHtml(JSON.stringify(run.metadata || {}, null, 2))}</pre>`;
  if (state.selectedTab === "status") return `<pre>${escapeHtml(JSON.stringify(run, null, 2))}</pre>`;
  return renderLogs(payload.logs || []);
}

function renderDetail(payload) {
  const run = payload && payload.run;
  const root = document.getElementById("detailRoot");
  if (!run) {
    document.getElementById("detailTitle").textContent = "Run detail";
    document.getElementById("detailMeta").textContent = "No run selected.";
    root.innerHTML = '<div class="empty">No run selected.</div>';
    return;
  }
  document.getElementById("detailTitle").textContent = compactPath(run.output_dir_relative || run.stage || "Run detail");
  document.getElementById("detailMeta").textContent = `${escapeHtml(run.stage || "-")} | updated ${formatAge(run.updated_at)}`;
  const tabs = [
    ["logs", "Logs"],
    ["failures", `Failures (${(payload.failures || []).length})`],
    ["metadata", "Metadata"],
    ["status", "Raw status"],
  ];
  root.innerHTML = `
    <div class="detail-grid">
      <div class="panel">
        <div class="tabs">
          ${tabs.map(([id, label]) => `<button class="tab ${state.selectedTab === id ? "active" : ""}" data-tab="${id}">${label}</button>`).join("")}
        </div>
        <div class="panel-body" id="tabBody">${renderTabContent(payload)}</div>
      </div>
      <div style="display:grid;gap:14px">
        ${renderStatusPanel(run)}
        ${renderCounterPanel(run)}
      </div>
    </div>`;
  for (const node of root.querySelectorAll(".tab")) {
    node.addEventListener("click", () => {
      state.selectedTab = node.dataset.tab || "logs";
      document.getElementById("tabBody").innerHTML = renderTabContent(payload);
      for (const tab of root.querySelectorAll(".tab")) tab.classList.toggle("active", tab === node);
    });
  }
}

function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 1800);
}

async function fetchRuns() {
  const response = await fetch("/api/runs");
  if (!response.ok) throw new Error(`runs ${response.status}`);
  const payload = await response.json();
  state.dataRoot = payload.data_root || "";
  state.runs = payload.runs || [];
  renderOverview();
  renderRuns();
}

async function fetchDetail() {
  if (!state.selectedDir) {
    renderDetail({ run: null, logs: [], failures: [] });
    return;
  }
  const response = await fetch(`/api/run?dir=${encodeURIComponent(state.selectedDir)}&log_limit=300&failure_limit=80`);
  if (!response.ok) throw new Error(`detail ${response.status}`);
  renderDetail(await response.json());
}

async function tick(showToast) {
  try {
    await fetchRuns();
    await fetchDetail();
    if (showToast) toast("Refreshed");
  } catch (error) {
    document.getElementById("detailRoot").innerHTML = `<div class="empty">${escapeHtml(error.message || String(error))}</div>`;
  }
}

function resetTimer() {
  if (state.timer) window.clearInterval(state.timer);
  if (!state.paused) {
    const interval = Number(document.getElementById("refreshSelect").value || 2000);
    state.timer = window.setInterval(() => tick(false), interval);
  }
}

document.getElementById("refreshButton").addEventListener("click", () => tick(true));
document.getElementById("pauseButton").addEventListener("click", () => {
  state.paused = !state.paused;
  document.getElementById("pauseButton").textContent = state.paused ? "Resume" : "Pause";
  resetTimer();
});
document.getElementById("refreshSelect").addEventListener("change", resetTimer);
document.getElementById("searchInput").addEventListener("input", () => { renderRuns(); });
document.getElementById("sortSelect").addEventListener("change", () => { renderRuns(); });
for (const node of document.querySelectorAll(".segment")) {
  node.addEventListener("click", () => {
    state.selectedStatus = node.dataset.status || "all";
    for (const other of document.querySelectorAll(".segment")) other.classList.toggle("active", other === node);
    renderRuns();
  });
}

tick(false);
resetTimer();
</script>
</body>
</html>
"""
