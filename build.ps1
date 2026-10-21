param([string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& $Python scripts/export_schemas.py
if ($LASTEXITCODE -ne 0) { throw 'Schema export failed' }
& $Python -m build --wheel --no-isolation
if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed' }
& $Python -m PyInstaller --noconfirm --name kompas-mcp --onedir --console --paths src --collect-all mcp --collect-all mcp_types --collect-all jsonschema_specifications --hidden-import win32timezone --hidden-import win32com.client.makepy --add-data 'src/kompas_mcp/schemas.json;kompas_mcp' --add-data 'src/kompas_mcp/compatibility.json;kompas_mcp' --add-data 'src/kompas_mcp/data;kompas_mcp/data' run.py
if ($LASTEXITCODE -ne 0) { throw 'Windows package build failed' }
