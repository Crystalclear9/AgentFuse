# Execution Model

本文档定义 AgentFuse 的执行实体、状态机与失败语义。

---

## 1. Workflow

一次完整的多智能体工作流执行实例。

| 字段 | 说明 |
|---|---|
| `workflow_id` | 全局唯一 |
| `scenario` | 场景名（shared / divergent / multi-agent …） |
| `start_time` / `end_time` | Unix 时间戳 |
| `status` | `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` / `INTERRUPTED` |
| `configuration` | 运行配置（JSON） |
| `runtime_mode` | `baseline` / `online` / `deferred` |
| `random_seed` | 随机种子（供可复现实验使用） |

一个 Workflow 包含多个 Agent，Agent 与 Agent 之间没有直接依赖关系，
依赖只通过 Invocation 表达。

## 2. Agent

逻辑 Agent 实体。

| 字段 | 说明 |
|---|---|
| `agent_id` | 内部标识，由运行时在**观测到的注册边界**上创建 |
| `workflow_id` | 所属工作流 |
| `external_identity` | 外部框架提供的身份，可为空 |
| `agent_description` | 描述 |
| `registration_source` | 注册来源（例如 `scripted-agent`） |
| `metadata` | 扩展信息 |

> 外部框架没有稳定 `agent_id` 时，内部标识依然稳定。
> AgentFuse **不会**通过文本相似度或名称相似度猜测两个事件属于同一个 Agent；
> 无法确认时保持"身份未确定"，绝不合并。

## 3. AgentInvocation

某个 Agent 的一次实际激活。

| 字段 | 说明 |
|---|---|
| `invocation_id` | 全局唯一 |
| `agent_id` | 所属 Agent |
| `parent_invocation_id` | 显式的调用依赖（例如 Supervisor → Worker） |
| `start_time` / `end_time` | 时间戳 |
| `status` | `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` |

同一个 Agent 会出现多次 Invocation。三种概念严格区分：

```text
Agent 实体  ≠  Agent 的一次调用  ≠  Agent 发起的工具调用
 agent_id        invocation_id          logical_call_id
```

## 4. LogicalToolCall

Agent 发起的一次**逻辑**工具请求。

| 字段 | 说明 |
|---|---|
| `logical_call_id` | 全局唯一 |
| `workflow_id` / `invocation_id` / `agent_id` | 归属 |
| `tool_name` / `tool_version` | 工具与版本 |
| `normalized_arguments` | 规范化参数（canonical JSON） |
| `workspace_snapshot_id` | 该调用所基于的快照 |
| `request_time` | 发起时间 |
| `status` | 见下表 |
| `physical_execution_id` | 绑定的物理执行 |
| `result_reference` / `result_hash` | 结果文件与内容哈希 |
| `candidate_key` | 候选共享键 |
| `share_group_id` | 所属共享组 |

状态机：

```text
PENDING ──► WAITING ──► SHARED_WAIT ──► COMPLETED
   │            │             │
   │            │             ├──► DETACHED ──► RUNNING_INDEPENDENTLY ──► COMPLETED
   │            │             └──► FAILED / CANCELLED
   │            └──► RUNNING_INDEPENDENTLY ──► COMPLETED / FAILED
   └──► FAILED / CANCELLED
```

**两个 Agent 共享同一次物理执行时，它们依然拥有各自独立的 LogicalToolCall。**

## 5. PhysicalExecution

系统真正启动的一次工具计算。

| 字段 | 说明 |
|---|---|
| `execution_id` | 全局唯一 |
| `tool_name` / `tool_version` | 工具与版本 |
| `arguments_hash` | 规范化参数哈希 |
| `candidate_key` | 候选共享键 |
| `source_snapshot_id` | 执行所基于的快照（Leader 的快照） |
| `worker_pid` | worker 进程 pid |
| `start_time` / `finish_time` | 时间戳 |
| `execution_status` | `CREATED`/`QUEUED`/`RUNNING`/`COMPLETED`/`FAILED`/`TIMEOUT`/`CANCELLED`/`CRASHED` |
| `cpu_time` | worker 自测的用户态+内核态 CPU 时间（秒） |
| `peak_rss` | psutil 采样到的进程树峰值 RSS（字节） |
| `exit_status` | worker 退出码 |
| `result_hash` | 结果内容哈希 |

一个 PhysicalExecution 可以对应多个 LogicalToolCall —— 这是 AgentFuse
最重要的数据关系之一。

