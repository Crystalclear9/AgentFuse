param([switch]$AllPythonVersions)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'enter-local.ps1')
$downloads = Join-Path $projectRoot '.tools/downloads'
New-Item -ItemType Directory -Force $downloads | Out-Null
if (-not (Test-Path -LiteralPath '.tools/uv/uv.exe')) {
    Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-pc-windows-msvc.zip' -OutFile (Join-Path $downloads 'uv.zip')
    Expand-Archive -LiteralPath (Join-Path $downloads 'uv.zip') -DestinationPath '.tools/uv' -Force
}
$uv = Join-Path $projectRoot '.tools/uv/uv.exe'
& $uv python install 3.11.18
if ($LASTEXITCODE) { throw 'Local Python installation failed' }
if (-not (Test-Path -LiteralPath '.venv-local/Scripts/python.exe')) {
    & $uv venv --python 3.11.18 .venv-local
    if ($LASTEXITCODE) { throw 'Local virtual environment creation failed' }
}
& $uv pip sync --python .venv-local/Scripts/python.exe requirements-dev.lock
if ($LASTEXITCODE) { throw 'Locked dependencies installation failed' }
& $uv pip install --python .venv-local/Scripts/python.exe --no-deps --no-build-isolation -e .
if ($LASTEXITCODE) { throw 'Project installation failed' }
if ($AllPythonVersions) {
    & $uv python install 3.12.16 3.13.18
    if ($LASTEXITCODE) { throw 'Compatibility Python installation failed' }
    foreach ($entry in @(@('3.12.16', 'py312'), @('3.13.18', 'py313'))) {
        $environmentPath = Join-Path $projectRoot ".tools/envs/$($entry[1])"
        $environmentPython = Join-Path $environmentPath 'Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $environmentPython)) {
            & $uv venv --python $entry[0] $environmentPath
            if ($LASTEXITCODE) { throw 'Compatibility environment creation failed' }
        }
        & $uv pip sync --python $environmentPython requirements-dev.lock
        if ($LASTEXITCODE) { throw 'Compatibility dependencies installation failed' }
        & $uv pip install --python $environmentPython --no-deps --no-build-isolation -e .
        if ($LASTEXITCODE) { throw 'Compatibility project installation failed' }
    }
}
# Tests launch the full Chromium executable explicitly; avoid a redundant shell.
& .venv-local/Scripts/python.exe -m playwright install --no-shell chromium
if ($LASTEXITCODE) { throw 'Local browser installation failed' }
if (-not (Test-Path -LiteralPath '.tools/git/cmd/git.exe')) {
    Invoke-WebRequest 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/PortableGit-2.56.0.4-64-bit.7z.exe' -OutFile (Join-Path $downloads 'portable-git.exe')
    & (Join-Path $downloads 'portable-git.exe') -y "-o$(Join-Path $projectRoot '.tools/git')" | Out-Null
    if (-not (Test-Path -LiteralPath '.tools/git/cmd/git.exe')) { throw 'Portable Git installation failed' }
}
. (Join-Path $PSScriptRoot 'enter-local.ps1')
if (Test-Path -LiteralPath '.git') {
    # Avoid PortableGit's first-use GUI helper selector for this repository.
    & .tools/git/cmd/git.exe config --local --replace-all credential.helper ''
    & .tools/git/cmd/git.exe config --local --add credential.helper manager
}
Write-Host 'Local tools ready. Dot-source scripts/enter-local.ps1 in each new shell.'
