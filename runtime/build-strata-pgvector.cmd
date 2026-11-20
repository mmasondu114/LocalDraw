@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "PGROOT=C:\Users\mmaso\Documents\ChatGPT\local_draw\runtime\strata-postgres\pgsql"
cd /d "C:\Users\mmaso\Documents\ChatGPT\local_draw\runtime\pgvector"
nmake /F Makefile.win
if errorlevel 1 exit /b 1
nmake /F Makefile.win install
exit /b %errorlevel%
