$ErrorActionPreference = 'Stop'

$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'

$configPath = if ($env:NOVEL_PIPELINE_CONFIG) { $env:NOVEL_PIPELINE_CONFIG } else { 'D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml' }
$scenesDir = if ($env:NOVEL_PIPELINE_SCENES_SOURCE_DIR) { $env:NOVEL_PIPELINE_SCENES_SOURCE_DIR } else { 'D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330' }
$factsDir = if ($env:NOVEL_PIPELINE_FACT_OUTPUT_DIR) { $env:NOVEL_PIPELINE_FACT_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable' }
$styleDir = if ($env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable' }
$storyNodesPath = if ($env:NOVEL_PIPELINE_STORY_NODES) { $env:NOVEL_PIPELINE_STORY_NODES } else { 'D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json' }
$outputRoot = if ($env:NOVEL_PIPELINE_STORY_NODE_OUTPUT_ROOT) { $env:NOVEL_PIPELINE_STORY_NODE_OUTPUT_ROOT } else { 'D:\card\novel_pipeline\data\semantic_versions_formal_cn_gpt54_stable' }
$targetNodeId = $env:NOVEL_PIPELINE_NODE_ID
$styleBibleGatewayIndex = if ($env:NOVEL_PIPELINE_STORY_NODE_STYLE_BIBLE_GATEWAY_INDEX) { $env:NOVEL_PIPELINE_STORY_NODE_STYLE_BIBLE_GATEWAY_INDEX } else { '1' }
$maxStyleWindows = if ($env:NOVEL_PIPELINE_STORY_NODE_MAX_STYLE_WINDOWS) { $env:NOVEL_PIPELINE_STORY_NODE_MAX_STYLE_WINDOWS } else { '24' }
$maxSceneSamples = if ($env:NOVEL_PIPELINE_STORY_NODE_MAX_SCENE_SAMPLES) { $env:NOVEL_PIPELINE_STORY_NODE_MAX_SCENE_SAMPLES } else { '24' }
$maxPlotNodes = if ($env:NOVEL_PIPELINE_STORY_NODE_MAX_PLOT_NODES) { $env:NOVEL_PIPELINE_STORY_NODE_MAX_PLOT_NODES } else { '24' }
$maxChapterSummaries = if ($env:NOVEL_PIPELINE_STORY_NODE_MAX_CHAPTER_SUMMARIES) { $env:NOVEL_PIPELINE_STORY_NODE_MAX_CHAPTER_SUMMARIES } else { '24' }
$maxEntitySamples = if ($env:NOVEL_PIPELINE_STORY_NODE_MAX_ENTITY_SAMPLES) { $env:NOVEL_PIPELINE_STORY_NODE_MAX_ENTITY_SAMPLES } else { '20' }

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$nodeStatusJson = @'
import json
from pathlib import Path

scenes_dir = Path(r"__SCENES_DIR__")
facts_dir = Path(r"__FACTS_DIR__")
manifest_path = Path(r"__STORY_NODES_PATH__")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

def rows_by_chapter(base: Path):
    rows = []
    for path in sorted(base.glob("scene_*.json")):
        parts = path.stem.split("_")
        if len(parts) >= 3 and parts[1].isdigit():
            rows.append((int(parts[1]), path.name))
    return rows

scene_rows = rows_by_chapter(scenes_dir)
fact_rows = rows_by_chapter(facts_dir)
status_rows = []
for node in manifest.get("nodes", []):
    if not isinstance(node, dict):
        continue
    if not node.get("selected") or str(node.get("status", "")).lower() != "confirmed":
        continue
    start = int(str(node.get("start_chapter", "0")) or 0)
    end = int(str(node.get("end_chapter", "0")) or 0)
    scene_count = sum(1 for chapter, _ in scene_rows if start <= chapter <= end)
    fact_count = sum(1 for chapter, _ in fact_rows if start <= chapter <= end)
    status_rows.append(
        {
            "node_id": str(node.get("node_id", "")),
            "label": str(node.get("label", "")),
            "start_chapter": str(node.get("start_chapter", "")),
            "end_chapter": str(node.get("end_chapter", "")),
            "scene_count": scene_count,
            "fact_count": fact_count,
            "fact_complete": scene_count > 0 and fact_count == scene_count,
        }
    )
print(json.dumps(status_rows, ensure_ascii=True))
'@

$nodeStatusJson = $nodeStatusJson.Replace('__SCENES_DIR__', $scenesDir.Replace('\', '\\'))
$nodeStatusJson = $nodeStatusJson.Replace('__FACTS_DIR__', $factsDir.Replace('\', '\\'))
$nodeStatusJson = $nodeStatusJson.Replace('__STORY_NODES_PATH__', $storyNodesPath.Replace('\', '\\'))
$nodeStatuses = $nodeStatusJson | & $python - | ConvertFrom-Json

if (-not $nodeStatuses -or @($nodeStatuses).Count -eq 0) {
  Write-Host "No confirmed story nodes found in $storyNodesPath"
  exit 0
}

$processedAny = $false
foreach ($node in @($nodeStatuses)) {
  if ($targetNodeId -and $node.node_id -ne $targetNodeId) {
    continue
  }

  $rangeLabel = "$($node.start_chapter)-$($node.end_chapter)"
  if (-not $node.fact_complete) {
    Write-Host "Skipping $($node.node_id) [$rangeLabel]: fact coverage $($node.fact_count)/$($node.scene_count)"
    continue
  }

  $processedAny = $true
  $nodeRoot = Join-Path $outputRoot $node.node_id
  $canonDir = Join-Path $nodeRoot 'canon'
  $styleBibleDir = Join-Path $nodeRoot 'style_bible'
  $styleBibleEvalDir = Join-Path $nodeRoot 'style_bible_eval'

  New-Item -ItemType Directory -Force -Path $nodeRoot, $canonDir, $styleBibleDir, $styleBibleEvalDir | Out-Null

  Write-Host "=== build-canon :: $($node.node_id) [$rangeLabel] ==="
  & $python -m novel_pipeline_stable build-canon `
    --facts-dir $factsDir `
    --style-dir $styleDir `
    --output-dir $canonDir `
    --story-nodes $storyNodesPath `
    --node-id $node.node_id
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }

  $previousGatewayIndex = $env:NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX
  try {
    $env:NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX = $styleBibleGatewayIndex
    Write-Host "=== build-style-bible :: $($node.node_id) [$rangeLabel] ==="
    & $python -m novel_pipeline_stable build-style-bible `
      --config $configPath `
      --facts-dir $factsDir `
      --style-dir $styleDir `
      --canon-dir $canonDir `
      --output-dir $styleBibleDir `
      --max-style-windows $maxStyleWindows `
      --max-scene-samples $maxSceneSamples `
      --max-plot-nodes $maxPlotNodes `
      --max-chapter-summaries $maxChapterSummaries `
      --max-entity-samples $maxEntitySamples `
      --resume
    if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
    }
  } finally {
    if ($null -eq $previousGatewayIndex) {
      Remove-Item Env:NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX -ErrorAction SilentlyContinue
    } else {
      $env:NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX = $previousGatewayIndex
    }
  }

  Write-Host "=== evaluate-style-bible :: $($node.node_id) [$rangeLabel] ==="
  & $python -m novel_pipeline_stable evaluate-style-bible `
    --config $configPath `
    --input-dir $styleBibleDir `
    --output-dir $styleBibleEvalDir `
    --resume
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

if (-not $processedAny) {
  if ($targetNodeId) {
    Write-Host "No node was eligible to run for target node id: $targetNodeId"
  } else {
    Write-Host "No confirmed story node has full fact coverage yet."
  }
}

exit 0
