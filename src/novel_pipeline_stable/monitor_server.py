from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from novel_pipeline_stable.monitor_dashboard import DASHBOARD_HTML as MODERN_DASHBOARD_HTML
from novel_pipeline_stable.monitoring import discover_runs, read_run_detail


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Novel Pipeline Monitor</title>
  <style>
    :root {
      --bg: #0f1720;
      --panel: #182230;
      --panel-2: #121a26;
      --line: #2b394d;
      --text: #eef5ff;
      --muted: #93a6bf;
      --accent: #59c3ff;
      --good: #56d28c;
      --warn: #f5b84c;
      --bad: #ff6b7a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      background: linear-gradient(180deg, #0d141d 0%, #101926 100%);
      color: var(--text);
    }
    .layout {
      display: grid;
      grid-template-columns: 360px 1fr;
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: rgba(16, 25, 38, 0.95);
      padding: 18px;
      overflow: auto;
    }
    .main {
      padding: 18px;
      overflow: auto;
    }
    h1, h2, h3 { margin: 0 0 10px; }
    .meta { color: var(--muted); font-size: 13px; margin-bottom: 14px; }
    .run-list { display: grid; gap: 10px; }
    .run-card {
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      cursor: pointer;
    }
    .run-card.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
    .run-card:hover { border-color: var(--accent); }
    .run-title { font-weight: 600; margin-bottom: 6px; }
    .run-sub, .run-small { color: var(--muted); font-size: 12px; }
    .status-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
    .badge {
      display: inline-flex;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--line);
      color: var(--muted);
    }
    .badge.running { color: var(--accent); border-color: var(--accent); }
    .badge.completed { color: var(--good); border-color: var(--good); }
    .badge.failed { color: var(--bad); border-color: var(--bad); }
    .grid { display: grid; gap: 16px; }
    .cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(24, 34, 48, 0.95);
      padding: 14px;
    }
    .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
    .card .value { margin-top: 8px; font-size: 24px; font-weight: 700; }
    .panel {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(24, 34, 48, 0.95);
      padding: 16px;
    }
    .progress {
      width: 100%;
      height: 12px;
      border-radius: 999px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      overflow: hidden;
      margin: 12px 0 10px;
    }
    .progress > div {
      height: 100%;
      background: linear-gradient(90deg, #3ca7ff 0%, #56d28c 100%);
      width: 0%;
      transition: width 0.25s ease;
    }
    .kv { display: grid; gap: 8px; }
    .kv div { color: var(--muted); }
    .kv strong { color: var(--text); }
    pre {
      margin: 0;
      padding: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      border-radius: 14px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      color: #dbe7f8;
      max-height: 420px;
      overflow: auto;
      line-height: 1.45;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 600; }
    .empty {
      border: 1px dashed var(--line);
      border-radius: 16px;
      color: var(--muted);
      padding: 24px;
      text-align: center;
      background: rgba(18, 26, 38, 0.85);
    }
    .toolbar {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
    }
    .small-button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 10px;
      padding: 8px 12px;
      cursor: pointer;
    }
    @media (max-width: 1100px) {
      .layout { grid-template-columns: 1fr; }
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 700px) {
      .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1>Live Monitor</h1>
      <div class="meta" id="summary">Loading runs...</div>
      <div class="run-list" id="runList"></div>
    </aside>
    <main class="main">
      <div class="toolbar">
        <div>
          <h2 id="detailTitle">Run detail</h2>
          <div class="meta" id="detailMeta">Polling every 2 seconds.</div>
        </div>
        <button class="small-button" id="refreshButton">Refresh now</button>
      </div>
      <div id="detailRoot" class="empty">No run selected.</div>
    </main>
  </div>
<script>
const state = {
  runs: [],
  selectedDir: '',
  polling: null,
};

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function pct(value) {
  return `${Math.round((Number(value || 0) * 100))}%`;
}

function renderRuns() {
  const list = document.getElementById('runList');
  const summary = document.getElementById('summary');
  summary.textContent = `${state.runs.length} run(s) discovered`;

  if (!state.runs.length) {
    list.innerHTML = '<div class="empty">No run_status.json found under the selected data root.</div>';
    document.getElementById('detailRoot').innerHTML = '<div class="empty">Start a pipeline command, then keep this page open.</div>';
    return;
  }

  if (!state.selectedDir || !state.runs.some((run) => run.output_dir_relative === state.selectedDir)) {
    state.selectedDir = state.runs[0].output_dir_relative;
  }

  list.innerHTML = state.runs.map((run) => {
    const active = run.output_dir_relative === state.selectedDir ? 'active' : '';
    const statusClass = escapeHtml(run.status || 'unknown');
    return `
      <div class="run-card ${active}" data-dir="${encodeURIComponent(run.output_dir_relative)}">
        <div class="status-row">
          <span class="badge ${statusClass}">${escapeHtml(run.status || 'unknown')}</span>
          <span class="run-small">${escapeHtml(run.stage || '')}</span>
        </div>
        <div class="run-title">${escapeHtml(run.output_dir_relative)}</div>
        <div class="run-sub">${escapeHtml(run.last_message || '')}</div>
        <div class="run-small">progress ${pct(run.progress_ratio)} | processed ${run.processed_items || 0}/${run.total_items || 0}</div>
      </div>`;
  }).join('');

  Array.from(list.querySelectorAll('.run-card')).forEach((node) => {
    node.addEventListener('click', () => {
      state.selectedDir = decodeURIComponent(node.dataset.dir);
      renderRuns();
      fetchDetail();
    });
  });
}

function renderDetail(payload) {
  const root = document.getElementById('detailRoot');
  const title = document.getElementById('detailTitle');
  const meta = document.getElementById('detailMeta');
  const run = payload && payload.run;
  if (!run) {
    title.textContent = 'Run detail';
    meta.textContent = 'Polling every 2 seconds.';
    root.innerHTML = '<div class="empty">No run selected.</div>';
    return;
  }

  title.textContent = `${run.stage || 'run'} / ${run.output_dir_relative || ''}`;
  meta.textContent = `updated ${formatTime(run.updated_at)} | started ${formatTime(run.started_at)}`;

  const logs = (payload.logs || []).map((row) => {
    const item = row.item ? ` (${row.item})` : '';
    return `${row.timestamp || ''} [${row.level || 'info'}] ${row.message || ''}${item}`.trim();
  }).join('\n');

  const failures = payload.failures || [];
  const metadataText = escapeHtml(JSON.stringify(run.metadata || {}, null, 2));
  const countSource = run.counter_source === 'artifacts' ? 'artifacts' : 'run_status';
  const trackedCounts = run.counter_source === 'artifacts'
    ? `<div><strong>Tracked outputs:</strong> ${run.output_files_count || 0} | <strong>Manifest rows:</strong> ${run.manifest_count || 0}</div>`
    : '';

  root.innerHTML = `
    <div class="grid">
      <div class="panel">
        <div class="status-row">
          <span class="badge ${escapeHtml(run.status || 'unknown')}">${escapeHtml(run.status || 'unknown')}</span>
          <span class="run-small">${escapeHtml(run.item_label || 'item')}</span>
        </div>
        <div class="progress"><div style="width:${pct(run.progress_ratio)}"></div></div>
        <div class="kv">
          <div><strong>Current item:</strong> ${escapeHtml(run.current_item || '-')}</div>
          <div><strong>Output dir:</strong> ${escapeHtml(run.output_dir || '')}</div>
          <div><strong>Source dir:</strong> ${escapeHtml(run.source_dir || '')}</div>
          <div><strong>Count source:</strong> ${escapeHtml(countSource)}</div>
          ${trackedCounts}
          <div><strong>Last message:</strong> ${escapeHtml(run.last_message || '')}</div>
        </div>
      </div>

      <div class="cards">
        <div class="card"><div class="label">Processed</div><div class="value">${run.processed_items || 0}</div></div>
        <div class="card"><div class="label">Success</div><div class="value">${run.success_count || 0}</div></div>
        <div class="card"><div class="label">Failed</div><div class="value">${run.failure_count || 0}</div></div>
        <div class="card"><div class="label">Skipped</div><div class="value">${run.skipped_count || 0}</div></div>
      </div>

      <div class="panel">
        <h3>Metadata</h3>
        <pre>${metadataText}</pre>
      </div>

      <div class="panel">
        <h3>Recent logs</h3>
        <pre>${escapeHtml(logs || 'No logs yet.')}</pre>
      </div>

      <div class="panel">
        <h3>Failure preview</h3>
        ${failures.length ? `
        <table>
          <thead><tr><th>Item</th><th>Error</th><th>Message</th></tr></thead>
          <tbody>
            ${failures.map((row) => `
              <tr>
                <td>${escapeHtml(row.source_file || row.window_id || row.output_file || '-')}</td>
                <td>${escapeHtml(row.error_type || '-')}</td>
                <td>${escapeHtml(row.error_message || '-')}</td>
              </tr>`).join('')}
          </tbody>
        </table>` : '<div class="empty">No failures recorded.</div>'}
      </div>
    </div>`;
}

async function fetchRuns() {
  const response = await fetch('/api/runs');
  const payload = await response.json();
  state.runs = payload.runs || [];
  renderRuns();
}

async function fetchDetail() {
  if (!state.selectedDir) {
    renderDetail({ run: null, logs: [], failures: [] });
    return;
  }
  const response = await fetch(`/api/run?dir=${encodeURIComponent(state.selectedDir)}`);
  const payload = await response.json();
  renderDetail(payload);
}

async function tick() {
  try {
    await fetchRuns();
    await fetchDetail();
  } catch (error) {
    document.getElementById('detailRoot').innerHTML = `<div class="empty">${escapeHtml(error.message || String(error))}</div>`;
  }
}

document.getElementById('refreshButton').addEventListener('click', tick);
tick();
state.polling = window.setInterval(tick, 2000);
</script>
</body>
</html>
"""


class MonitorHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_handler(data_root: str | Path):
    root = Path(data_root).resolve()

    class MonitorRequestHandler(BaseHTTPRequestHandler):
        server_version = "NovelPipelineMonitor/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._send_html(MODERN_DASHBOARD_HTML)
                return

            if parsed.path == "/api/runs":
                runs = discover_runs(root)
                self._send_json(
                    {
                        "data_root": str(root),
                        "run_count": len(runs),
                        "runs": runs,
                    }
                )
                return

            if parsed.path == "/api/run":
                query = parse_qs(parsed.query)
                requested_dir = query.get("dir", [""])[0]
                log_limit = int(query.get("log_limit", ["200"])[0])
                failure_limit = int(query.get("failure_limit", ["20"])[0])

                if not requested_dir:
                    runs = discover_runs(root)
                    if not runs:
                        self._send_json({"run": None, "logs": [], "failures": []})
                        return
                    requested_dir = runs[0].get("output_dir_relative", "")

                try:
                    payload = read_run_detail(
                        root,
                        requested_dir,
                        log_limit=log_limit,
                        failure_limit=failure_limit,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"error": str(exc), "run": None, "logs": [], "failures": []}, status=400)
                    return

                self._send_json(payload)
                return

            self._send_json({"error": f"Not found: {parsed.path}"}, status=404)

    return MonitorRequestHandler


def serve_monitor(data_root: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    root = Path(data_root).resolve()
    server = MonitorHTTPServer((host, port), create_handler(root))
    print(f"Monitor serving {root} on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Monitor stopped.")
    finally:
        server.server_close()

