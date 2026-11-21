$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the virtual environment first: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt' }
Push-Location $PSScriptRoot
try { & $python etl.py; if ($LASTEXITCODE -ne 0) { throw 'ETL failed; check the private inputs.' } }
finally { Pop-Location }
