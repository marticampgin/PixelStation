param([switch]$NoBrowser, [switch]$Check)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$uvPath = Join-Path $projectRoot '.tools\uv\uv.exe'
if (-not (Test-Path -LiteralPath $uvPath)) {
    New-Item -ItemType Directory -Path '.tools' -Force | Out-Null
    Write-Host 'Downloading the per-project uv runtime...'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '.tools\uv.zip'
    Expand-Archive -LiteralPath '.tools\uv.zip' -DestinationPath '.tools\uv' -Force
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & $uvPath python install 3.12 --install-dir '.tools\python' --no-managed-python
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    $managedPython = Get-ChildItem -LiteralPath '.tools\python' -Filter 'cpython-3.12.*' -Directory | Select-Object -First 1
    & $uvPath venv --python (Join-Path $managedPython.FullName 'python.exe') '.venv'
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
}
$lockHash = (Get-FileHash -LiteralPath 'backend\requirements.lock').Hash
$backendStamp = Join-Path $projectRoot '.tools\backend-installed.txt'
if (-not (Test-Path -LiteralPath $backendStamp) -or (Get-Content -LiteralPath $backendStamp -Raw).Trim() -ne $lockHash) {
    & $uvPath pip install --python $pythonPath --require-hashes -r 'backend\requirements.lock'
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    Set-Content -LiteralPath $backendStamp -Value $lockHash
}
$frontendHash = (Get-FileHash -LiteralPath 'frontend\package-lock.json').Hash
$frontendStamp = Join-Path $projectRoot '.tools\frontend-installed.txt'
if (-not (Test-Path -LiteralPath 'frontend\node_modules') -or -not (Test-Path -LiteralPath $frontendStamp) -or (Get-Content -LiteralPath $frontendStamp -Raw).Trim() -ne $frontendHash) {
    Push-Location -LiteralPath 'frontend'
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally { Pop-Location }
    Set-Content -LiteralPath $frontendStamp -Value $frontendHash
}
if ($Check) {
    Push-Location -LiteralPath 'backend'
    try {
        & $pythonPath -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
        & $pythonPath -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw 'Backend lint failed.' }
    } finally { Pop-Location }
    Push-Location -LiteralPath 'frontend'
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        & npm.cmd test -- --run
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
    } finally { Pop-Location }
    exit 0
}
$launcherArgs = @('scripts\launch.py')
if ($NoBrowser) { $launcherArgs += '--no-browser' }
& $pythonPath @launcherArgs
exit $LASTEXITCODE
