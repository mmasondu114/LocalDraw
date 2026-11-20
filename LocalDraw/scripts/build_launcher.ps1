param([string]$OutputPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is missing.' }
$source = Join-Path $PSScriptRoot 'windows\StudioLauncher.cs'
$output = if ($OutputPath) { $OutputPath } else { Join-Path $projectRoot 'LocalDraw.exe' }
$sdk = Join-Path $projectRoot 'runtime\desktop'
$core = Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $sdk 'Microsoft.Web.WebView2.WinForms.dll'
$loader = Join-Path $sdk 'WebView2Loader.dll'
foreach ($dependency in @($core,$forms,$loader)) { if (-not (Test-Path -LiteralPath $dependency)) { throw "Missing WebView2 SDK: $dependency" } }
$icon = Join-Path $PSScriptRoot 'windows\LocalDraw.ico'
$manifest = Join-Path $PSScriptRoot 'windows\LocalDraw.manifest'
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$archive = [IO.Path]::GetTempFileName()
try {
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Update)
    try {
        foreach ($file in @($core, $forms, $loader)) {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, [IO.Path]::GetFileName($file)) | Out-Null
        }
    } finally { $zip.Dispose() }
    & $compiler /nologo /target:winexe /platform:x64 /optimize+ "/win32manifest:$manifest" "/win32icon:$icon" /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$core" "/reference:$forms" "/resource:$archive,desktop.zip" "/out:$output" $source
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
} finally { Remove-Item -LiteralPath $archive }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows\LocalDraw.config') -Destination ($output + '.config') -Force
Write-Output "Built $output"
