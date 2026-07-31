param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [string]$BackendHost = '127.0.0.1',
    [string]$FrontendHost = '0.0.0.0',
    [switch]$NoStopExisting
)

$ErrorActionPreference = 'Stop'

$AppRoot = $PSScriptRoot
$BackendDir = Join-Path $AppRoot 'backend'
$FrontendDir = Join-Path $AppRoot 'frontend'

function Assert-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Resolve-BackendPython {
    # The backend's dependencies live in backend\.venv, not in whatever `python`
    # happens to resolve to on PATH. Launching the bare interpreter produced a
    # window that failed on `import sqlalchemy` while the frontend came up fine,
    # which reads as "the backend is broken" rather than "wrong interpreter".
    $venvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $fallback = Get-Command python -ErrorAction SilentlyContinue
    if (-not $fallback) {
        throw "No interpreter found. Expected a virtualenv at $venvPython, and 'python' is not in PATH."
    }
    return $fallback.Source
}

function Assert-BackendDependencies {
    param([string]$Python)

    & $Python -c 'import fastapi, sqlalchemy, uvicorn, alembic' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw ("Backend dependencies are missing from '$Python'. " +
               "Create the virtualenv and install them:`n" +
               "  cd $BackendDir`n" +
               "  python -m venv .venv`n" +
               "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt")
    }
}

function Stop-Listeners {
    param([int[]]$Ports)

    $listeners = Get-NetTCPConnection -LocalPort $Ports -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($processId in $listeners) {
        if ($processId -and $processId -ne $PID) {
            Write-Host "Stopping existing listener PID $processId on ports $($Ports -join ', ')..."
            Stop-Process -Id $processId -Force
        }
    }
}

function Start-DevWindow {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )

    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    $shell = if ($pwsh) { $pwsh.Source } else { (Get-Command powershell -ErrorAction Stop).Source }
    $escapedTitle = $Title.Replace("'", "''")
    $commandWithTitle = "`$host.UI.RawUI.WindowTitle = '$escapedTitle'; $Command"

    Start-Process -FilePath $shell -WorkingDirectory $WorkingDirectory -ArgumentList @(
        '-NoExit',
        '-Command',
        $commandWithTitle
    )
}

Assert-Command node

$BackendPython = Resolve-BackendPython
Assert-BackendDependencies -Python $BackendPython

if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    throw "Frontend dependencies are missing. Run 'npm install' in $FrontendDir first."
}

if (-not $NoStopExisting) {
    Stop-Listeners -Ports @($BackendPort, $FrontendPort)
}

# Vite proxies /api to the backend, so a non-default -BackendPort has to reach the
# dev server too; without this the frontend loads and every request 500s.
$backendOrigin = "http://localhost:$BackendPort"

$backendCommand = "& '$BackendPython' -m uvicorn api.main:app --host $BackendHost --port $BackendPort"
$frontendCommand = "`$env:VITE_BACKEND_ORIGIN = '$backendOrigin'; node .\node_modules\vite\bin\vite.js --host $FrontendHost --port $FrontendPort"

Start-DevWindow -Title 'Store Replenishment Backend' -WorkingDirectory $BackendDir -Command $backendCommand
Start-DevWindow -Title 'Store Replenishment Frontend' -WorkingDirectory $FrontendDir -Command $frontendCommand

Write-Host "Backend starting at http://$BackendHost`:$BackendPort"
Write-Host "Frontend starting at http://localhost:$FrontendPort"
