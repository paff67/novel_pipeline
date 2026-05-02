$ErrorActionPreference = 'Stop'

$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'
$config = if ($env:NOVEL_PIPELINE_CONFIG) { $env:NOVEL_PIPELINE_CONFIG } else { 'D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml' }
$factInput = if ($env:NOVEL_PIPELINE_FACT_INPUT_DIR) { $env:NOVEL_PIPELINE_FACT_INPUT_DIR } else { 'D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330' }
$styleInput = if ($env:NOVEL_PIPELINE_STYLE_INPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_INPUT_DIR } else { 'D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330' }
$factsOut = if ($env:NOVEL_PIPELINE_FACT_OUTPUT_DIR) { $env:NOVEL_PIPELINE_FACT_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable' }
$styleOut = if ($env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable' }
$canonOut = if ($env:NOVEL_PIPELINE_CANON_OUTPUT_DIR) { $env:NOVEL_PIPELINE_CANON_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable' }
$reviewOut = if ($env:NOVEL_PIPELINE_REVIEW_OUTPUT_DIR) { $env:NOVEL_PIPELINE_REVIEW_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\review_formal_cn_gpt54_stable' }
$factScript = 'D:\card\novel_pipeline\scripts\run_fact_formal_cn_gpt54_stable.ps1'
$styleScript = 'D:\card\novel_pipeline\scripts\run_style_formal_cn_gpt54_stable.ps1'
$canonScript = 'D:\card\novel_pipeline\scripts\run_canon_formal_cn_gpt54_stable.ps1'

$env:NOVEL_PIPELINE_CONFIG = $config
$env:NOVEL_PIPELINE_FACT_INPUT_DIR = $factInput
$env:NOVEL_PIPELINE_STYLE_INPUT_DIR = $styleInput
$env:NOVEL_PIPELINE_FACT_OUTPUT_DIR = $factsOut
$env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR = $styleOut
$env:NOVEL_PIPELINE_CANON_OUTPUT_DIR = $canonOut

& $factScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $styleScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $canonScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m novel_pipeline_stable build-review-panel `
  --facts-dir $factsOut `
  --style-dir $styleOut `
  --output-dir $reviewOut

exit $LASTEXITCODE
