# Starts Property Case Investigator locally:
#   backend  -> FastAPI on http://127.0.0.1:8000 (API + single investigation worker + SQLite)
#   frontend -> Vite on http://localhost:5173 (proxies /api to the backend)
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host 'Creating the Python virtual environment (first run only)...'
    python -m venv (Join-Path $backend '.venv')
    & $python -m pip install --disable-pip-version-check -q -r (Join-Path $backend 'requirements.txt')
}
if (-not (Test-Path (Join-Path $backend '.env'))) {
    Copy-Item (Join-Path $backend '.env.example') (Join-Path $backend '.env')
    Write-Host 'Created backend\.env from .env.example. Add OPENAI_API_KEY there for live model runs.'
}
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host 'Installing frontend packages (first run only)...'
    Push-Location $frontend
    npm.cmd ci
    Pop-Location
}

Start-Process powershell -ArgumentList '-NoExit', '-Command',
    "Set-Location '$backend'; & '$python' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$frontend'; npm.cmd run dev"

Write-Host ''
Write-Host 'Backend health: http://127.0.0.1:8000/api/health'
Write-Host 'Open the app:   http://localhost:5173'
