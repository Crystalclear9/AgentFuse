# 项目内工具与清理

Windows 新终端在仓库根目录运行 `. ./scripts/enter-local.ps1`，WSL Bash 运行 `source scripts/enter-local.sh`。两者在当前会话使用项目内环境。

| 用途 | 项目内位置 |
|---|---|
| Windows Python 3.11 与开发依赖 | `.tools/python`、`.venv-local` |
| WSL Python 3.11 与开发依赖 | `.tools/python-linux`、`.venv-wsl` |
| 安装与锁文件同步 | `.tools/uv`、`.tools/uv-linux`、`requirements-dev.lock` |
| Git 和源码检索 | `.tools/git`、`.tools/ripgrep` |
| 浏览器端到端测试 | `.tools/browsers`，Node 随 Playwright 驱动安装在虚拟环境内 |
| 可选 Docker 容器命令 | `.tools/docker` 提供客户端，Docker Engine 由系统提供 |

首次安装或恢复默认环境使用 `scripts/setup-local.ps1`，WSL 使用 `bash scripts/setup-local.sh`。虚拟环境含绝对路径，迁移项目后需要重建。Python 3.12 和 3.13 的本地兼容性环境已清理，需要时运行 `scripts/setup-local.ps1 -AllPythonVersions` 恢复。远端 CI 继续运行完整版本矩阵。

本地模型、可选框架 SDK 和 Linux 验收镜像按需安装。复现 GPU、KV Cache 和容器专项时，按 [P2 验收说明](P2_COMPLETION.md) 运行 `scripts/setup_p2.py`。框架依赖锁定在 `requirements-p2.lock`。Windows、WSL2、Docker Engine 和 GPU 驱动由系统提供。

## 文件保留规则

- 源码、测试、配置、依赖锁、原始需求、性能基线和关键验收摘要随 Git 保存。
- 本地数据库、备份、工作区、运行结果和正式交付包由各自的目录管理。清理临时文件时保护这些路径。
- 测试日志、临时工作区、缓存和新报告保存在被 Git 忽略的 `.agentfuse/` 或工具缓存目录。仓库中的关键结果见 [验收证据索引](verification/README.md)。

`python scripts/acceptance_check.py` 每次使用独立的临时工作区，运行结束自动清理，保留 `.agentfuse/reports/acceptance.json` 结果。

`python scripts/clean_intermediates.py --plan <清单>` 检查显式路径清单。添加 `--apply` 后执行并写入 `.agentfuse/verification/cleanup.json`。只读重试会先检查符号链接、重解析点和硬链接，避免通过别名修改其他文件的权限。删除失败时尝试移入 `_manual_delete`，无法移动的锁定路径记录于该目录的清单。

`python scripts/finalize_report.py --reports <原始证据目录>` 验证发布所需的测试证据，默认将汇总写到 `.agentfuse/verification/current`。原始文件留在输入目录。
