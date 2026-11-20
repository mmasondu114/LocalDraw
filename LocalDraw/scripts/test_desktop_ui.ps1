$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sdk = Join-Path $projectRoot 'runtime\desktop'
$testOutput = Join-Path $projectRoot 'data\test-results\desktop-redesign'
New-Item -ItemType Directory -Force -Path $testOutput | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 "/win32manifest:$PSScriptRoot\windows\LocalDraw.manifest" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/reference:$sdk\Microsoft.Web.WebView2.Core.dll" "/reference:$sdk\Microsoft.Web.WebView2.WinForms.dll" "/out:$testOutput\DesktopUiTests.exe" (Join-Path $PSScriptRoot 'windows\DesktopUiTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'UI acceptance build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows\LocalDraw.config') -Destination "$testOutput\DesktopUiTests.exe.config" -Force
$process = Start-Process -FilePath "$testOutput\DesktopUiTests.exe" -ArgumentList "`"$projectRoot`"" -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(45000)) { $process.Kill(); throw 'UI acceptance timed out.' }
if ($process.ExitCode -ne 0) { throw (Get-Content -LiteralPath "$testOutput\failure.txt" -Raw) }
Get-Content -LiteralPath "$testOutput\result.json"
