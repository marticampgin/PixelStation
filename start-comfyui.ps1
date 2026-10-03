param(
    [string]$PortableRoot = (Join-Path $PSScriptRoot '.tools\comfyui\ComfyUI_windows_portable')
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PortableRoot -PathType Container)) {
    throw "ComfyUI Portable installation not found at '$PortableRoot'. Extract the official ComfyUI Windows Portable NVIDIA package there, or pass -PortableRoot 'C:\path\ComfyUI_windows_portable'."
}
$portablePath = (Resolve-Path -LiteralPath $PortableRoot).Path
$pythonPath = Join-Path $portablePath 'python_embeded\python.exe'
$mainPath = Join-Path $portablePath 'ComfyUI\main.py'
foreach ($requiredPath in @($pythonPath, $mainPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "ComfyUI Portable is incomplete: '$requiredPath' is missing. Re-extract the official Windows Portable NVIDIA package, or pass -PortableRoot pointing to its ComfyUI_windows_portable folder."
    }
}
$pythonPath = (Resolve-Path -LiteralPath $pythonPath).Path
$mainPath = (Resolve-Path -LiteralPath $mainPath).Path
$comfyArgs = @(
    '-s', $mainPath,
    '--windows-standalone-build',
    '--listen', '127.0.0.1',
    '--port', '8188',
    '--disable-auto-launch',
    '--disable-api-nodes',
    '--lowvram',
    '--disable-dynamic-vram'
)

Push-Location -LiteralPath $portablePath
try {
    & $pythonPath @comfyArgs
    $comfyExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $comfyExitCode
