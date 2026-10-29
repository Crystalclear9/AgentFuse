# Examples

可独立运行的离线演示脚本。它们通过**与真实框架完全相同的适配器接口**进入
AgentFuse，Runtime 中不存在 Demo 专用旁路。

| 脚本 | 内容 |
|---|---|
| `shared_execution/main.py` | 两个 Agent 请求同一份 AST 分析 → 只做一次真实计算 |
| `divergent_execution/main.py` | 两个分支内容不同 → 在线检测冲突 → 分离 → 各自独立执行 |
| `multi_agent_workflow/main.py` | Supervisor → 三个 Worker → Aggregator，并打印由事件重建的执行图 |

运行方式：

```bash
python examples/shared_execution/main.py --mode online
python examples/divergent_execution/main.py --mode online
python examples/multi_agent_workflow/main.py --mode online
```

`sample_repository/` 目录只保留一个说明文件 —— 示例仓库的真实副本位于
`agentfuse/resources/sample_repository/`，随包安装，保证离线可用。

等价的命令行入口：

```bash
agentfuse demo --scenario shared --mode online
agentfuse demo --scenario diverged --mode online
agentfuse demo --scenario multi-agent --mode online
```
