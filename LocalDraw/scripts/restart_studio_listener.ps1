param([Parameter(Mandatory)][ValidateRange(1,65535)][int]$Port)
$ErrorActionPreference = 'Stop'
function Read-StudioJson($client, [string]$url) {
    $bytes = $client.DownloadData($url)
    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
        return (ConvertFrom-Json -InputObject ($utf8.GetString($bytes)) -ErrorAction Stop)
    } catch {
        throw "Invalid UTF-8 JSON from $url. Restart cancelled."
    }
}
$root = Split-Path -Parent $PSScriptRoot
$listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen)
if ($listeners.Count -ne 1) { throw 'Cannot identify a single loopback listener.' }
$ownerId = $listeners[0].OwningProcess
$process = Get-Process -Id $ownerId
try {
    # Pin the process handle before validating its command line and children.
    $null = $process.Handle
    $instance = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
    $entry = [regex]::Escape((Join-Path $root 'launch.py'))
    $command = '^(?:"[^"]+"|\S+)\s+(?:"{0}"|{0})\s+--no-browser\s*$' -f $entry
    if ($instance.Name -notin @('python.exe','pythonw.exe') -or $instance.CommandLine -notmatch $command) {
        throw 'The listener is not the Python server from this LocalDraw folder.'
    }
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ownerId" |
        Where-Object { $_.Name -ne 'conhost.exe' })
    if ($children.Count) { throw 'The backend owns a worker. Unload the studio model before retrying.' }
    $client = New-Object System.Net.WebClient
    try {
        $client.Proxy = $null
        $base = "http://127.0.0.1:$Port/api"
        $session = Read-StudioJson $client "$base/session"
        $state = Read-StudioJson $client "$base/status"
        if (-not $session.token -or $null -eq $state.queue.running -or $null -eq $state.jobs -or
            $session.token -cne $env:LOCALDRAW_REPAIR_SESSION -or $state.queue.running -or
            @($state.jobs | Where-Object { $_.status -in @('queued','running','paused','pausing','cancelling') }).Count) {
            throw 'The studio session or task state changed. Please retry after it is idle.'
        }
    } finally { $client.Dispose() }
    $process.Kill()
    if (-not $process.WaitForExit(10000)) { throw 'The previous backend did not exit.' }
} finally {
    $process.Dispose()
}
