param([string]$OutputPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is missing.' }
$source = Join-Path $PSScriptRoot 'windows\StudioLauncher.cs'
$output = if ($OutputPath) { $OutputPath } else { Join-Path $projectRoot 'LocalDraw.exe' }
$icon = Join-Path $PSScriptRoot 'windows\LocalDraw.ico'
$manifest = Join-Path $PSScriptRoot 'windows\LocalDraw.manifest'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/win32manifest:$manifest" "/win32icon:$icon" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.Core.dll "/out:$output" $source (Join-Path $PSScriptRoot "windows\StudioApi.cs") (Join-Path $PSScriptRoot "windows\StudioTheme.cs") (Join-Path $PSScriptRoot "windows\StudioWindow.cs")
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows\LocalDraw.config') -Destination ($output + '.config') -Force
Write-Output "Built $output"
