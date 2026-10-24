<#
.SYNOPSIS
    Configura el entorno virtual aislado (.venv) y las caches locales del proyecto DocLayout-Preserve.
#>
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

New-Item -ItemType Directory -Force -Path "$ProjectRoot\.cache\pip", "$ProjectRoot\.tmp" | Out-Null
$env:PIP_CACHE_DIR = "$ProjectRoot\.cache\pip"
$env:TMP = "$ProjectRoot\.tmp"
$env:TEMP = "$ProjectRoot\.tmp"

$VenvPython = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[setup] Creando entorno virtual aislado en .venv..." -ForegroundColor Cyan
    $CandidatePythons = @(
        "C:\Users\joanr\AppData\Local\Google\Cloud SDK\google-cloud-sdk\platform\bundledpython\python.exe",
        "python"
    )
    $FoundPy = $null
    foreach ($candidate in $CandidatePythons) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $FoundPy = $candidate
            break
        }
    }
    if (-not $FoundPy) {
        throw "No se encontro un interprete de Python disponible para crear .venv."
    }
    & $FoundPy -m venv "$ProjectRoot\.venv"
}

Write-Host "[setup] Instalando dependencias en .venv con cache local..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r "$ProjectRoot\requirements.txt" -r "$ProjectRoot\requirements-dev.txt"

Write-Host "[setup] Entorno .venv configurado correctamente." -ForegroundColor Green
