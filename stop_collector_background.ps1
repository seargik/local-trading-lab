Set-Location $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot "data\collector.pid"
if (Test-Path $pidFile) {
    $pid = Get-Content $pidFile | Select-Object -First 1
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "No collector.pid file found."
}
