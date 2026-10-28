<#
.SYNOPSIS
    Inicia la interfaz interactiva de Streamlit restringida localmente a 127.0.0.1:8501 usando .venv.
#>
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"

$VenvPython = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "No existe .venv\Scripts\python.exe. Ejecuta primero .\scripts\dev_setup.ps1"
}

$env:PYTHONPATH = "$ProjectRoot\src"
$BindHost = if ($env:APP_HOST) { $env:APP_HOST } else { "127.0.0.1" }
$BindPort = if ($env:APP_PORT) { $env:APP_PORT } else { "8501" }

Write-Host "[ui] Iniciando Streamlit en http://${BindHost}:${BindPort} ..." -ForegroundColor Cyan
& $VenvPython -m streamlit run "$ProjectRoot\src\doclayout\ui\app.py" `
    --server.address $BindHost `
    --server.port $BindPort `
    --browser.gatherUsageStats false
