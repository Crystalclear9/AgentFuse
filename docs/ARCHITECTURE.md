# Architecture

## 1. 模块图

```text
                  Multi-Agent Framework  (外部)
                           |
                           v
                   Controlled Adapter
              agentfuse/adapters/base.py
                           |
                           v
             Existing Workflow Middleware
              (Agent 身份 / 调用关系 / 结果传递)
                           |
              +------------+------------+
              |                         |
              v                         v
        Event Collector           Runtime Controller
   core/events.py  core/execution_graph.py      runtime/coordinator.py
              |                         |
              v                         v
        Execution Graph           Share Coordinator
                                  sharing/share_group.py
                                  sharing/candidate.py
                                  sharing/dependency.py
                                  sharing/validator.py
                                  sharing/result_delivery.py
                                          |                |
                                          v                v
                                  Dependency         Resource
                                   Validator          Scheduler
                             sharing/validator.py  runtime/scheduler.py
                                          |                |
                                          +-------+--------+
                                                  |
                                                  v
                                            Tool Executor
                                            runtime/worker.py
                                            runtime/worker_child.py
                                                  |
                                                  v
                                         Local CPU / Memory
                                                  |
              +---------------+----------------------+
              |                                      |
              v                                      v
        SQLite Storage                         Local Web Dashboard
     storage/database.py                     api/app.py + web/
     storage/repository.py
```

## 2. 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `core/events.py` | 统一事件协议、全局唯一 event_id、workflow 内稳定序号、订阅分发 | 事件语义解释 |
| `core/execution_graph.py` | 从事件增量重建执行图（节点 + 边） | 推断未被观察到的关系 |
| `core/workflow.py` | Workflow 生命周期、Agent 身份绑定、Invocation 管理 | 工具执行 |
| `core/state.py` | 全部状态词汇表（唯一合法枚举） | 状态转换逻辑 |
| `adapters/base.py` | 外部框架接入的最小接口契约 | 任何具体框架假设 |
| `adapters/scripted_adapter.py` | 离线 Scripted Agent 的适配器与执行上下文 | LLM 能力 |
| `runtime/coordinator.py` | 共享状态的唯一权威；物理执行生命周期；取消与失败处理 | 具体工具实现 |
| `runtime/worker.py` / `worker_child.py` | 独立进程执行工具、流式上报依赖 | 共享决策 |
| `runtime/scheduler.py` | **LocalResourceController**：FIFO / SJF 准入、Worker 上限、内存准入、资源快照 | 硬性资源限制 |
| `runtime/strategy.py` | 调度策略接口与实现（`fifo` / `sjf`） | 业务策略 |
| `runtime/pool.py` | Worker 句柄与预热池 | 任务分发 |
| `runtime/execution.py` | 物理执行与执行结果的数据模型 | 共享决策 |
| `runtime/cancellation.py` | 取消 / 超时 / 关闭时的进程终止与回收 | 状态转换 |
| `sharing/result_cache.py` | 会话内已完成结果复用（默认关闭） | 跨会话缓存 |
| `sharing/candidate.py` | CandidateKey 计算 | 证明结果等价 |
| `sharing/share_group.py` | 共享组与参与者状态机（含非法转换检查） | 具体验证逻辑 |
| `sharing/dependency.py` | 只增依赖账本 | 依赖语义解释 |
| `sharing/validator.py` | 在另一个快照上重算依赖指纹并比对 | 决定是否共享 |
| `sharing/result_delivery.py` | 结果落盘、内容哈希、幂等索引 | 分发时机 |
| `workspace/snapshots.py` | 不可变快照、分支 fork、manifest、完整性校验 | 写时复制 / 去重 |
| `workspace/reader.py` | 受控读取接口：路径规范化、边界校验、指纹、即时上报 | 安全沙箱 |
| `workspace/fingerprint.py` | **唯一**的指纹实现（父子进程共用） | 策略判断 |
| `tools/*` | 4 个真实只读确定性工具 + 1 个故障注入测试夹具 | 任意程序执行 |
| `storage/*` | SQLite schema、后台写入线程、仓储查询 | 业务逻辑 |
| `monitoring/*` | 指标采集、时间线构建、依赖检查 | 采集频率之外的调度 |
| `api/*` | 本地 HTTP 接口 | 业务决策 |
| `web/*` | 中文管理界面 | 任何假数据 |

## 3. 事件流

一次典型的共享执行会产生以下事件序列（真实顺序由运行决定）：

