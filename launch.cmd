@echo off
setlocal
cd /d "%~dp0"
if exist "dist\kompas-mcp\kompas-mcp.exe" (
  "dist\kompas-mcp\kompas-mcp.exe"
) else (
  ".venv\Scripts\python.exe" "%~dp0run.py"
)
