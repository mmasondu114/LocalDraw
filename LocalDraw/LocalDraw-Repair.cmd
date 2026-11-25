@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "STUDIO_ROOT="
set "STUDIO_CHECK="
if /i "%~1"=="--check" set "STUDIO_CHECK=--check"
for %%D in ("%~dp0." "%~dp0LocalDraw") do call :candidate "%%~fD"
if not defined STUDIO_ROOT (
  for /f "usebackq delims=" %%D in (`powershell.exe -NoProfile -NonInteractive -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in 'DesktopDirectory','CommonDesktopDirectory','Programs'){ $shortcut=Join-Path ([Environment]::GetFolderPath($d)) 'LocalDraw.lnk'; if(Test-Path -LiteralPath $shortcut){ $target=$w.CreateShortcut($shortcut).TargetPath; if($target){ [IO.Path]::GetDirectoryName($target) } } }"`) do call :candidate "%%D"
)
if not defined STUDIO_ROOT call :candidate "%LOCALAPPDATA%\Programs\LocalDraw"
if not defined STUDIO_ROOT call :candidate "F:\ChatGPT\local_draw\LocalDraw"
if not defined STUDIO_ROOT (
  echo LocalDraw.exe could not be located. Place this CMD beside LocalDraw.exe and run again.
  goto :failed
)
echo LocalDraw: "%STUDIO_ROOT%"
if not exist "%STUDIO_ROOT%\scripts\refresh_wardrobe.py" (
  echo This copy of LocalDraw does not contain the Wardrobe repair files.
  goto :failed
)
set "PYTHONUTF8=1"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $r=$env:STUDIO_ROOT; $config=Join-Path $r 'config\installed-runtime.json'; $paths=@(); if(Test-Path -LiteralPath $config){ $cfg=Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json; if($cfg.pythonw){ $p=[IO.Path]::GetFullPath([IO.Path]::Combine($r,$cfg.pythonw)); $paths+=Join-Path ([IO.Path]::GetDirectoryName($p)) 'python.exe' } }; foreach($rel in 'runtime\python\python.exe','.venv\Scripts\python.exe','..\.venv\Scripts\python.exe'){ $paths+=[IO.Path]::GetFullPath([IO.Path]::Combine($r,$rel)) }; $python=$paths | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1; if(!$python){throw ('Missing LocalDraw Python. Checked: '+($paths -join ', '))}; Write-Host ('Python: '+$python); $env:COMIC_STUDIO_ROOT=$r; $entry=Join-Path $r 'scripts\refresh_wardrobe.py'; if($env:STUDIO_CHECK){ & $python -B $entry --check }else{ & $python -B $entry }; exit $LASTEXITCODE"
set "STUDIO_RESULT=%errorlevel%"
goto :done

:candidate
if defined STUDIO_ROOT exit /b
if exist "%~1\LocalDraw.exe" if exist "%~1\launch.py" for %%R in ("%~1") do set "STUDIO_ROOT=%%~fR"
exit /b

:failed
set "STUDIO_RESULT=1"
:done
if not defined STUDIO_CHECK pause
exit /b %STUDIO_RESULT%
