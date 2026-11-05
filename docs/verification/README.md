# 验收证据索引

仓库保留最近一次完整验证的摘要、可比较的性能基线，以及需要 GPU 或容器才能复现的专项证据。

| 内容 | 入口 |
|---|---|
| 当前验证结果与最近一次含可选框架的完整证据 | [测试报告](../TEST_REPORT.md) · [latest.json](latest.json) |
| 2026-11-04 性能基线实测 | [汇总](baseline-2026-11-04/summary.json) · [原始性能报告](baseline-2026-11-04/performance.txt) |
| P2 模型、GPU 和原生审计实测 | [验收说明](../P2_COMPLETION.md) · [原始专项汇总](p2-completion-2026-11-05/summary.json) |
| 较早的完整 JUnit、失败日志和复核记录 | [清理前的 Git 版本](https://github.com/Crystalclear9/AgentFuse/tree/d1e10a5e73c07b00cb977244ed763e57f8ea83d1/docs) |

本地验收、清理和报告生成的产物放在 `.agentfuse/`。CI 日志和产物随各自的 GitHub Actions 运行保留。