## 6. Tool Executor

工具在**独立操作系统进程**中执行：

```bash
python -m agentfuse.runtime.worker_child
```

* payload 通过 stdin 传入（JSON）；
* 依赖事件与结果通过 stdout 以 JSON 行流式返回；
* 依赖事件在**读取发生的瞬间**产生，因此协调者可以在线验证；
* 工具读取必须经过 `WorkspaceReader`，绕过的工具一律禁止共享。

## 7. Resource Scheduling

首版采用简单稳定的 FIFO 物理执行调度。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `max_workers` | 2 | 同时运行的 worker 进程上限 |
| `max_memory_budget_mb` | 1024 | **准入**用的内存估算预算 |
| `default_timeout_seconds` | 60 | 单次执行超时 |
| `resource_sample_interval_ms` | 50 | RSS 采样间隔 |
| `max_rss_soft_limit_mb` | 0（关闭） | 实测 RSS 软限制，超出则阻止新准入并告警 |

调度规则：

1. 请求进入等待队列，由**调度策略**决定下一个放行哪个；
2. 当"运行数 < max_workers"且"已预留估算 + 本次估算 ≤ 预算"时放行；
3. 预算被实测 RSS 超出时，**阻止新任务进入**并告警（不是硬限制）；
4. 单个超预算的工具在无其它运行时仍会被放行，避免死锁；
5. 多个逻辑调用共享一个物理执行时，**只占用一个执行槽位**；
6. 等待队列中的每个执行都带有**等待原因**（Worker 上限 / 内存预算 / 实测 RSS 超限），
   通过 `/api/resources` 与 Web 界面暴露。

### 调度策略

准入策略是可插拔的（`runtime/strategy.py`），由 `runtime.scheduling_strategy` 选择：

| 策略 | 行为 | 取舍 |
|---|---|---|
| `fifo`（默认） | 严格先进先出，最老的请求先放行 | 公平、可预测 |
| `sjf` | 等待队列中**声明 CPU 成本最小**的请求先放行 | 吞吐（goodput）更高，长任务可能被无限推迟 |

策略只决定"下一个放行谁"，不改变共享语义与正确性保证。

CPU 时间与内存的语义区别见 `KNOWN_LIMITATIONS.md`。

## 8. Cancellation

| 场景 | 行为 |
|---|---|
| Follower 取消 | 移除等待关系，保留其它参与者，物理执行继续 |
| Leader 的逻辑调用取消 | 物理执行继续（Leader 不拥有物理执行的独占所有权），其余参与者照常等待；被取消的调用不会收到成功交付 |
| 全部参与者取消 | 终止物理执行，释放资源，记录取消，清理共享组 |
| 超时 | 终止 worker 进程树，执行标记为 `TIMEOUT` |

取消是幂等的：重复取消同一个调用不会产生第二次事件。

## 9. Failure Recovery

三类失败被严格区分：

| 类型 | 触发条件 | 重试 | 结果 |
|---|---|---|---|
| 工具业务错误 `tool_error` | 工具实现抛出异常 | **不重试** | 逻辑调用 `FAILED` |
| 工作进程崩溃 `worker_crash` | worker 非零退出 / 没有返回结果 | **重试一次** | 首次失败后独立重试；再次失败则 `FAILED` |
| Runtime 调度错误 `internal_error` | 运行时自身问题（例如工具未注册） | 不重试 | 逻辑调用 `FAILED` |

共享组中的失败传播：

* 共享组的物理执行失败时，**所有**等待参与者都会收到明确的状态变化；
* **不会**把部分结果当作成功；
* 允许参与者进入独立重试队列**一次**，防止无限重试；
* 失败原因写入 `logical_tool_calls.error` 与 `physical_executions.error`。

Runtime 进程中断：

* 启动时 `Repository.recover_interrupted_workflows()` 把所有
  `status=RUNNING` 且 `end_time IS NULL` 的工作流标记为 `INTERRUPTED`，
  并把对应的 `physical_executions`（状态 CREATED/QUEUED/RUNNING）与
  `logical_tool_calls`（状态 PENDING/WAITING/SHARED_WAIT/RUNNING_INDEPENDENTLY/DETACHED）
  一并标记为 `INTERRUPTED`；
* 不存在的 Worker 状态随之被清理（worker 进程随父进程一起消失）；
* 中断状态不会被误判为完成；
* SQLite 采用 WAL + 事务，普通异常不会损坏数据库。
