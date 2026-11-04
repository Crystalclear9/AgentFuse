# Source this file from Bash: source scripts/enter-local.sh
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_PYTHON_INSTALL_DIR="$project_root/.tools/python-linux"
export UV_PYTHON_BIN_DIR="$project_root/.tools/bin-linux"
export UV_CACHE_DIR="$project_root/.tools/cache/uv-linux"
export PIP_CACHE_DIR="$project_root/.tools/cache/pip-linux"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.tools/browsers-linux"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
export TMPDIR="$project_root/.agentfuse/tmp-linux"
mkdir -p "$TMPDIR"
export PATH="$project_root/.venv-wsl/bin:$project_root/.venv-wsl/lib/python3.11/site-packages/playwright/driver:$project_root/.tools/uv-linux/uv-x86_64-unknown-linux-gnu:$PATH"
