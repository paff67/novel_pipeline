$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'
$config = 'D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml'
$defaultInputDir = 'D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330'
$inputDir = if ($env:NOVEL_PIPELINE_STYLE_INPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_INPUT_DIR } else { $defaultInputDir }
$outputDir = if ($env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR) { $env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR } else { 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable' }
$env:NOVEL_PIPELINE_ALLOWED_GATEWAY_INDEXES = '2,3'
$env:NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX = '2'
Remove-Item Env:NOVEL_PIPELINE_ALLOWED_GATEWAY_LABELS -ErrorAction SilentlyContinue
Remove-Item Env:NOVEL_PIPELINE_PRIMARY_GATEWAY_LABEL -ErrorAction SilentlyContinue
$env:NOVEL_PIPELINE_MAX_RPM = '0'

& $python -m novel_pipeline_stable extract-style `
  --config $config `
  --input-dir $inputDir `
  --output-dir $outputDir `
  --resume

exit $LASTEXITCODE
