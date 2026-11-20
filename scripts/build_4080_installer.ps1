param(
    [Parameter(Mandatory=$true)][string]$Makensis,
    [Parameter(Mandatory=$true)][string]$SevenZip,
    [Parameter(Mandatory=$true)][string]$WebView2Offline,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$work = [IO.Path]::GetFullPath($BuildDirectory)
$output = [IO.Path]::GetFullPath($OutputPath)
if ((Test-Path -LiteralPath $work) -or (Test-Path -LiteralPath $output)) { throw 'Choose a new staging directory and output file.' }
$nsis = (Resolve-Path -LiteralPath $Makensis).Path
$seven = (Resolve-Path -LiteralPath $SevenZip).Path
$webview = (Resolve-Path -LiteralPath $WebView2Offline).Path
$signature = Get-AuthenticodeSignature -LiteralPath $webview
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation') { throw 'Invalid WebView2 offline installer signature.' }
New-Item -ItemType Directory -Path $work | Out-Null
$payload = Join-Path $work 'payload'
& python (Join-Path $PSScriptRoot 'stage_4080.py') $payload
if ($LASTEXITCODE) { throw 'Runtime staging failed.' }
& (Join-Path $PSScriptRoot 'build_launcher.ps1') -OutputPath (Join-Path $payload 'LocalDraw.exe')
Copy-Item -LiteralPath $webview -Destination (Join-Path $payload 'WebView2Offline.exe')
$archive = Join-Path $work 'payload.7z'
$entries = (Get-ChildItem -LiteralPath $payload).Name
Push-Location $payload
try {
    & $seven a -t7z -mx=1 -ms=64m -mmt=12 '-xr!__pycache__' '-xr!*.pyc' $archive @entries
    if ($LASTEXITCODE) { throw 'Packaging failed.' }
} finally { Pop-Location }
$size = (Get-ChildItem -LiteralPath $payload -Recurse -File | Measure-Object Length -Sum).Sum
$wizard = Join-Path $work 'wizard.exe'
& $nsis /V2 "/DSTUB=$wizard" "/DSEVENZIP=$seven" "/DSIZE_KB=$([math]::Ceiling($size/1KB))" (Join-Path $PSScriptRoot 'setup_4080.nsi')
if ($LASTEXITCODE) { throw 'Setup wizard compilation failed.' }
$hostExe = Join-Path $work 'setup-host.exe'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$icon = Join-Path $PSScriptRoot 'windows\LocalDraw.ico'
& $csc /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Windows.Forms.dll "/win32icon:$icon" "/resource:$wizard,wizard" "/out:$hostExe" (Join-Path $PSScriptRoot 'windows\SetupLauncher.cs')
if ($LASTEXITCODE) { throw 'Setup launcher compilation failed.' }
$size = (Get-Item -LiteralPath $hostExe).Length + (Get-Item -LiteralPath $archive).Length
if ($size -ge 4GB) { throw 'Archive exceeds the Windows single-executable size limit.' }
$stream = [IO.File]::Open($output, [IO.FileMode]::CreateNew)
try {
    foreach ($part in @($hostExe,$archive)) {
        $inputFile = [IO.File]::OpenRead($part)
        try { $inputFile.CopyTo($stream) } finally { $inputFile.Dispose() }
    }
} finally { $stream.Dispose() }
Write-Output "Built $output"
