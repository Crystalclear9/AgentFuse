# Dot-source this file: . .\scripts\enter-local.ps1
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:UV_CACHE_DIR = Join-Path $projectRoot '.tools/cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.tools/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $projectRoot '.tools/bin'
$env:PIP_CACHE_DIR = Join-Path $projectRoot '.tools/cache/pip'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.tools/browsers'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$temporaryRoot = Join-Path $projectRoot '.agentfuse/tmp'
New-Item -ItemType Directory -Force $temporaryRoot | Out-Null
$env:TEMP = $temporaryRoot
$env:TMP = $temporaryRoot
$localPaths = @('.venv-local/Scripts', '.venv-local/Lib/site-packages/playwright/driver', '.tools/git/cmd', '.tools/docker', '.tools/uv', '.tools/ripgrep')
$env:PATH = (($localPaths | ForEach-Object { Join-Path $projectRoot $_ }) -join ';') + ';' + $env:PATH
$browser = Get-ChildItem -LiteralPath $env:PLAYWRIGHT_BROWSERS_PATH -Filter chrome.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($browser) { $env:AGENTFUSE_BROWSER_PATH = $browser.FullName }
