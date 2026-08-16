Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Start-Process -WindowStyle Minimized -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "collector_worker.py"
