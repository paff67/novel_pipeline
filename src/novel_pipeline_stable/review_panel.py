from __future__ import annotations

import json
from pathlib import Path

from novel_pipeline_stable.io_utils import ensure_dir, iter_json_files, read_json, write_json, write_text


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Novel Review Panel</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #11151c;
      --panel: #1a2230;
      --muted: #8fa0b8;
      --line: #2c394f;
      --text: #edf3ff;
      --accent: #7ac7ff;
      --accent2: #ffd166;
      --good: #7bd389;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      background: linear-gradient(180deg, #10141b 0%, #151d29 100%);
      color: var(--text);
    }
    .app {
      display: grid;
      grid-template-columns: 360px 1fr;
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: rgba(17, 21, 28, 0.95);
      padding: 18px;
      overflow: auto;
    }
    .main {
      padding: 18px;
      overflow: auto;
    }
    h1, h2, h3 { margin: 0 0 12px; }
    .meta {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 16px;
    }
    .controls {
      display: grid;
      gap: 10px;
      margin-bottom: 16px;
    }
    select, input {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0f1621;
      color: var(--text);
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .item {
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      cursor: pointer;
    }
    .item:hover { border-color: var(--accent); }
    .item.active { border-color: var(--accent2); box-shadow: inset 0 0 0 1px var(--accent2); }
    .item .title { font-weight: 600; margin-bottom: 6px; }
    .item .sub { color: var(--muted); font-size: 12px; }
    .grid {
      display: grid;
      gap: 16px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(26, 34, 48, 0.95);
      padding: 16px;
    }
    .pill {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      margin: 0 6px 6px 0;
      color: var(--muted);
      font-size: 12px;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      padding: 14px;
      border-radius: 12px;
      background: #0d131c;
      border: 1px solid var(--line);
      color: #d5e2ff;
      line-height: 1.5;
    }
    .kv {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
    }
    .kv div { color: var(--muted); }
    .kv strong { color: var(--text); }
    .empty {
      color: var(--muted);
      padding: 24px;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 14px;
    }
    .section-title {
      margin-bottom: 10px;
      color: var(--accent);
      font-size: 13px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
  </style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <h1>Review Panel</h1>
    <div class="meta" id="summary"></div>
    <div class="controls">
      <select id="mode">
        <option value="facts">Facts</option>
        <option value="style">Style</option>
      </select>
      <input id="chapterFilter" placeholder="Filter by chapter id, scene id, or title" />
    </div>
    <div class="list" id="list"></div>
  </aside>
  <main class="main">
    <div id="detail" class="empty">Select an item to inspect extracted content.</div>
  </main>
</div>
<script id="review-data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById('review-data').textContent);
const modeEl = document.getElementById('mode');
const filterEl = document.getElementById('chapterFilter');
const listEl = document.getElementById('list');
const detailEl = document.getElementById('detail');
const summaryEl = document.getElementById('summary');
let selectedId = null;

function getItems() {
  return modeEl.value === 'facts' ? data.facts : data.style;
}

function getLabel(item) {
  if (modeEl.value === 'facts') {
    return `${item.chapter_id} / ${item.scene_id}`;
  }
  return `${item.window_id}`;
}

function getSubtitle(item) {
  if (modeEl.value === 'facts') {
    return `${item.scene_summary || ''}`;
  }
  return `${(item.chapter_ids || []).join(', ')}`;
}

function renderSummary() {
  summaryEl.textContent = `Facts: ${data.summary.fact_count} | Style windows: ${data.summary.style_count}`;
}

function renderList() {
  const q = filterEl.value.trim().toLowerCase();
  const items = getItems().filter((item) => {
    if (!q) return true;
    const hay = JSON.stringify(item).toLowerCase();
    return hay.includes(q);
  });

  if (!items.length) {
    listEl.innerHTML = '<div class="empty">No items matched the current filter.</div>';
    detailEl.innerHTML = '<div class="empty">Select an item to inspect extracted content.</div>';
    return;
  }

  if (!selectedId || !items.some((item) => getLabel(item) === selectedId)) {
    selectedId = getLabel(items[0]);
  }

  listEl.innerHTML = items.map((item) => {
    const label = getLabel(item);
    const active = label === selectedId ? 'active' : '';
    return `<div class="item ${active}" data-id="${encodeURIComponent(label)}"><div class="title">${label}</div><div class="sub">${escapeHtml(getSubtitle(item)).slice(0, 140)}</div></div>`;
  }).join('');

  Array.from(listEl.querySelectorAll('.item')).forEach((node) => {
    node.addEventListener('click', () => {
      selectedId = decodeURIComponent(node.dataset.id);
      renderList();
      renderDetail();
    });
  });

  renderDetail();
}

function escapeHtml(text) {
  return String(text || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function pills(values) {
  return (values || []).map((v) => `<span class="pill">${escapeHtml(v)}</span>`).join('');
}

function jsonBlocks(values, emptyText) {
  return (values || []).length
    ? values.map((value) => `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`).join('')
    : `<div class="empty">${escapeHtml(emptyText)}</div>`;
}

function compactScalarContracts(item) {
  const scalarContracts = item.scalar_contracts || {};
  return Object.entries(scalarContracts)
    .filter(([, value]) => value && value !== 'unspecified')
    .map(([key, value]) => `<div><strong>${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`)
    .join('');
}

function renderFactDetail(item) {
  const facts = item.facts || [];
  const events = item.events || [];
  const entities = item.entities || [];
  const styleMarkers = item.style_markers || [];
  detailEl.innerHTML = `
    <div class="grid">
      <div class="card">
        <div class="section-title">Scene</div>
        <div class="kv">
          <div><strong>Chapter:</strong> ${escapeHtml(item.chapter_id)}</div>
          <div><strong>Scene:</strong> ${escapeHtml(item.scene_id)}</div>
          <div><strong>Summary:</strong> ${escapeHtml(item.scene_summary || '')}</div>
        </div>
      </div>
      <div class="card">
        <div class="section-title">Entities</div>
        ${entities.length ? entities.map((e) => `<pre>${escapeHtml(JSON.stringify(e, null, 2))}</pre>`).join('') : '<div class="empty">No entities extracted.</div>'}
      </div>
      <div class="card">
        <div class="section-title">Events</div>
        ${events.length ? events.map((e) => `<pre>${escapeHtml(JSON.stringify(e, null, 2))}</pre>`).join('') : '<div class="empty">No events extracted.</div>'}
      </div>
      <div class="card">
        <div class="section-title">Facts</div>
        ${facts.length ? facts.map((f) => `<pre>${escapeHtml(JSON.stringify(f, null, 2))}</pre>`).join('') : '<div class="empty">No facts extracted.</div>'}
      </div>
      <div class="card">
        <div class="section-title">Scene Style Markers</div>
        ${styleMarkers.length ? styleMarkers.map((m) => `<pre>${escapeHtml(JSON.stringify(m, null, 2))}</pre>`).join('') : '<div class="empty">No style markers extracted.</div>'}
      </div>
    </div>`;
}

function renderStyleDetail(item) {
  const primaryRules = [
    ...(item.narrative_engine_rules || []),
    ...(item.dialogue_rules || []),
    ...(item.characterization_rules || []),
    ...(item.humor_rules || []),
  ];
  const routingHints = [
    ...(item.rag_candidates || []),
    ...(item.worldbook_candidates || []),
    ...(item.routing_hints || []),
  ];
  detailEl.innerHTML = `
    <div class="grid">
      <div class="card">
        <div class="section-title">Window</div>
        <div class="kv">
          <div><strong>Window ID:</strong> ${escapeHtml(item.window_id)}</div>
          <div><strong>Chapters:</strong> ${escapeHtml((item.chapter_ids || []).join(', '))}</div>
          <div><strong>Schema:</strong> ${escapeHtml(item.schema_version || '')}</div>
          ${compactScalarContracts(item) || '<div class="empty">No scalar contracts extracted.</div>'}
        </div>
      </div>
      <div class="card">
        <div class="section-title">Surface Markers</div>
        <div>${pills(item.surface_markers)}</div>
      </div>
      <div class="card">
        <div class="section-title">Primary Rules</div>
        ${jsonBlocks(primaryRules, 'No primary style rules extracted.')}
      </div>
      <div class="card">
        <div class="section-title">Routing Hints</div>
        ${jsonBlocks(routingHints, 'No routing hints extracted.')}
      </div>
      <div class="card">
        <div class="section-title">Negative Pitfalls</div>
        ${jsonBlocks(item.negative_pitfalls, 'No negative pitfalls extracted.')}
      </div>
      <div class="card">
        <div class="section-title">Evidence Index</div>
        ${jsonBlocks(item.evidence_index, 'No evidence index extracted.')}
      </div>
      <div class="card">
        <div class="section-title">Full JSON</div>
        <pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>
      </div>
    </div>`;
}

function renderDetail() {
  const item = getItems().find((row) => getLabel(row) === selectedId);
  if (!item) {
    detailEl.innerHTML = '<div class="empty">Select an item to inspect extracted content.</div>';
    return;
  }
  if (modeEl.value === 'facts') {
    renderFactDetail(item);
  } else {
    renderStyleDetail(item);
  }
}

modeEl.addEventListener('change', renderList);
filterEl.addEventListener('input', renderList);
renderSummary();
renderList();
</script>
</body>
</html>
"""


METADATA_JSON_NAMES = {"manifest.json", "failures.json", "run_status.json"}

def build_review_panel(facts_dir: str | Path, style_dir: str | Path, output_dir: str | Path) -> Path:
    output_path = ensure_dir(output_dir)
    fact_rows = []
    style_rows = []

    for path in iter_json_files(facts_dir):
        if path.name in METADATA_JSON_NAMES:
            continue
        fact_rows.append(read_json(path))

    for path in iter_json_files(style_dir):
        if path.name in METADATA_JSON_NAMES:
            continue
        style_rows.append(read_json(path))

    fact_rows.sort(key=lambda row: (row.get("chapter_id", ""), row.get("scene_id", "")))
    style_rows.sort(key=lambda row: row.get("window_id", ""))

    payload = {
        "summary": {
            "fact_count": len(fact_rows),
            "style_count": len(style_rows),
        },
        "facts": fact_rows,
        "style": style_rows,
    }

    write_json(output_path / "review_data.json", payload)
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    html_path = output_path / "review_panel.html"
    write_text(html_path, html)
    return html_path



