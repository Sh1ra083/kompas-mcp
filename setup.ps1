$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 and venv are required' }
& .\.venv\Scripts\python.exe -m pip install --requirement requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
