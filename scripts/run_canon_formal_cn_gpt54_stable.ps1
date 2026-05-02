$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'
$factsDir = if ($env:NOVEL_PIPELINE_FACT_OUTPUT_DIR) { $env:NOVEL_PIPELINE_FACT_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable' }
$styleDir = if ($env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable' }
$outputDir = if ($env:NOVEL_PIPELINE_CANON_OUTPUT_DIR) { $env:NOVEL_PIPELINE_CANON_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable' }
$storyNodes = $env:NOVEL_PIPELINE_STORY_NODES
$nodeId = $env:NOVEL_PIPELINE_CANON_NODE_ID

if ($storyNodes -and $nodeId) {
  & $python -m novel_pipeline_stable build-canon `
    --facts-dir $factsDir `
    --style-dir $styleDir `
    --output-dir $outputDir `
    --story-nodes $storyNodes `
    --node-id $nodeId
} else {
  & $python -m novel_pipeline_stable build-canon `
    --facts-dir $factsDir `
    --style-dir $styleDir `
    --output-dir $outputDir
}

exit $LASTEXITCODE