```text
WORKFLOW_STARTED
AGENT_BOUND            (每个 Agent 一次)
AGENT_INVOKED          (每次调用一次)
TOOL_CALL_REQUESTED    (每个逻辑调用一次)
SHARE_CANDIDATE_FOUND  (发现潜在共享候选时)
TOOL_CALL_WAITING      (进入共享等待)
TOOL_CALL_RETRYING     (失败后进入唯一一次独立重试)
TOOL_EXECUTION_STARTED (物理执行启动)
TOOL_READ_OBSERVED     (每条依赖一次，读取即刻上报)
SHARE_VALIDATION_PASSED / SHARE_VALIDATION_FAILED
SHARE_PARTICIPANT_DETACHED
TOOL_EXECUTION_FINISHED
TOOL_RESULT_DELIVERED  (每个逻辑调用一次，仅由 Runtime 发布)
FRAMEWORK_RESULT_RECEIVED (框架侧回传边界，适配器专用)
RESOURCE_LIMIT_WARNING (实测 RSS 超过软限制)
AGENT_RETURNED
WORKFLOW_FINISHED
```

> `TOOL_RESULT_DELIVERED` 只由 Runtime 发布一次。适配器的 `deliver_result`
> 使用独立事件类型 `FRAMEWORK_RESULT_RECEIVED`，因此外部框架回传结果时
> **不会**产生第二条交付事件。

事件写入 SQLite 由 `AgentFuseApp._persist_event` 订阅 `EventBus` 完成，
写入通过队列交给独立线程，**不会阻塞事件循环**。

## 4. 执行图模型

### 节点

| kind | 标识 | 含义 |
|---|---|---|
| `workflow` | `workflow:<id>` | 一次完整的多智能体工作流实例 |
| `agent` | `agent:<id>` | 逻辑 Agent 实体 |
| `invocation` | `invocation:<id>` | Agent 的一次实际激活 |
| `logical_call` | `call:<id>` | 一次逻辑工具请求 |
| `physical_execution` | `execution:<id>` | 一次真实启动的工具计算 |
| `tool_result` | `result:<hash>` | 一次结果交付 |

### 边

| kind | 含义 | 来源事件 |
|---|---|---|
| `AGENT_INVOKES` | Agent → Invocation | `AGENT_INVOKED` |
| `CALLS_TOOL` | Invocation → LogicalToolCall | `TOOL_CALL_REQUESTED` |
| `BOUND_TO_EXECUTION` | LogicalToolCall → PhysicalExecution | `TOOL_EXECUTION_STARTED` |
| `EXECUTION_PRODUCES_RESULT` | PhysicalExecution → ToolResult | `TOOL_RESULT_DELIVERED` |
| `RESULT_DELIVERED_TO` | ToolResult → LogicalToolCall | `TOOL_RESULT_DELIVERED` |
| `SHARES_EXECUTION_WITH` | LogicalToolCall → PhysicalExecution | `TOOL_CALL_WAITING` |
| `EXPLICIT_INVOCATION_DEPENDENCY` | Invocation → Invocation | `AGENT_INVOKED` 的 `parent_invocation_id` |

**图中不存在任何推测出来的边。** 没有观察到调用边界，就没有 `CALLS_TOOL`；
没有观察到执行启动，就没有 `BOUND_TO_EXECUTION`；时间接近、参数相似、名称相似
都不会产生任何边。

## 5. Runtime 控制流程

```text
submit_tool_call
   │
   ├─ 计算 CandidateKey = H(tool ‖ version ‖ canonical(args) ‖ env_signature)
   │
   ├─ 查 active_groups[candidate_key]（最新 → 最旧）
   │     ├─ 命中且账本前缀全部 MATCH  → 加入共享组（SHARED_WAIT）
   │     ├─ 命中但存在 MISMATCH/UNKNOWN → 记录验证失败，继续尝试下一个组
   │     └─ 无可用组                    → 创建新组并成为 Leader
   │
   └─ 等待结果
         │
         ├─ 在线验证：Leader 每读一个新依赖 → 立即在每个参与者快照上重算指纹
         │              MATCH  → 继续保持等待
         │              MISMATCH/UNKNOWN → 立即 DETACH → 独立执行
         │
         ├─ Leader 完成 → 冻结账本 → 最终提交验证
         │              参与者 validated_prefix == len(ledger) 且全部 MATCH
         │                 → 交付共享结果
         │              否则 → 分离并独立执行
         │
         └─ 失败/超时/取消 → 按状态机处理（见 EXECUTION_MODEL.md）
```

所有共享状态转换都在 `RuntimeCoordinator._lock` 这一把 `asyncio.Lock` 下完成，
因此 join / detach / deliver / cancel / release 不会交错出中间状态。

## 6. 数据持久化

SQLite（`agentfuse/storage/database.py`），schema 版本记录在 `PRAGMA user_version`
与 `schema_meta` 表中。当前版本 **v2**。

* `initialize()` 先幂等地建表，再按 `user_version` 顺序执行迁移
  （`_MIGRATIONS`：`(目标版本, 语句列表)`）；
