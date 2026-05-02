$ErrorActionPreference = 'Stop'

$reportPath = 'D:\card\novel_pipeline\data\reports\formal_cn_gpt54_stable_live_processes.json'
$factStatusPath = 'D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable\run_status.json'
$styleStatusPath = 'D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable\run_status.json'
$canonStatusPath = 'D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable\run_status.json'
$canonRunScript = 'D:\card\novel_pipeline\scripts\run_canon_formal_cn_gpt54_stable.ps1'
$python = 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe'
$powershell = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$dataRoot = 'D:\card\novel_pipeline\data'
$logDir = 'D:\card\novel_pipeline\data\logs'
$watchdogStatusPath = 'D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_status.json'
$watchdogEventsPath = 'D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_events.jsonl'
$monitorHost = '127.0.0.1'
$monitorPort = 8765
$monitorUrl = 'http://127.0.0.1:8765'
$pollSeconds = 120
$staleThresholdMinutes = 20

New-Item -ItemType Directory -Force -Path (Split-Path $watchdogStatusPath) | Out-Null

function Write-JsonFile {
  param(
    [string]$Path,
    [object]$Data
  )

  $json = $Data | ConvertTo-Json -Depth 10
  [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
}

function Append-JsonLine {
  param(
    [string]$Path,
    [object]$Data
  )

  $json = $Data | ConvertTo-Json -Depth 10 -Compress
  Add-Content -LiteralPath $Path -Value $json -Encoding utf8
}

function Read-JsonFile {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    return $null
  }

  try {
    return Get-Content -Raw $Path | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Get-UtcDateTime {
  param([object]$Value)

  $text = [string]$Value
  if (-not $text) {
    return $null
  }

  try {
    return ([DateTimeOffset]::Parse($text)).UtcDateTime
  } catch {
    return $null
  }
}

function Update-LiveReport {
  param([hashtable]$Patch)

  $live = Read-JsonFile -Path $reportPath
  if (-not $live) {
    return
  }

  $updated = [ordered]@{}
  foreach ($property in $live.PSObject.Properties) {
    $updated[$property.Name] = $property.Value
  }
  foreach ($entry in $Patch.GetEnumerator()) {
    $updated[$entry.Key] = $entry.Value
  }
  Write-JsonFile -Path $reportPath -Data $updated
}

function Get-MonitorListener {
  if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    return $null
  }

  return Get-NetTCPConnection -LocalPort $monitorPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Get-ProcessParentId {
  param([int]$ProcessId)

  try {
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId)
    if ($process) {
      return [int]$process.ParentProcessId
    }
  } catch {
    return $null
  }

  return $null
}

function Ensure-MonitorRunning {
  param([datetime]$CheckedAt)

  $listener = Get-MonitorListener
  if ($listener) {
    $monitorPid = [int]$listener.OwningProcess
    $monitorParentShellPid = Get-ProcessParentId -ProcessId $monitorPid
    Update-LiveReport -Patch @{
      monitor_url = $monitorUrl
      monitor_pid = $monitorPid
      monitor_parent_shell_pid = $monitorParentShellPid
    }
    return [ordered]@{
      restarted = $false
      pid = $monitorPid
      parent_shell_pid = $monitorParentShellPid
    }
  }

  $monitorCommand = @"
`$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
& '$python' -m novel_pipeline_stable serve-monitor --data-root '$dataRoot' --host $monitorHost --port $monitorPort
"@
  $monitorStdout = Join-Path $logDir 'monitor_stdout.log'
  $monitorStderr = Join-Path $logDir 'monitor_stderr.log'
  $monitorShell = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-Command',$monitorCommand -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $monitorStdout -RedirectStandardError $monitorStderr -PassThru

  Start-Sleep -Seconds 2

  $monitorPid = $null
  $listener = Get-MonitorListener
  if ($listener) {
    $monitorPid = [int]$listener.OwningProcess
  } else {
    try {
      $child = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $monitorShell.Id -and $_.Name -eq 'python.exe' } | Select-Object -First 1
      if ($child) {
        $monitorPid = [int]$child.ProcessId
      }
    } catch {
      $monitorPid = $null
    }
  }

  Update-LiveReport -Patch @{
    monitor_url = $monitorUrl
    monitor_pid = $monitorPid
    monitor_parent_shell_pid = $monitorShell.Id
  }

  return [ordered]@{
    restarted = $true
    pid = $monitorPid
    parent_shell_pid = $monitorShell.Id
  }
}

function Test-CanonNeedsBuild {
  param(
    [object]$FactStatus,
    [object]$StyleStatus,
    [object]$CanonStatus
  )

  if (-not $FactStatus -or -not $StyleStatus) {
    return $false
  }
  if ([string]$FactStatus.status -ne 'completed') {
    return $false
  }
  if ([string]$StyleStatus.status -ne 'completed') {
    return $false
  }
  if ($CanonStatus -and [string]$CanonStatus.status -eq 'running') {
    return $false
  }
  if (-not $CanonStatus) {
    return $true
  }
  if ([string]$CanonStatus.status -ne 'completed') {
    return $true
  }

  $factUpdatedUtc = Get-UtcDateTime -Value $FactStatus.updated_at
  $styleUpdatedUtc = Get-UtcDateTime -Value $StyleStatus.updated_at
  $canonUpdatedUtc = Get-UtcDateTime -Value $CanonStatus.updated_at

  $latestSourceUtc = $factUpdatedUtc
  if ($styleUpdatedUtc -and (-not $latestSourceUtc -or $styleUpdatedUtc -gt $latestSourceUtc)) {
    $latestSourceUtc = $styleUpdatedUtc
  }

  if ($latestSourceUtc -and $canonUpdatedUtc -and $canonUpdatedUtc -lt $latestSourceUtc) {
    return $true
  }
  return $false
}

function Start-CanonBuild {
  param([datetime]$CheckedAt)

  $timestamp = $CheckedAt.ToString('yyyyMMdd_HHmmss')
  $canonStdout = Join-Path $logDir ("canon_stdout_{0}.log" -f $timestamp)
  $canonStderr = Join-Path $logDir ("canon_stderr_{0}.log" -f $timestamp)
  $canonProc = Start-Process -FilePath $powershell -ArgumentList '-NoLogo','-NoProfile','-File',$canonRunScript -WorkingDirectory 'D:\card' -WindowStyle Hidden -RedirectStandardOutput $canonStdout -RedirectStandardError $canonStderr -PassThru

  $canonPythonPid = $null
  Start-Sleep -Seconds 3
  try {
    $child = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $canonProc.Id -and $_.Name -eq 'python.exe' } | Select-Object -First 1
    if ($child) {
      $canonPythonPid = $child.ProcessId
    }
  } catch {
    $canonPythonPid = $null
  }

  Update-LiveReport -Patch @{
    canon_shell_pid = $canonProc.Id
    canon_python_pid = $canonPythonPid
    canon_stdout_log = $canonStdout
    canon_stderr_log = $canonStderr
  }

  return [ordered]@{
    shell_pid = $canonProc.Id
    python_pid = $canonPythonPid
    stdout_log = $canonStdout
    stderr_log = $canonStderr
  }
}

$lastFingerprint = $null
$lastProcessedItems = $null
$lastFailureCount = $null
$lastStyleProcessedItems = $null
$lastStyleFailureCount = $null

while ($true) {
  $checkedAt = Get-Date
  $checkedAtUtc = $checkedAt.ToUniversalTime()
  $live = Read-JsonFile -Path $reportPath
  $status = Read-JsonFile -Path $factStatusPath
  $styleStatus = Read-JsonFile -Path $styleStatusPath
  $canonStatus = Read-JsonFile -Path $canonStatusPath
  $monitorInfo = Ensure-MonitorRunning -CheckedAt $checkedAt

  $factsPid = $null
  if ($live -and $live.facts_python_pid) {
    $factsPid = [int]$live.facts_python_pid
  }

  $factsProcess = $null
  if ($factsPid) {
    $factsProcess = Get-Process -Id $factsPid -ErrorAction SilentlyContinue
  }

  $stylePid = $null
  if ($live -and $live.style_python_pid) {
    $stylePid = [int]$live.style_python_pid
  }

  $styleProcess = $null
  if ($stylePid) {
    $styleProcess = Get-Process -Id $stylePid -ErrorAction SilentlyContinue
  }

  $alerts = New-Object System.Collections.Generic.List[string]
  if ($monitorInfo.restarted) {
    $alerts.Add('monitor_restarted')
  }
  $updatedAtUtc = $null
  $processedItems = $null
  $failureCount = $null
  $statusName = $null
  $currentItem = $null
  $lastMessage = $null
  $pendingItems = $null
  $styleStatusName = if ($styleStatus) { [string]$styleStatus.status } else { $null }
  $styleProcessedItems = if ($styleStatus) { [int]$styleStatus.processed_items } else { $null }
  $styleFailureCount = if ($styleStatus) { [int]$styleStatus.failure_count } else { $null }
  $stylePendingItems = if ($styleStatus) { [int]$styleStatus.pending_items } else { $null }
  $styleCurrentItem = if ($styleStatus) { [string]$styleStatus.current_item } else { $null }
  $styleLastMessage = if ($styleStatus) { [string]$styleStatus.last_message } else { $null }
  $styleUpdatedAtUtc = if ($styleStatus) { Get-UtcDateTime -Value $styleStatus.updated_at } else { $null }
  $styleStaleMinutes = $null
  $canonStatusName = if ($canonStatus) { [string]$canonStatus.status } else { $null }
  $canonTriggered = $false
  $canonTriggerInfo = $null

  if (-not $status) {
    $alerts.Add('missing_run_status')
  } else {
    $statusName = [string]$status.status
    $processedItems = [int]$status.processed_items
    $failureCount = [int]$status.failure_count
    $pendingItems = [int]$status.pending_items
    $currentItem = [string]$status.current_item
    $lastMessage = [string]$status.last_message

    $updatedAtUtc = Get-UtcDateTime -Value $status.updated_at
    if (-not $updatedAtUtc) {
      $updatedAtUtc = $null
      $alerts.Add('invalid_updated_at')
    }

    if ($updatedAtUtc) {
      $staleMinutes = [math]::Round(($checkedAtUtc - $updatedAtUtc).TotalMinutes, 2)
      if ($staleMinutes -ge $staleThresholdMinutes) {
        $alerts.Add('no_progress_20m')
      }
    } else {
      $staleMinutes = $null
    }

    if ($lastFailureCount -ne $null -and $failureCount -gt $lastFailureCount) {
      $alerts.Add('failure_count_increased')
    }

    if ($factsPid -and -not $factsProcess -and $statusName -eq 'running') {
      $alerts.Add('fact_process_not_running')
    }

    if (Test-CanonNeedsBuild -FactStatus $status -StyleStatus $styleStatus -CanonStatus $canonStatus) {
      $canonTriggerInfo = Start-CanonBuild -CheckedAt $checkedAt
      $canonTriggered = $true
      $canonStatusName = 'triggered'
      $alerts.Add('canon_build_triggered')
    }

    $lastProcessedItems = $processedItems
    $lastFailureCount = $failureCount
  }

  if (-not $styleStatus) {
    $alerts.Add('missing_style_run_status')
  } else {
    if ($styleUpdatedAtUtc) {
      $styleStaleMinutes = [math]::Round(($checkedAtUtc - $styleUpdatedAtUtc).TotalMinutes, 2)
      if ($styleStatusName -eq 'running' -and $styleStaleMinutes -ge $staleThresholdMinutes) {
        $alerts.Add('style_no_progress_20m')
      }
    } else {
      $alerts.Add('style_invalid_updated_at')
    }

    if ($lastStyleFailureCount -ne $null -and $styleFailureCount -gt $lastStyleFailureCount) {
      $alerts.Add('style_failure_count_increased')
    }

    if ($stylePid -and -not $styleProcess -and $styleStatusName -eq 'running') {
      $alerts.Add('style_process_not_running')
    }

    $lastStyleProcessedItems = $styleProcessedItems
    $lastStyleFailureCount = $styleFailureCount
  }

  $snapshot = [ordered]@{
    checked_at = $checkedAt.ToString('o')
    status = $statusName
    processed_items = $processedItems
    failure_count = $failureCount
    pending_items = $pendingItems
    current_item = $currentItem
    last_message = $lastMessage
    updated_at = if ($status) { [string]$status.updated_at } else { $null }
    stale_minutes = $staleMinutes
    monitor_pid = $monitorInfo.pid
    monitor_parent_shell_pid = $monitorInfo.parent_shell_pid
    monitor_process_running = [bool]$monitorInfo.pid
    facts_python_pid = $factsPid
    facts_process_running = [bool]$factsProcess
    style_status = $styleStatusName
    style_processed_items = $styleProcessedItems
    style_failure_count = $styleFailureCount
    style_pending_items = $stylePendingItems
    style_current_item = $styleCurrentItem
    style_last_message = $styleLastMessage
    style_updated_at = if ($styleStatus) { [string]$styleStatus.updated_at } else { $null }
    style_stale_minutes = $styleStaleMinutes
    style_python_pid = $stylePid
    style_process_running = [bool]$styleProcess
    canon_status = $canonStatusName
    canon_triggered = $canonTriggered
    canon_trigger_info = $canonTriggerInfo
    alerts = @($alerts)
  }

  Write-JsonFile -Path $watchdogStatusPath -Data $snapshot

  $fingerprint = '{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}|{8}|{9}|{10}' -f $statusName, $processedItems, $failureCount, $pendingItems, $factsPid, $styleStatusName, $styleProcessedItems, $styleFailureCount, $stylePid, $canonStatusName, ($alerts -join ',')
  if ($fingerprint -ne $lastFingerprint) {
    Append-JsonLine -Path $watchdogEventsPath -Data $snapshot
    $lastFingerprint = $fingerprint
  }

  Start-Sleep -Seconds $pollSeconds
}
