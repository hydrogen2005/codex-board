param([string]$PythonCommand = 'python')
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
$previousCache = $env:PYINSTALLER_CONFIG_DIR
try {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build\pyinstaller-cache'
    $uiDirectory = Join-Path $PSScriptRoot 'ui'
    & $PythonCommand -m PyInstaller --noconfirm --onefile --console --noupx `
        --name 'Codex用量看板' --add-data "${uiDirectory}:ui" `
        --distpath 'dist' --workpath 'build\pyinstaller' --specpath 'build' `
        'codex_usage_ui.py'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $executable = Join-Path $PSScriptRoot 'dist\Codex用量看板.exe'
    Get-Item -LiteralPath $executable | Select-Object FullName, Length
    Get-FileHash -LiteralPath $executable -Algorithm SHA256
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $previousCache
    Pop-Location
}
