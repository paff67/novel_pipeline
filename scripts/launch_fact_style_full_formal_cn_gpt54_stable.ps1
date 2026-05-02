$ErrorActionPreference = 'Stop'

$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'
$powershell = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$dataRoot = 'D:\card\novel_pipeline\data'
$logDir = 'D:\card\novel_pipeline\data\logs'
$reportPath = 'D:\card\novel_pipeline\data\reports\formal_cn_gpt54_stable_live_processes.json'
$factRunScript = 'D:\card\novel_pipeline\scripts\run_fact_formal_cn_gpt54_stable.ps1'
$styleRunScript = 'D:\card\novel_pipeline\scripts\run_style_formal_cn_gpt54_stable.ps1'
$watchdogRunScript = 'D:\card\novel_pipeline\scripts\run_fact_watchdog_formal_cn_gpt54_stable.ps1'
$monitorUrl = 'http://127.0.0.1:8765'
$factInput = 'D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330'
$styleInput = 'D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330'
$factOutput = 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable'
$styleOutput = 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable'
$canonOutput = 'D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable'
$watchdogStatusPath = 'D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_status.json'
$watchdogEventsPath = 'D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_events.jsonl'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $reportPath) | Out-Null

$monitorPid = $null
$listener = $null
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
  $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

if (-not $listener) {
  $monitorCommand = @"
`$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
& '$python' -m novel_pipeline_stable serve-monitor --data-root '$dataRoot' --host 127.0.0.1 --port 8765
"@
  $monitorStdout = Join-Path $logDir 'monitor_stdout.log'
  $monitorStderr = Join-Path $logDir 'monitor_stderr.log'
  $monitorProc = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-Command',$monitorCommand -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $monitorStdout -RedirectStandardError $monitorStderr -PassThru
  $monitorPid = $monitorProc.Id
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'

$factStdout = Join-Path $logDir ("facts_full_resume_stdout_{0}.log" -f $timestamp)
$factStderr = Join-Path $logDir ("facts_full_resume_stderr_{0}.log" -f $timestamp)
$factCommand = @"
`$env:NOVEL_PIPELINE_FACT_INPUT_DIR = '$factInput'
`$env:NOVEL_PIPELINE_FACT_OUTPUT_DIR = '$factOutput'
& '$factRunScript'
"@
$factShell = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-Command',$factCommand -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $factStdout -RedirectStandardError $factStderr -PassThru

$styleStdout = Join-Path $logDir ("style_full_resume_stdout_{0}.log" -f $timestamp)
$styleStderr = Join-Path $logDir ("style_full_resume_stderr_{0}.log" -f $timestamp)
$styleCommand = @"
`$env:NOVEL_PIPELINE_STYLE_INPUT_DIR = '$styleInput'
`$env:NOVEL_PIPELINE_STYLE_OUTPUT_DIR = '$styleOutput'
& '$styleRunScript'
"@
$styleShell = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-Command',$styleCommand -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $styleStdout -RedirectStandardError $styleStderr -PassThru

Start-Sleep -Seconds 4

$factPython = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $factShell.Id -and $_.Name -eq 'python.exe' } | Select-Object -First 1
$stylePython = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $styleShell.Id -and $_.Name -eq 'python.exe' } | Select-Object -First 1

$watchdogStdout = Join-Path $logDir ("fact_watchdog_stdout_{0}.log" -f $timestamp)
$watchdogStderr = Join-Path $logDir ("fact_watchdog_stderr_{0}.log" -f $timestamp)
$watchdogShell = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-File',$watchdogRunScript -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $watchdogStdout -RedirectStandardError $watchdogStderr -PassThru

$processInfo = [ordered]@{
  started_at = (Get-Date).ToString('s')
  mode = 'resume_fact_style_full_with_watchdog'
  monitor_url = $monitorUrl
  monitor_pid = $monitorPid
  facts_shell_pid = $factShell.Id
  facts_python_pid = if ($factPython) { $factPython.ProcessId } else { $null }
  facts_allowed_gateway_indexes = '1,3'
  facts_primary_gateway_index = 1
  facts_input = $factInput
  facts_output = $factOutput
  facts_stdout_log = $factStdout
  facts_stderr_log = $factStderr
  style_shell_pid = $styleShell.Id
  style_python_pid = if ($stylePython) { $stylePython.ProcessId } else { $null }
  style_allowed_gateway_indexes = '2,3'
  style_primary_gateway_index = 2
  style_input = $styleInput
  style_output = $styleOutput
  style_stdout_log = $styleStdout
  style_stderr_log = $styleStderr
  canon_output = $canonOutput
  rpm_limit = 'disabled'
  rpm_env_override = 'NOVEL_PIPELINE_MAX_RPM=0'
  fact_watchdog_shell_pid = $watchdogShell.Id
  fact_watchdog_stdout_log = $watchdogStdout
  fact_watchdog_stderr_log = $watchdogStderr
  fact_watchdog_status_path = $watchdogStatusPath
  fact_watchdog_events_path = $watchdogEventsPath
  api_route = 'responses'
  reasoning_effort = 'xhigh'
}
$processInfo | ConvertTo-Json | Set-Content -Path $reportPath -Encoding utf8

$processInfo | ConvertTo-Json -Compress