* 迁移前自动备份数据库到 `<home>/backups/`，迁移在事务中执行；
* `ALTER TABLE ... ADD COLUMN` 会先检查列是否已存在，因此重复初始化是安全的。

| 表 | 内容 |
|---|---|
| `workflows` | 工作流实例与配置 |
| `agents` | Agent 身份绑定 |
| `agent_invocations` | 调用记录与父子关系 |
| `logical_tool_calls` | 逻辑调用、状态、绑定的物理执行、结果哈希 |
| `physical_executions` | 物理执行、worker pid、CPU 时间、峰值 RSS、退出状态 |
| `share_groups` | 共享组、Leader、`participant_count` 与 `participant_calls`（组内的逻辑调用列表） |
| `share_participants` | 参与者状态、已验证前缀、分离原因、等待时长 |
| `workspace_snapshots` | 快照元数据与 manifest 哈希 |
| `dependency_records` | 每个物理执行的依赖账本 |
| `validation_records` | 每一次真实验证检查的结果与耗时 |
| `runtime_events` | 全部运行时事件 |
| `resource_samples` | 资源采样序列 |
| `tool_results` | 结果文件路径、哈希与大小 |
| `benchmark_runs` | 基准测试记录 |

写入路径：运行时 → `Database.write()` → 有界队列 → 独立写入线程 → 事务提交。
读取路径：查询前先 `flush()`，保证读到的数据包含已经产生的写入（read-your-writes）。

### 崩溃恢复

Runtime 启动时 `Repository.recover_interrupted_workflows()` 会把
`status=RUNNING` 且 `end_time IS NULL` 的工作流标记为 `INTERRUPTED`，
并把对应的物理执行与逻辑调用一并标记为 `INTERRUPTED`。
中断的运行**永远不会被误判为完成**，用户可以重新发起运行。

大型工具输出不塞进数据库：结果写入 `.agentfuse/results/<workflow_id>/<hash>.json`，
数据库只保存路径、哈希与大小。

## 7. 进程模型

```text
主进程 (asyncio)
  ├─ EventBus
  ├─ RuntimeCoordinator ── ShareGroup 状态机
  ├─ ResourceScheduler  ── FIFO + 并发上限 + 内存准入
  ├─ 数据库写入线程
  ├─ psutil 采样线程
  └─ worker 读取线程 (每个执行一个)
        │
        v
  worker 进程  python -m agentfuse.runtime.worker_child
        ├─ 读取 payload (JSON 行, stdin)
        ├─ 工具线程 ──> 依赖事件 ──> 队列 ──> stdout (JSON 行)
        ├─ 结果 / 错误消息 ──> stdout
        └─ 等待下一条 payload（预热池模式：进程被复用）
```

worker 以普通子进程方式启动（而非 `multiprocessing`），因此：

* worker 不会重新导入宿主进程的 `__main__` 模块（对库使用者更安全）；
* 协议是人类可读的 JSON 行，便于调试；
* 进程树可以被可靠地终止（psutil 递归 kill 子进程）并被回收（`wait()`）。

### 8.1 Worker 预热池

`runtime.worker_pool_size`（默认 2）控制预启动的 worker 进程数量。
Runtime 启动时在后台线程里把它们拉起来（`RuntimeCoordinator.prewarm_workers`），
因此解释器启动与包导入发生在**空闲期**而不是任何一次调用的关键路径上。

实测：worker 冷启动 225 ms → 99 ms（惰性包导出），
4 次顺序执行 712 ms → 167 ms（预热池）。

worker 本身是"多任务"进程：它按行读取 payload，执行完一个任务后继续等待下一个。
`runtime/pool.py` 的 `WorkerHandle` 负责把协议流里的消息归属到**当前**任务。

### 8.2 惰性包导出

`agentfuse.core`、`agentfuse.workspace`、`agentfuse.runtime` 三个包使用
:pep:`562` 的 `__getattr__` 惰性导出。worker 只需要标识与指纹工具，
不应该为此付出 `asyncio`（~55 ms）、`sqlite3` 与整个协调器的导入代价。

## 8. 与现有多智能体中间层的关系

AgentFuse 不实现 Agent 身份绑定与工作流重建逻辑，它**消费**这些事实：

* 上游中间层负责把外部调用边界转换成 `RuntimeEvent`；
* AgentFuse 负责工具执行接管、候选共享组、物理调度、依赖验证、资源控制与结果分发。

对接点就是 `agentfuse/adapters/base.py` 的六个方法
（`bind_agent` / `begin_invocation` / `submit_tool_call` / `deliver_result` /
`end_invocation` / `record_event`）。详见 `ADAPTER_GUIDE.md`。
