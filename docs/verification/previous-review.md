<!-- current-verification:start -->
## 当前版本实测（2026-11-04）

| 检查 | 结果 |
|---|---|
| 完整 pytest，含真实浏览器 | 303 passed, 2 skipped；0 failed |
| Docker 隔离与扩展定向验证（独立执行） | 15 passed in 5.75s |
| 交付验收 | 44/44 |
| 需求审计 | 325/325；110 behavioural / 215 structural |
| KV 前缀复用 | 输出逐值一致，计算步数 72 → 8 |
| 性能回归守卫，重复 5 次 | 未通过：3 项取消场景时延超过旧基线；原始记录保留 |

命令：`.venv/Scripts/python.exe -m pytest tests -q -rs -x --junitxml=.agentfuse/reports/final-suite.xml`。
完整测试环境变量：`AGENTFUSE_BROWSER_TEST=1`。Docker 已单独完成验证，完整复测时不启动引擎。
Docker 定向测试环境变量：`AGENTFUSE_SANDBOX_TEST_IMAGE` 为本地不可变 Python Linux 镜像摘要。
镜像：`python@sha256:09d17d5e4c16d0a7a1eea690bcb7c6ab8616b211f8421e429e97c1bef5bd0ed2`。
跳过原因：set AGENTFUSE_SANDBOX_TEST_IMAGE to a local immutable Linux image；symlinks are not supported on this platform。
运行环境：Windows-10-10.0.26100-SP0；Python 3.11.5。
代码/测试快照 SHA-256：`c295238ef30c3cecd1c9e51ba1a12469a62f764998adf98359a982a4fb4ed104`。

本轮未重新验证 WSL2/macOS、跨物理机与真实 TLS 部署、生产 LLM/GPU 推理。
structural 只证明接口/字段/文件/测试引用存在；behavioural 执行真实操作并断言结果。
审计与单元测试通过不代表所有 P2 均完成，具体剩余边界见 KNOWN_LIMITATIONS 和 P2_EXTENSIONS。
可复核原始证据：[summary](verification/summary.json)、[JUnit](verification/suite.xml)、
[audit](verification/audit.json)、[acceptance](verification/acceptance.txt)、[inference](verification/inference.json)。
此块由 `scripts/finalize_report.py` 从真实产物生成。

首次完整运行为 300 passed / 1 failed / 1 skipped：迟到加入测试的固定 CPU 时间窗口在负载下失效。
修复为显式结果处理屏障后，定向测试通过，再完整复测；不降低原来的共享次数或前缀校验断言。
第二次完整运行为 299 passed / 2 failed / 2 skipped：取消演示使用固定 50ms 延迟和旧事件，
另一个测试假设 Windows 250ms 内必能发现子进程。取消改用对应 Invocation 的真实请求事件；
终止测试主动制造早期发现缺失，再验证权威 hello PID 的真实父子关系和原进程退出。
Runtime 日志句柄增加所有权计数，最后一个使用者关闭时释放；进程监测先筛 Python 再读命令行。
前几次性能守卫报告失败；首次与全量测试并行，另发现固定测量 home 已累积 3642 个 Workflow、6030 个快照，SQLite 达 88.2 MiB。
将性能守卫改为每场景使用独立临时 home，并关闭该目录的日志句柄以便 Windows 清理；保留旧历史数据。
完整测试后，性能守卫进一步改为每场景使用独立解释器，避免前一场景的线程、GC 和进程状态影响下一场景；
这一脚本改动单独复测全部 9 项守卫单元测试（guard-final.xml），应用 Runtime 未再改动。
停止本次启动的 Docker 后独立复测，没有更新基线或扩大容差来让守卫通过。
最终 3 次复测的取消场景 Baseline 中位数 4.279225 秒，比容差上限高约 5ms，标准差为 0.274291 秒；
保留该失败记录，并与基线使用相同的 5 次重复设置进行最终测量。
最终独立进程测量中，多 Agent 等场景满足原守卫，取消场景在三种模式下仍高于旧参考；
该项性能告警尚未消除，不能将本报告解读为所有交付门禁均为绿色。功能测试与性能门禁分别列出。
<!-- current-verification:end -->