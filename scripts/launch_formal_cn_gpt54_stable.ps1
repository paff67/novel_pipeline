$powershell = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$logDir = 'D:\card\novel_pipeline\data\logs'
$factsOut = 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable'
$styleOut = 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable'
$canonOut = 'D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable'
$reviewOut = 'D:\card\novel_pipeline\data\review_formal_cn_gpt54_stable'
$runScript = 'D:\card\novel_pipeline\scripts\run_formal_cn_gpt54_stable.ps1'
$monitorScript = 'D:\card\novel_pipeline\scripts\run_monitor.ps1'
$monitorUrl = 'http://127.0.0.1:8765'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$monitorPid = $null
$listener = $null
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
  $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

if (-not $listener) {
  $monitorProc = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-File',$monitorScript -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'monitor_stdout.log') -RedirectStandardError (Join-Path $logDir 'monitor_stderr.log') -PassThru
  $monitorPid = $monitorProc.Id
}

$pipelineProc = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-File',$runScript -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'formal_cn_gpt54_stable_stdout.log') -RedirectStandardError (Join-Path $logDir 'formal_cn_gpt54_stable_stderr.log') -PassThru

$processInfo = [ordered]@{
  started_at = (Get-Date).ToString('s')
  monitor_url = $monitorUrl
  monitor_pid = $monitorPid
  pipeline_pid = $pipelineProc.Id
  facts_output = $factsOut
  style_output = $styleOut
  canon_output = $canonOut
  review_output = $reviewOut
  log_dir = $logDir
}
$processInfo | ConvertTo-Json | Set-Content -Path 'D:\card\novel_pipeline\data\reports\formal_cn_gpt54_stable_live_processes.json' -Encoding utf8

try {
  Start-Process $monitorUrl | Out-Null
} catch {
}

$processInfo | ConvertTo-Json -Compress
