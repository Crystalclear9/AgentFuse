# 项目内工具与清理

本轮验证使用的工具全部位于本项目内，没有修改系统 PATH 或用户 Shell 配置。

| 工具 | 项目内位置 |
|---|---|
| Python 3.11.18（Windows） | `.tools/python` |
| Windows 项目和开发依赖 | `.venv-local` |
| Python 3.12.16 / 3.13.18 兼容性验证 | 基础解释器在 `.tools/python`，独立依赖环境在 `.tools/envs/py312`、`.tools/envs/py313` |
| Python 3.11.18（WSL）及依赖 | `.tools/python-linux`、`.venv-wsl` |
| uv 0.13.7 | `.tools/uv`、`.tools/uv-linux` |
| Portable Git 2.56.0.windows.4 | `.tools/git` |
| ripgrep 15.2.0（源码检索） | `.tools/ripgrep/rg.exe` |
| Node 24.22.1 | `.venv-local/Lib/site-packages/playwright/driver/node.exe`；Linux 同包的 driver 目录 |
| Chromium 156 与浏览器辅助工具 | `.tools/browsers` |
| Docker CLI 28.5.1 | `.tools/docker/docker.exe` |
| 已验证的离线 Docker 镜像 | `.tools/images/python-3.11-alpine.tar` |

Windows 新终端运行 `. ./scripts/enter-local.ps1`，WSL Bash 运行 `source scripts/enter-local.sh`。
首次恢复 Python、开发依赖和浏览器可运行 `scripts/setup-local.ps1`，WSL 使用 `bash scripts/setup-local.sh`。
完整 Windows Python 版本矩阵可运行 `scripts/setup-local.ps1 -AllPythonVersions`，所有解释器与依赖仍安装在项目内。
Python 依赖版本和哈希锁定在 `requirements-dev.lock`；已验证可执行文件的 SHA-256 见 `verification/current/toolchain.json`。
工具与虚拟环境较大，不提交 Git。远端源码提供安装脚本和依赖锁；本机交付目录保留已安装工具。
虚拟环境包含绝对路径，移动整个项目后应重新运行安装脚本。

操作系统、PowerShell、WSL2 和 Docker Engine 是系统前提。Docker 客户端和镜像可放在项目内，
Engine 的系统服务、内核能力与虚拟磁盘不能靠复制可执行文件在普通目录内安装。
本轮 Docker 测试使用现有 Linux Engine；测试结束后已停止本次启动的 Docker Desktop。
恢复镜像可用 `.tools/docker/docker.exe load -i .tools/images/python-3.11-alpine.tar`。
本轮按用户要求补齐：`.tools/llama` 为固定版本 llama.cpp Vulkan 后端，`.tools/models` 为哈希固定的预训练 GGUF 模型，`requirements-p2.lock` 锁定 LangGraph 依赖。安装与验证见 P2_COMPLETION.md。

清理通过 `scripts/clean_intermediates.py` 的显式路径清单执行；它先校验路径位于项目内，
独立保护源码、Git、已安装工具、活动环境及工作区/数据库/备份/结果/日志。
删除失败的残留尝试转入 `_manual_delete`；若 Windows 同时禁止删除和重命名，清单如实记录原路径。
实际结果见 `verification/cleanup.json`。测试原始证据保留于 `verification/current`，不把失败历史当作无效文件删除。
