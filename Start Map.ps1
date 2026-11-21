$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the virtual environment first: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt' }
try { Invoke-RestMethod 'http://127.0.0.1:8765/data/versions.json' | Out-Null }
catch { Start-Process -FilePath $python -ArgumentList 'server.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden }
Start-Process 'http://127.0.0.1:8765/'
