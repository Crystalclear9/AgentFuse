# 预测统计隔离复核

PathPredictor 的工具预测与 Agent 路径预测原来共用统计表。同名工具与 Agent 标签会污染两类概率，并把工具转移误用于 Agent 路径。现在采用两张独立、有容量上限的统计表。Agent 路径仅学习显式父子 Invocation 边。新增两项回归，修复前均失败、修复后通过。

本地完整 347 项：345 passed / 2 skipped；Docker/TLS、快照及预测专项 38 passed；审计 325 项零失败；验收 44/44；原基线、原容差、五次性能守卫通过。wheel 安装后的预测行为通过，99 个运行时/资源文件与源码一致。

原始证据见 verification/prediction-review-2026-11-05。跨平台矩阵、变异与稳定性由本次提交的远端 CI 执行，最终状态保存在本地 .agentfuse/dist/delivery.json。清理结果见 cleanup.json，必要工具和正式数据保留。

全部 P2 仍未完成，支持边界见 KNOWN_LIMITATIONS.md。
