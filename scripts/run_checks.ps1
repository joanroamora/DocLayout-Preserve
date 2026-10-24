<#
.SYNOPSIS
    Ejecuta las validaciones de calidad de codigo (ruff, black, isort, mypy y pytest) usando .venv.
#>
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "No existe .venv\Scripts\python.exe. Ejecuta primero .\scripts\dev_setup.ps1"
}

function Invoke-Step {
    param([string]$Label, [scriptblock]$Action)
    Write-Host "==> $Label" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo la verificacion: $Label (exit code $LASTEXITCODE)"
    }
}

Invoke-Step "1/5 Ejecutando Ruff Linter..." { & $VenvPython -m ruff check src tests }
Invoke-Step "2/5 Verificando formato con Black..." { & $VenvPython -m black --check src tests }
Invoke-Step "3/5 Verificando orden de imports con isort..." { & $VenvPython -m isort --check-only src tests }
Invoke-Step "4/5 Ejecutando verificacion de tipos con Mypy..." { & $VenvPython -m mypy src tests }
Invoke-Step "5/5 Ejecutando bateria de pruebas unitarias con Pytest..." { & $VenvPython -m pytest }

Write-Host "Todas las validaciones finalizaron exitosamente." -ForegroundColor Green
