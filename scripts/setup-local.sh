#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/enter-local.sh"
cd "$project_root"
mkdir -p .tools/downloads .tools/uv-linux
uv_binary="$project_root/.tools/uv-linux/uv-x86_64-unknown-linux-gnu/uv"
if [[ ! -x "$uv_binary" ]]; then
  curl -fL 'https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-unknown-linux-gnu.tar.gz' -o .tools/downloads/uv-linux.tar.gz
  tar -xzf .tools/downloads/uv-linux.tar.gz -C .tools/uv-linux
fi
"$uv_binary" python install 3.11.18
if [[ ! -x .venv-wsl/bin/python ]]; then "$uv_binary" venv --python 3.11.18 .venv-wsl; fi
"$uv_binary" pip sync --python .venv-wsl/bin/python requirements-dev.lock
"$uv_binary" pip install --python .venv-wsl/bin/python --no-deps --no-build-isolation -e .
printf 'Local Linux tools ready. Source scripts/enter-local.sh in each new shell.\n'
