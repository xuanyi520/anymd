@echo off
setlocal
if defined MSYSTEM (
    set "PYTHONIOENCODING=utf-8"
) else (
    set "PYTHONIOENCODING=gbk"
)
"%~dp0.venv\Scripts\anymd.exe" %*
