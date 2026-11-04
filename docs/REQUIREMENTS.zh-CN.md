# AgentFuse：面向多智能体工作流的共享执行与资源管理 Runtime

## 一、项目总目标与开发角色

你是一名高级系统软件工程师和 AI Systems 研究工程师，具备以下能力：

- Python 高性能异步与并发编程。
- Multi-Agent Framework 与 Agent Runtime 设计。
- 工作流执行图、事件追踪与因果关系建模。
- 操作系统进程管理和资源调度。
- 文件系统版本管理与动态依赖分析。
- 数据库事务、状态一致性和故障恢复。
- FastAPI、SQLite、psutil 和本地 Web 应用开发。
- 系统性能分析、Benchmark 设计和自动化测试。

你的任务是直接开发一个名为 **AgentFuse** 的完整系统。

AgentFuse 是一个面向多智能体工作流的本地执行管理 Runtime。

它位于多智能体框架与实际工具执行环境之间，通过受控接入的调用边界观察 Agent 的真实执行过程，并进一步进行工具执行共享、动态依赖验证和资源管理。

系统需要实现以下核心能力：

1. 根据真实运行事件，在线维护多智能体工作流的执行关系。
2. 识别不同 Agent 之间潜在的重复工具执行。
3. 在不同执行分支和工作区状态下，动态判断计算结果是否能够安全共享。
4. 对符合共享条件的多个逻辑调用，仅执行一次实际计算。
5. 当发现输入依赖不一致时，及时将相关调用从共享执行中分离。
6. 管理本地 CPU、内存和工作进程并发。
7. 记录逻辑调用、物理执行、共享关系、依赖验证和资源消耗。
8. 提供真实可交互的本地 Web 管理界面。
9. 提供不依赖外部模型的本地多智能体工作流演示。
10. 通过真实 Benchmark 验证共享执行的正确性及性能收益。

最终交付一个可以独立安装、启动、运行、监控和测试的单机软件。

**本项目是一个真正执行计算并管理计算资源的 Runtime，不是纯粹的 Trace Viewer、工作流可视化工具、Agent 聊天应用或者模拟数据展示程序。**

---

# 二、不可违反的开发约束

## 2.1 运行环境

目标环境：

- 单台 Windows 电脑。
- Python 3.11 或以上。
- 优先原生 Windows 运行。
- 如部分能力依赖 Linux，可以通过 WSL2 提供可选支持。
- 无需真实 GPU 集群。
- 无需云计算服务。
- 无需额外服务器。

核心技术栈：

| 部分 | 推荐技术 |
|---|---|
| Runtime 核心 | Python |
| 并发调度 | asyncio |
| 计算进程 | multiprocessing / subprocess |
| 资源监控 | psutil |
| 数据存储 | SQLite |
| 本地 API | FastAPI |
| Web 服务 | Uvicorn |
| 前端 | HTML、CSS、JavaScript |
| 自动化测试 | pytest |
| 配置 | TOML / JSON |
| 命令行 | argparse 或 Typer |

允许合理调整技术栈，但不得为了框架复杂度牺牲交付质量。

前端不要求 React、Vue 或复杂构建系统。优先使用不需要额外 Node.js 构建链的轻量实现。

## 2.2 完全本地运行

必须满足：

- 不调用 OpenAI、Anthropic、Google 或其他模型 API。
- 不要求任何 API Key。
- 不依赖外部 SaaS。
- 不要求在线数据库。
- 核心功能不需要网络连接。
- 安装依赖以后可以完全离线运行。
- Web 管理界面默认仅监听 `127.0.0.1`。
- 不通过 CDN 加载前端资源。

本地 FastAPI 接口属于软件内部通信，不属于外部服务依赖。

## 2.3 开发周期

目标为一个月左右完成完整首版。

因此必须避免：

- 实现完整 Agent Framework。
- 训练自己的语言模型。
- 自己实现大语言模型推理引擎。
- 开发大型分布式调度系统。
- 实现 GPU KV Cache 管理。
- 开发复杂的容器或 microVM 基础设施。
- 实现任意 Python 程序的完备动态依赖追踪。
- 实现通用操作系统级安全沙箱。
- 开发复杂的多用户权限系统。
- 为了视觉效果制作过大的前端工程。

首版只支持受控注册的确定性、只读本地工具。

必须将核心技术集中在：

**真实执行图 + 在线依赖验证 + 共享执行 + 资源控制。**

---

# 三、与现有多智能体中间层的关系

## 3.1 不重新实现已有系统

本项目需要尽量复用已有的多智能体运行图重建中间层。

现有系统的基本研究方向为：

在 Multi-Agent Framework 与 Runtime 之间建立通用中间层，通过有限的受控接入点观察 Agent 的请求、响应、调用和运行事件，在线重建真实执行关系。

已有设计原则：

- 不依赖完整静态 DAG。
- 不读取框架内部 Scheduler。
- 不假设所有 Agent 提前已知。
- 不依赖固定格式的 `agent_id`。
- 不依赖自然语言语义猜测 Agent 身份。
- 不修改 Agent 原始 Query 来插入标识。
- 不要求下一 Agent 的唤醒格式固定。
- 不依赖特定 Agent Framework。
- 支持同一工作流中的多个并发 Agent。
- 使用明确的调用、结果传递和执行边界建立可验证的因果关系。
- 不将同时发生的事件直接视为因果关系。

AgentFuse 应当建立在这些原则之上。

如果当前代码仓库中已有工作流重建模块，应优先检查、复用和扩展。

如果不存在可直接使用的代码，应实现一个最小兼容事件接口，并确保以后能对接现有系统。

不要为了开发 AgentFuse 将原有中间层整体重写。

## 3.2 职责划分

原有中间层负责：

- Agent 身份绑定。
- Workflow 身份关联。
- 调用事件收集。
- 请求与响应关联。
- Agent 调用关系。
- 结果传递关系。
- 在线运行图构建。

AgentFuse 新增：

- 工具执行请求接管。
- 候选共享组管理。
- 真实物理执行调度。
- 动态文件依赖追踪。
- 不同分支的依赖验证。
- 共享等待与执行分离。
- 本地资源控制。
- 执行结果分发。
- 共享收益分析。

两者需要通过清晰接口连接。

## 3.3 核心架构

目标架构：

```text
                  Multi-Agent Framework
                           |
                           v
                   Controlled Adapter
                           |
                           v
             Existing Workflow Middleware
                           |
              +------------+------------+
              |                         |
              v                         v
        Event Collector           Runtime Controller
              |                         |
              v                         v
        Execution Graph           Share Coordinator
              |                         |
              |                 +-------+-------+
              |                 |               |
              |                 v               v
              |          Dependency       Resource
              |          Validator        Scheduler
              |                 |               |
              |                 +-------+-------+
              |                         |
              |                         v
              |                  Tool Executor
              |                         |
              |                         v
              |                Local CPU / Memory
              |                         |
              +------------+------------+
                           |
                           v
                    SQLite Storage
                           |
                           v
                  Local Web Dashboard
```

系统必须体现控制与观测之间的真实联系。

不能只用可视化图代替执行控制。

---

# 四、核心数据模型

系统必须明确区分以下概念。

## 4.1 Workflow

表示一次完整的多智能体工作流执行实例。

主要字段：

- workflow_id
- scenario
- start_time
- end_time
- status
- configuration
- runtime_mode
- random_seed

一个 Workflow 可以包含多个 Agent。

## 4.2 Agent

表示一个逻辑 Agent 实体。

主要字段：

- internal_agent_id
- workflow_id
- external_identity
- agent_description
- registration_source
- metadata

外部框架可能没有固定的 agent_id。

因此内部标识应由适配器在实际执行边界建立和维护。

不能依靠文本相似度猜测 Agent 身份。

## 4.3 AgentInvocation

表示某个 Agent 的一次实际激活或调用。

主要字段：

- invocation_id
- workflow_id
- agent_id
- start_time
- end_time
- parent_invocation_id
- status

同一个 Agent 可以出现多次 Invocation。

必须区分：

- Agent 实体。
- Agent 的一次调用。
- Agent 发起的工具调用。

不能将它们混成同一种节点。

## 4.4 LogicalToolCall

表示 Agent 发起的一次逻辑工具请求。

主要字段：

- logical_call_id
- workflow_id
- invocation_id
- tool_name
- tool_version
- normalized_arguments
- workspace_snapshot_id
- request_time
- status
- physical_execution_id
- result_reference

即使两个 Agent 共享同一次实际工具执行，它们也必须拥有不同的 LogicalToolCall。

## 4.5 PhysicalExecution

表示系统真正启动的一次工具计算。

主要字段：

- execution_id
- tool_name
- tool_version
- arguments_hash
- source_snapshot_id
- worker_pid
- start_time
- finish_time
- execution_status
- cpu_time
- peak_rss
- exit_status
- result_hash

一个 PhysicalExecution 可以对应多个 LogicalToolCall。

这是 AgentFuse 最重要的数据关系之一。

## 4.6 WorkspaceSnapshot

表示一次不可变的工作区文件状态。

主要字段：

- snapshot_id
- parent_snapshot_id
- workspace_id
- snapshot_path
- creation_time
- manifest_hash
- source_reference
- metadata

首版只管理文件系统状态，不保存进程内存、寄存器或者正在运行的线程状态。

## 4.7 DependencyRecord

表示工具执行过程中观察到的实际输入依赖。

主要字段：

- execution_id
- sequence_number
- dependency_type
- relative_path
- fingerprint
- observed_time

依赖类型至少包括：

- FILE_READ
- DIRECTORY_LIST
- PATH_EXISTS
- PATH_NOT_EXISTS

必要时可以增加 FILE_STAT。

## 4.8 ShareGroup

表示一组潜在可以共享执行的逻辑调用。

主要字段：

- share_group_id
- candidate_key
- leader_execution_id
- creation_time
- status
- participant_calls

共享组需要支持动态加入、验证、分离与关闭。

## 4.9 ValidationRecord

记录一次真实的共享条件检查。

主要字段：

- validation_id
- share_group_id
- logical_call_id
- dependency_sequence
- validation_status
- validation_time
- mismatch_reason

验证状态至少包括：

- PENDING
- MATCH
- MISMATCH
- UNKNOWN

UNKNOWN 不可以被解释为 MATCH。

---

# 五、受控接入与事件系统

## 5.1 统一事件协议

定义统一的内部事件结构：

```python
RuntimeEvent = {
    "event_id": "...",
    "workflow_id": "...",
    "event_type": "...",
    "timestamp": ...,
    "agent_id": ...,
    "invocation_id": ...,
    "logical_call_id": ...,
    "physical_execution_id": ...,
    "parent_event_id": ...,
    "metadata": {...}
}
```

允许可选字段为空。

内部 event_id 必须全局唯一。

一个 Workflow 内的事件必须具有稳定的序号，避免只依赖墙上时钟排序。

至少支持：

- WORKFLOW_STARTED
- WORKFLOW_FINISHED
- AGENT_BOUND
- AGENT_INVOKED
- AGENT_RETURNED
- TOOL_CALL_REQUESTED
- TOOL_CALL_WAITING
- TOOL_EXECUTION_STARTED
- TOOL_READ_OBSERVED
- SHARE_CANDIDATE_FOUND
- SHARE_VALIDATION_PASSED
- SHARE_VALIDATION_FAILED
- SHARE_PARTICIPANT_DETACHED
- TOOL_EXECUTION_FINISHED
- TOOL_RESULT_DELIVERED
- TOOL_CALL_FAILED
- TOOL_CALL_CANCELLED

事件必须由真实执行行为产生。

不能为了让执行图更好看而伪造事件。

## 5.2 身份绑定

提供适配器接口。

适配器应当能够在实际 Agent 注册、调用或唤醒边界绑定内部标识。

建议抽象：

```python
bind_agent(...)
begin_invocation(...)
submit_tool_call(...)
deliver_result(...)
end_invocation(...)
record_event(...)
```

具体参数由实现确定。

必须提供一个清晰的最小 API。

禁止要求所有外部框架必须提供固定的 agent_id 或固定的请求载荷。

外部身份可以为空，但内部必须维持稳定的运行实例引用。

如果无法确认两个事件属于同一个 Agent，则保留身份未确定状态，不得直接合并。

## 5.3 因果关系

只根据已观察到的事实建立因果关系。

例如：

- 某个 AgentInvocation 发出了 ToolCall。
- 某个 ToolCall 绑定到了 PhysicalExecution。
- 某个 PhysicalExecution 产生了 ToolResult。
- 某个 ToolResult 被交付给指定的 Invocation。
- 某个 Invocation 通过明确调用边界触发了另一个 Invocation。

禁止仅凭：

- 时间接近。
- 参数相似。
- 文本语义相近。
- Agent 名称相似。

来认定因果关系。

执行图应当允许暂时不完整。

随着真实事件到达，执行图可以增量更新。

---

# 六、工作区快照与分支管理

## 6.1 基本目标

不同 Agent 可以从同一个代码仓库的不同文件状态执行工具。

系统需要能够明确区分这些状态。

例如：

```text
Original Workspace
        |
        v
     Snapshot S0
        |
   +----+----+
   |         |
   v         v
Branch A   Branch B
   |         |
   v         v
 S_A        S_B
```

Agent A 和 Agent B 可以读取不同快照。

快照不要求使用高级文件系统 Copy-on-Write。

首版允许使用普通文件复制和 SHA-256 哈希。

## 6.2 实现方式

实现：

- create_workspace
- create_snapshot
- fork_snapshot
- inspect_snapshot
- compare_snapshots
- delete_snapshot

快照创建以后不能被正常工作流直接修改。

所有分支修改都应当发生在独立的可写工作目录中，完成后生成新的不可变快照。

为控制首版复杂度：

- 仅支持小型本地代码仓库。
- 限制允许纳入快照的文件数量和总大小。
- 默认排除 `.git`、虚拟环境、缓存和大型二进制文件。
- 不实现块级去重。
- 不实现进程级检查点。
- 不实现真正的文件系统层隔离。

这些限制需要明确写入文档。

## 6.3 文件依赖表示

对文件读取：

```text
FILE_READ
path = src/parser.py
hash = SHA256(file_content)
```

对目录读取：

```text
DIRECTORY_LIST
path = src/
hash = SHA256(canonical_child_entries)
```

目录指纹必须来自规范化的直接子项名称与类型。

对路径不存在的检查：

```text
PATH_NOT_EXISTS
path = config/local.json
```

所有路径使用相对工作区根目录的规范化表示。

必须拒绝通过 `../` 或其他方式越过受控根目录的路径。

首版不支持跨出工作区的符号链接访问。

不能在两个分支之间直接比较绝对路径，因为不同分支的物理目录位置本来就不同。

---

# 七、受控工具执行系统

## 7.1 工具注册

提供一个 Tool Registry。

每个工具需要声明：

- tool_name
- tool_version
- deterministic
- read_only
- resource_estimate
- input_schema
- output_schema
- implementation_reference

只有满足共享安全契约的工具才可以进入 Share Coordinator。

首版不允许对任意 shell 命令自动开启共享。

## 7.2 工具执行接口

建议提供：

```python
class WorkspaceReader:
    def read_text(self, path): ...
    def read_bytes(self, path): ...
    def list_dir(self, path): ...
    def exists(self, path): ...
    def stat(self, path): ...
```

所有允许共享的文件依赖都必须通过这个受控接口读取。

这个接口负责：

1. 规范化路径。
2. 校验工作区边界。
3. 读取对应快照。
4. 计算依赖指纹。
5. 立即向 Runtime 报告读取事件。
6. 将读取结果返回工具。

不允许共享工具绕过该接口直接读取工作区文件。

必须明确：这是针对可信、受控工具的执行契约，不构成对任意恶意程序的操作系统级安全隔离。

无法确认完整依赖的工具必须退化为独立执行。

## 7.3 必须实现的本地工具

至少包含四种真实工具。

### Tool 1：SourceFileIndex

遍历指定源码目录。

输出：

- 源代码文件列表。
- 文件数量。
- 文件扩展名统计。
- 文件大小统计。

目录遍历必须产生对应依赖记录。

### Tool 2：PythonASTAnalyzer

读取指定 Python 源码文件。

使用 Python AST 解析。

输出：

- 函数数量。
- 类数量。
- 导入语句数量。
- 代码结构摘要。

必须真正解析源代码。

### Tool 3：SourceDependencyAnalyzer

扫描指定 Python 源文件集合。

输出：

- 模块导入关系。
- 模块之间的本地依赖。
- 简单依赖统计。

无需实现完整 Python 包解析器，只需在文档中明确支持范围。

### Tool 4：ContentDigestAnalyzer

读取指定文件集合。

执行内容哈希与确定性计算。

支持设置合理的重复分析次数，以构造实际 CPU 工作负载。

不能只用 `sleep()` 伪造 CPU 计算。

这个工具主要用于测量共享执行的真实资源收益。

## 7.4 确定性要求

可共享工具必须满足：

相同工具实现、相同规范化参数、相同声明环境以及相同实际读取依赖，应当产生相同的逻辑结果。

工具返回值不得依赖：

- 当前随机数。
- 当前系统时间。
- 未声明的环境变量。
- Agent 的隐含上下文。
- 工作区的绝对物理路径。
- 未追踪的外部文件。
- 外部网络服务。

需要使用这些输入的工具，必须显式声明并使其进入共享验证条件，否则禁止共享。

---

# 八、核心算法：跨 Agent 共享执行

这一部分是项目最核心的技术实现。

## 8.1 候选共享识别

当两个 Agent 发出工具请求时，首先判断它们是否可能共享同一次执行。

定义候选键：

```text
CandidateKey = SHA256(
    ToolName
    + ToolVersion
    + CanonicalArguments
    + ExecutionEnvironmentSignature
)
```

其中：

- ToolName：工具名称。
- ToolVersion：工具实现版本。
- CanonicalArguments：规范化参数。
- ExecutionEnvironmentSignature：工具相关的环境与配置签名。

注意：

**候选键相同只代表可能共享，不能直接证明执行结果等价。**

不要简单把整个 WorkspaceSnapshot ID 加入候选键，否则不同分支永远无法共享。

真正决定共享正确性的应当是工具实际读取的依赖。

## 8.2 共享执行状态机

建议共享组支持：

- CREATED
- EXECUTING
- VALIDATING
- COMPLETED
- FAILED
- CLOSED

参与调用支持：

- WAITING
- SHARED_WAIT
- DETACHED
- RUNNING_INDEPENDENTLY
- COMPLETED
- FAILED
- CANCELLED

状态转换必须有明确规则。

不允许一个逻辑调用同时归属于两个正在执行的物理任务。

## 8.3 第一个调用到达

对于某个候选键：

1. 查找正在执行的共享组。
2. 如果不存在，创建 ShareGroup。
3. 创建 PhysicalExecution。
4. 将当前 LogicalToolCall 绑定为第一个参与者。
5. 将物理执行提交给资源调度器。
6. 工具开始执行并产生依赖读取事件。

## 8.4 后续调用到达

如果出现候选键相同的请求：

1. 检查对应 ShareGroup 是否仍处于可加入状态。
2. 检查工具版本、参数和环境签名。
3. 将新调用加入候选共享组。
4. 获取当前物理执行已经观察到的依赖记录。
5. 在新调用对应的工作区快照中验证这些依赖。
6. 如果全部匹配，则暂时进入 SHARED_WAIT。
7. 如果存在不匹配，则立即 DETACH。
8. 如果存在无法验证的依赖，则保守地独立执行。

新加入的调用不能仅仅因为候选键相同就直接获得结果。

## 8.5 在线动态依赖验证

这是首版必须实现的核心功能。

当 Leader 工具读取一个新依赖：

```text
Read Event:
  FILE_READ(src/main.py, hash=H1)
```

Share Coordinator 应当立即：

1. 将记录写入依赖账本。
2. 读取每个候选参与者所对应快照的相同依赖指纹。
3. 比较是否一致。
4. 一致则保持共享等待。
5. 不一致则立即将参与者分离。
6. 被分离的调用进入独立执行队列。

不允许等整个 Leader 完全执行结束后才开始检查所有依赖。

在线验证的价值在于：

**当较早发现依赖不一致时，另一个分支可以提前启动独立执行，从而减少等待。**

## 8.6 增量验证语义

每个 PhysicalExecution 维护一个按序增长的 Dependency Ledger：

```text
R = [r1, r2, ..., rk]
```

对每个候选参与者维护：

```text
validated_prefix_length
validation_status
```

每出现新依赖，检查对应的新增部分。

如果某个参与者与 Leader 的已观察依赖不一致，则将其从当前共享组分离。

如果参与者在执行中途加入，必须先验证已有全部记录，再订阅后续依赖事件。

需要处理并发竞态：

- 已有依赖验证与新依赖写入同时发生。
- 参与者加入时 Leader 恰好完成执行。
- 参与者取消时共享组正在分发结果。
- 某个分支分离后又出现相同参数的新调用。

通过单一协调者的状态锁、事件队列或等价的串行化机制维护一致性。

## 8.7 最终提交验证

Leader 完成计算以后，不能立即向全部参与者广播结果。

必须：

1. 确认工具正常完成。
2. 冻结本次物理执行的依赖账本。
3. 对每个仍在共享组中的参与者确认全部依赖已验证。
4. 确认工具输出符合上下文无关的共享契约。
5. 将结果安全地绑定到对应 LogicalToolCall。
6. 分别发送结果交付事件。

无法通过验证的参与者必须独立执行。

这条规则非常重要：

**任何尚未完成依赖验证的逻辑调用，都不能对外暴露共享执行结果。**

## 8.8 分离机制

当 Agent B 的某个实际读取依赖与 Agent A 不同：

```text
Agent A:
    src/config.py -> hash_A

Agent B:
    src/config.py -> hash_B
```

如果：

```text
hash_A != hash_B
```

则：

- Agent B 从共享组中移除。
- Agent A 的实际执行继续。
- Agent B 创建独立 PhysicalExecution。
- Agent B 的执行使用自己的工作区快照。
- 两者分别产生真实结果。
- 系统记录发生分离的依赖与时刻。

不能直接将 Agent A 的结果返回给 Agent B。

## 8.9 结果缓存边界

首版核心是**正在执行中的调用合并（In-flight Sharing）**。

不要求实现跨进程重启、跨会话永久有效的通用结果缓存。

可以在同一次运行会话中扩展有限的已完成结果复用，但必须经过完整依赖验证。

若缓存机制影响在线共享与正确性实现，应推迟。

## 8.10 正确性原则

系统必须始终满足：

1. 不共享未经验证的结果。
2. 不复用可能依赖不同输入状态的结果。
3. 不将工具参数相同视为计算等价。
4. 不将不同 Agent 的逻辑调用合并成一个身份。
5. 共享失败时不会造成结果错发。
6. 同一调用不会被重复交付。
7. 分离后使用正确的工作区状态独立执行。
8. 共享不能改变工具的逻辑输出。
9. 没有证据时宁可不共享。
10. 共享必须有可检查的验证记录。

---

# 九、资源管理系统

## 9.1 资源控制器

建立 LocalResourceController。

至少管理：

- 最大并发执行进程数。
- 运行进程数。
- 等待执行数。
- CPU 使用情况。
- 内存 RSS。
- 已声明的预计资源需求。
- 单次执行超时。
- 执行取消。

首版默认：

```text
max_workers = 2
max_memory_budget = configurable
default_timeout_seconds = 60
```

配置应可修改。

## 9.2 CPU 并发控制

使用 Worker Pool 或等价机制。

最大运行任务数不能超过配置。

等待任务进入调度队列。

只有获得执行许可的 PhysicalExecution 才能启动计算。

多个 LogicalToolCall 共享同一个 PhysicalExecution 时，只占用一次执行槽位。

不能按照逻辑调用数量重复扣除资源额度。

## 9.3 内存控制

使用 psutil 采集进程 RSS。

工具注册时可以声明预计内存需求。

资源控制器根据预算决定是否允许启动新任务。

注意：

基于预计内存进行准入控制不等同于操作系统级硬内存限制。

必须准确区分：

- 预计内存预算。
- 实际监控 RSS。
- 操作系统提供的硬限制能力。

首版不要求实现跨平台硬内存限制。

如果实际内存使用超出配置预算，可以触发警告并阻止新的任务进入执行。

不要把未实现的限制描述为强制隔离。

## 9.4 工具进程执行

应尽量在独立工作进程中执行工具。

要求：

- 主 Runtime 不被 CPU 密集型计算阻塞。
- 进程异常退出能够被检测。
- 超时可以终止执行。
- 支持任务取消。
- 能回收已退出进程。
- 可以采集实际执行时间与资源使用。

工具读取依赖时，需要通过 IPC、队列或其他可靠方式及时向协调者发送依赖事件。

不允许等工具退出后才一次性上报全部读取依赖，因为这无法支持真正的在线验证。

## 9.5 执行调度

首版采用简单稳定的 FIFO 物理执行调度。

可以优先实现：

- 有限并发。
- 预计内存准入。
- 等待队列。
- 超时终止。
- 取消。
- 故障回收。

不必在首版实现复杂的 SLO-Aware 或 Goodput-Aware Scheduler。

后续可以将这套 Runtime 扩展为研究资源调度策略的平台。

---

# 十、并发、失败和取消语义

## 10.1 Leader 执行失败

如果共享组的物理执行失败：

- 所有等待参与者必须收到明确状态变化。
- 不能将部分执行结果视为成功。
- 记录失败原因。
- 默认允许参与者进入独立重试队列一次。
- 防止无限重试。

需要明确区分工具业务错误、工作进程崩溃和 Runtime 调度错误。

## 10.2 Follower 取消

如果某个等待共享结果的 Agent 取消：

- 移除该 LogicalToolCall 的等待关系。
- 保留其他参与者的执行。
- 不得因为一个 Follower 取消而终止整个共享组。

## 10.3 Leader 逻辑调用取消

Leader 是逻辑上的首个请求，不代表其拥有物理执行的独占所有权。

如果 Leader 对应的 LogicalToolCall 取消，但还有其他参与者：

- 物理执行可以继续。
- 其余参与者仍然等待结果。
- 取消的调用不能收到成功交付。
- 共享组不能因为 Leader 取消就丢失执行状态。

## 10.4 所有参与者取消

如果没有任何有效参与者继续等待：

- 允许终止物理执行。
- 释放资源。
- 记录取消。
- 清理共享组。

## 10.5 Runtime 进程中断

首版不要求恢复已经死亡的操作系统进程。

但需要：

- 将被中断的运行记录标记为 INTERRUPTED。
- 清理不存在的 Worker 状态。
- 不将中断状态误判为完成。
- 允许用户重新发起运行。
- 保证 SQLite 数据不因普通异常而损坏。

---

# 十一、在线执行图重建与展示

## 11.1 图节点

至少区分：

- Workflow
- AgentInvocation
- LogicalToolCall
- PhysicalExecution
- ToolResult

## 11.2 图边

至少包括：

- AGENT_INVOKES
- CALLS_TOOL
- BOUND_TO_EXECUTION
- EXECUTION_PRODUCES_RESULT
- RESULT_DELIVERED_TO
- SHARES_EXECUTION_WITH
- EXPLICIT_INVOCATION_DEPENDENCY

共享执行的图结构应当真实体现：

```text
Agent A -> LogicalCall A --+
                            |
                            v
                    PhysicalExecution
                            |
                            v
                       ToolResult
                            |
Agent B -> LogicalCall B --+---> Individual Deliveries
```

两个 Agent 的逻辑调用始终独立存在。

图结构不要求首版支持任意复杂 DAG 的完美布局，但需要能明确查看节点与边的真实关系。

## 11.3 不完整图处理

如果某个 Agent 调用关系尚未被观察到：

- 不自动创造边。
- 可以展示 UNKNOWN 状态。
- 等后续真实事件到达再补充。

不要为了追求完整图而牺牲因果正确性。

## 11.4 可观察状态

用户应当能够看到：

- 哪个 Agent 发起请求。
- 哪个工具真正启动了物理执行。
- 哪些调用正在共享等待。
- 哪些调用完成验证。
- 哪些调用因为依赖不同而分离。
- 哪些执行正在占用 CPU 与内存。
- 哪些调用由于资源不足而等待。

---

# 十二、真实离线多智能体演示

系统必须提供可以直接运行的本地演示工作流。

演示不能依赖外部 LLM。

## 12.1 Scripted Agent

实现一套轻量 Scripted Agent 示例。

Scripted Agent 是按照实际结果动态选择下一步的本地程序，不要求具有语言模型智能能力。

必须明确命名为 Scripted Agent，不能宣称其具有 LLM Agent 的全部能力。

这些 Agent 应当通过与真实框架相同的适配器接口进入 AgentFuse。

不能为了 Demo 单独写一套虚假的执行路径。

## 12.2 场景一：完全共享

两个 Agent 从同一个快照开始。

同时请求对同一组 Python 文件执行 AST 分析。

要求：

- 生成两个 LogicalToolCall。
- 候选键相同。
- 只有一个 PhysicalExecution。
- 两个调用都获得正确结果。
- 记录真实共享关系。
- 相比独立执行减少物理调用次数。

需要使用同步屏障或其他确定性机制，使两个调用确实在执行时间上重叠。

## 12.3 场景二：不同工作区但仍能共享

两个分支来自同一初始快照。

Agent A 修改 README。

Agent B 保持原样。

二者都请求分析相同的 `src/*.py` 文件。

如果工具实际读取的源码文件与目录依赖一致，应当允许共享。

要求证明：

**工作区整体不同，不一定代表工具执行结果不同。**

## 12.4 场景三：动态依赖冲突

两个分支中某个源码文件内容不同。

两个 Agent 发起候选键相同的 AST 或内容分析任务。

Leader 运行过程中读取到有差异的文件。

要求：

- 产生真实 FILE_READ 事件。
- Follower 发现不匹配。
- Follower 及时从共享组分离。
- 分离时 Leader 尚未结束后续计算。
- Follower 启动独立执行。
- 最终两个调用分别得到各自正确的结果。

需要记录分离发生的实际时间。

这是验证在线依赖检查价值的主要场景。

## 12.5 场景四：多个 Agent 并行与汇合

构造一个具有真实依赖的协作流程：

```text
Supervisor
    |
    +----> Agent A ----+
    |                  |
    +----> Agent B ----+----> Aggregator
    |                  |
    +----> Agent C ----+
```

各 Agent 根据工具返回的真实数据执行不同的后续操作。

Aggregator 必须在所有必要结果到达后才能启动。

这个场景用于验证：

- Agent 身份绑定。
- 并发调用。
- 工具结果传递。
- 运行图重建。
- 共享执行。
- 资源竞争。
- 执行等待。

图必须由事件生成，不能直接读取演示工作流的预设 DAG 并冒充为重建结果。

## 12.6 场景五：取消和失败

至少包含：

- 一个共享等待者取消。
- Leader 逻辑调用取消。
- 物理执行超时。
- 工具异常退出。
- 多个调用等待同一失败执行。

验证不会产生错误结果、永久等待或者资源泄漏。

---

# 十三、Benchmark 与实验对比

AgentFuse 需要内置真实的对比测试。

## 13.1 比较模式

至少支持：

### Mode A：Baseline

所有工具调用独立执行。

不启用共享。

### Mode B：Online Sharing

启用：

- 候选共享识别。
- 在线动态依赖验证。
- 分离机制。
- 共享结果分发。

建议进一步支持：

### Mode C：Deferred Validation

允许等待候选共享执行，但直到 Leader 完成之后才集中验证依赖。

这用于比较在线分离是否降低等待。

如果开发时间不足，首先保证 A、B 模式完整正确。C 模式可以作为后续扩展。

## 13.2 实验公平性

所有模式必须保持：

- 相同输入仓库。
- 相同工作区快照。
- 相同工具实现。
- 相同任务请求序列。
- 相同 Worker 并发上限。
- 相同内存预算。
- 相同超时配置。
- 相同随机种子。

不能让共享模式使用更多计算资源。

建议对同一实验重复运行多次。

由于系统负载波动可能影响结果，报告中应记录重复测量及其波动。

## 13.3 必须记录的指标

### 工作流完成时间

```text
Workflow Makespan
= Workflow Finish Time - Workflow Start Time
```

### 物理执行次数

```text
Physical Execution Count
```

### 冗余执行减少量

```text
Saved Executions
= Baseline Physical Executions
  - Shared Physical Executions
```

### 共享成功率

```text
Share Success Rate
= Successfully Shared Logical Calls
  / Eligible Candidate Calls
```

必须在报告中明确分母定义。

### 总 CPU 时间

累计工具 Worker 的实际 CPU 时间。

### 峰值内存

采集实际工具进程内存 RSS，并说明聚合方法和采样频率。

### 共享等待时间

记录逻辑调用从进入共享等待到收到结果或分离的持续时间。

### 验证开销

记录：

- 依赖验证次数。
- 验证耗时。
- 验证失败次数。
- 分离次数。

### 在线分离收益

记录：

- 依赖冲突首次被观察到的时间。
- Follower 实际开始独立执行的时间。
- 如果执行了 Deferred 模式，比较两种模式下的等待时间。

### 正确性

所有共享结果必须与相同分支、相同输入下的独立执行结果一致。

## 13.4 Speedup

定义：

```text
Speedup = Baseline Makespan / AgentFuse Makespan
```

只对相同工作负载进行比较。

如果 AgentFuse 更慢，也必须真实显示。

禁止伪造性能收益。

## 13.5 对比结果

Web UI 至少展示：

- 工作流完成时间。
- 实际工具执行次数。
- 总 CPU 时间。
- 峰值 RSS。
- 共享次数。
- 分离次数。
- 验证开销。
- 逻辑执行与物理执行对应关系。

可以使用简单的柱状图、折线图和表格。

不要求复杂图表组件。

---

# 十四、本地 Web 管理界面

## 14.1 产品目标

用户应当能够通过浏览器直接操作 Runtime，而不只是运行测试脚本。

默认地址：

```text
http://127.0.0.1:8765
```

前端必须读取真实后端状态。

禁止使用假任务、假 Agent、随机生成的资源曲线冒充实际运行情况。

## 14.2 Dashboard

展示：

- 当前运行中的 Workflow。
- 活跃 Agent Invocation 数量。
- LogicalToolCall 数量。
- PhysicalExecution 数量。
- 正在执行的 Worker。
- CPU 与内存占用。
- 最近执行日志。
- 最近共享与分离事件。

## 14.3 Workflow Detail

选择某次 Workflow 后展示：

- Agent 执行关系图。
- 工具调用节点。
- 物理执行节点。
- 共享关系。
- 任务状态。
- 已确认依赖。
- 等待原因。

点击节点能够查看详细信息。

## 14.4 Execution Timeline

实现简化的执行时间线。

横轴为时间。

每个 Agent 或 PhysicalExecution 展示一条泳道。

标记：

- 请求发起。
- 共享等待。
- 实际执行。
- 依赖验证。
- 分离。
- 执行完成。
- 结果交付。

重点让用户直观看出共享执行相比独立执行减少了哪些重复工作。

## 14.5 Dependency Inspector

选择 PhysicalExecution，展示它读取的依赖：

| 路径 | 类型 | Leader 指纹 | Follower 验证 |
|---|---|---|---|
| src/main.py | FILE_READ | ... | MATCH |
| src/config.py | FILE_READ | ... | MISMATCH |
| src/ | DIRECTORY_LIST | ... | MATCH |

支持查看具体的失败原因。

## 14.6 Benchmark 页面

用户可以：

- 选择演示场景。
- 选择 Baseline 或 Online Sharing。
- 设置 Worker 数量。
- 启动运行。
- 查看结果。
- 对比历史运行。

界面必须有真实的运行按钮和状态反馈。

---

# 十五、命令行工具

除了 Web UI，必须提供 CLI。

推荐命令：

```bash
agentfuse init
```

初始化本地数据目录。

```bash
agentfuse serve
```

启动本地管理服务。

```bash
agentfuse demo --scenario shared --mode baseline
```

运行完全共享场景的独立执行版本。

```bash
agentfuse demo --scenario shared --mode online
```

运行在线共享版本。

```bash
agentfuse demo --scenario diverged --mode online
```

运行动态依赖冲突场景。

```bash
agentfuse demo --scenario multi-agent --mode online
```

运行并行多 Agent 场景。

```bash
agentfuse compare --scenario shared --repeat 5
```

执行重复对比实验。

```bash
agentfuse trace <workflow_id>
```

查看 Workflow 的真实事件和运行关系。

```bash
agentfuse inspect <execution_id>
```

查看物理执行状态、依赖和资源信息。

以上为目标 CLI 形式。

具体可以通过 Python 包入口实现，但必须保证命令实际可用。

---

# 十六、SQLite 数据持久化

需要保存：

- Workflows
- Agents
- AgentInvocations
- LogicalToolCalls
- PhysicalExecutions
- ShareGroups
- ShareParticipants
- WorkspaceSnapshots
- DependencyRecords
- ValidationRecords
- RuntimeEvents
- ResourceSamples
- BenchmarkRuns

可以按实际实现合理合并表。

要求：

1. 主键和外键关系清晰。
2. 重要状态转换使用事务。
3. 逻辑调用与物理执行存在明确关联。
4. 结果交付具有幂等保护。
5. 大量事件写入不能长期阻塞执行主循环。
6. 支持按 Workflow 和 Execution 查询。
7. 保存数据库 Schema 版本。
8. 初始版本提供自动建表和必要迁移。

对大型工具输出可以采用文件持久化，并在 SQLite 中保存路径和摘要。

不要求把所有输出内容都塞进数据库。

---

# 十七、自动化测试

至少建立以下测试类别。

## 17.1 图重建测试

- Agent 身份不会因为并发而混淆。
- 同一 Agent 的不同 Invocation 能区分。
- 工具请求与响应正确关联。
- 未观察到的因果边不会自动生成。
- 共享执行不会合并两个 AgentInvocation。
- 乱序到达的事件能够被正确处理或明确标识。

## 17.2 共享正确性测试

- 相同参数和相同依赖可以共享。
- 相同参数但不同实际依赖不能共享。
- 不相关文件发生变化仍可能共享。
- 不同工具版本不能共享。
- 不同环境签名不能共享。
- 未注册的工具不能被自动共享。
- 未声明的非确定性工具不能被共享。
- 共享结果与 Baseline 完全一致。

## 17.3 动态验证测试

- Leader 读取文件后立即产生依赖事件。
- Follower 能验证已有依赖。
- Follower 能验证后来出现的依赖。
- 中途加入的参与者不会遗漏早期依赖。
- 发现冲突立即分离。
- 分离以后执行自己的工作区。
- Leader 结束前无法通过最终验证的 Follower 不能收到结果。
- 目录列表变化会正确影响验证。
- 文件不存在状态变化会被检测。

## 17.4 并发测试

- 多个 Agent 同时提交相同调用。
- 多个 Agent 同时加入共享组。
- Follower 加入时 Leader 正在结束。
- Follower 取消时结果正在返回。
- Leader 取消后其他参与者继续等待。
- 所有参与者取消后可以终止执行。
- 不会创建重复 PhysicalExecution。
- 同一个 LogicalToolCall 不会收到两次结果。

## 17.5 资源测试

- 运行 Worker 数不超过配置上限。
- 超时任务可以终止。
- Worker 异常退出可以回收。
- 共享调用不重复占用 Worker 配额。
- 内存预算不足时进入等待。
- 资源释放后等待任务可以继续。
- 长时间运行后不会残留大量无效进程。

## 17.6 快照测试

- 不同分支文件状态互不影响。
- 快照创建后状态保持稳定。
- 相同内容得到一致指纹。
- 不同内容得到不同指纹。
- 非法路径不能访问快照根目录之外。
- 文件删除和目录变化被正确记录。

## 17.7 集成测试

至少真实运行以下完整流程：

1. 单 Agent 调用工具。
2. 双 Agent 完全共享。
3. 不同快照但依赖等价。
4. 动态依赖不一致并提前分离。
5. 三 Agent 并行工作流。
6. 工具异常失败。
7. 中途取消。
8. Baseline 与 Online Sharing 对比。

必须验证最终结果，而不是只检查是否没有抛出异常。

---

# 十八、一个月开发计划

请按照四个阶段增量开发。

## 第一周：真实执行与事件闭环

目标：

**让多个本地 Scripted Agent 能够真正运行工具，并自动产生正确的执行关系。**

完成：

- 项目初始化。
- 基础目录结构。
- 配置系统。
- 统一事件格式。
- Agent Adapter。
- Workflow Runtime。
- LogicalToolCall。
- PhysicalExecution。
- Worker 执行器。
- 至少两个真实本地工具。
- SQLite 基础表。
- 简单 CLI。
- 最简 Workflow 运行图。

第一周结束必须可以：

- 启动两个本地 Agent。
- 发出真实工具请求。
- 执行本地计算。
- 收集请求、执行和结果事件。
- 查看逻辑调用和物理执行对应关系。

不能只交付一堆尚未连接的模块。

## 第二周：安全共享执行

目标：

**在保证正确性的前提下，让两个逻辑调用共享一次真实物理执行。**

完成：

- Tool Registry。
- CandidateKey。
- ShareGroup。
- In-flight Sharing。
- Follower 共享等待。
- 共享执行结果分发。
- 取消和失败处理。
- Worker 并发控制。
- 完全共享场景。
- 不相关文件变化场景。
- Baseline 对比。

第二周结束必须证明：

同一个确定性工具的两个等价调用，只启动一次实际计算，两个 Agent 均得到正确结果。

## 第三周：在线动态依赖验证

目标：

**使不同工作区中的候选共享调用能够在执行过程中被正确验证，并在不一致时提前分离。**

完成：

- WorkspaceSnapshot。
- 文件和目录指纹。
- WorkspaceReader。
- Dependency Ledger。
- 实时 READ 事件。
- 增量依赖验证。
- Late Join 验证。
- 动态分离。
- 独立重执行。
- 结果最终提交验证。
- 依赖检查日志。
- 动态依赖冲突演示。
- 资源统计。

第三周结束必须证明：

不同分支中读取依赖相同时可以共享，而当某个读取依赖发生变化时，系统会在 Leader 完成之前发现冲突，并启动独立执行。

这是第三周的硬性验收条件。

## 第四周：完整产品与系统评估

目标：

**将核心 Runtime 变成一个可以独立操作、观察与评估的本地系统。**

完成：

- FastAPI 服务。
- Web Dashboard。
- Workflow Graph。
- Execution Timeline。
- Dependency Inspector。
- Benchmark 对比页面。
- CLI 完善。
- 自动化集成测试。
- 重复 Benchmark。
- 错误处理。
- 用户文档。
- Windows/WSL2 运行说明。
- 可安装或可直接启动的交付包。

第四周结束必须可以：

1. 启动 AgentFuse。
2. 在浏览器打开管理界面。
3. 选择一个真实多 Agent 演示。
4. 启动 Baseline。
5. 启动 Online Sharing。
6. 观察实时执行过程。
7. 查看共享与分离。
8. 对比资源与执行耗时。
9. 查看历史数据。
10. 在没有外部 API 的情况下重新运行实验。

---

# 十九、优先级与范围控制

为了防止一个月内无法完成，需要明确优先级。

## P0：必须完成

- 真实多 Agent 工作流。
- 受控调用接入。
- 真实工具进程执行。
- 基本在线运行图。
- 本地工作区快照。
- 工具依赖追踪。
- In-flight Sharing。
- 在线动态依赖验证。
- 冲突分离。
- 正确结果分发。
- Worker 并发控制。
- 基线对比。
- SQLite。
- 可运行 CLI。
- 基础 Web UI。
- 完整自动化测试。

## P1：尽量完成

- 更丰富的执行时间线。
- 更精细的内存准入。
- Deferred Validation 对比。
- 更多真实工具。
- 更完善的资源统计。
- 更友好的图形界面。

## P2：后续扩展

- 跨会话持久化结果缓存。
- GPU 资源调度。
- KV Cache 管理。
- 真正的 Agent 推理执行优化。
- 多节点分布式执行。
- 复杂的可抢占任务调度。
- 自动预测未来 Agent 路径。
- 框架无关的高级自动适配。
- 对任意第三方工具进行完备依赖追踪。
- 具备安全隔离保证的通用沙箱。

不允许为了 P2 功能而影响 P0 的正确性。

---

# 二十、工程目录建议

建议项目结构：

```text
AgentFuse/
├── pyproject.toml
├── README.md
├── LICENSE
├── config/
│   └── default.toml
├── agentfuse/
│   ├── __init__.py
│   ├── cli.py
│   ├── core/
│   │   ├── workflow.py
│   │   ├── agent_context.py
│   │   ├── events.py
│   │   ├── execution_graph.py
│   │   └── state.py
│   ├── adapters/
│   │   ├── base.py
│   │   └── scripted_adapter.py
│   ├── runtime/
│   │   ├── coordinator.py
│   │   ├── execution.py
│   │   ├── worker.py
│   │   ├── scheduler.py
│   │   └── cancellation.py
│   ├── sharing/
│   │   ├── candidate.py
│   │   ├── share_group.py
│   │   ├── dependency.py
│   │   ├── validator.py
│   │   └── result_delivery.py
│   ├── workspace/
│   │   ├── snapshots.py
│   │   ├── manifest.py
│   │   └── reader.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── source_index.py
│   │   ├── ast_analyzer.py
│   │   ├── dependency_analyzer.py
│   │   └── digest_analyzer.py
│   ├── monitoring/
│   │   ├── metrics.py
│   │   └── resource_monitor.py
│   ├── storage/
│   │   ├── database.py
│   │   ├── models.py
│   │   └── repository.py
│   ├── api/
│   │   ├── app.py
│   │   └── routes/
│   └── web/
│       ├── index.html
│       ├── styles.css
│       └── app.js
├── examples/
│   ├── sample_repository/
│   ├── shared_execution/
│   ├── divergent_execution/
│   └── multi_agent_workflow/
├── benchmarks/
│   ├── runner.py
│   └── scenarios.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── stress/
└── docs/
    ├── ARCHITECTURE.md
    ├── EXECUTION_MODEL.md
    ├── SHARING_PROTOCOL.md
    ├── ADAPTER_GUIDE.md
    ├── BENCHMARK.md
    ├── TEST_REPORT.md
    └── KNOWN_LIMITATIONS.md
```

可以调整结构，但必须保持模块边界清晰。

不要因为追求目录完整而创建大量空文件。

---

# 二十一、必须解决的关键工程问题

开发过程中重点检查以下问题。

## 21.1 共享正确性与运行并发的竞态

必须确保：

- 共享组状态变化原子化。
- 加入和分离过程不会丢失参与者。
- 结果不会重复交付。
- 正在完成的 Leader 不会漏掉新增参与者。
- 资源释放不会发生两次。
- 工作进程异常不会让 Follower 永久等待。

优先设计一个统一的 Share Coordinator 负责所有共享状态转换。

## 21.2 快照不变性

依赖验证依赖稳定的文件状态。

必须确保工具执行过程中使用的快照不会因为外部修改而变化。

首版可以将需要执行的文件复制到 AgentFuse 自己管理的快照目录。

如果发现快照被修改或状态不可信，必须拒绝共享。

不要默认用户工作目录天然不可变。

## 21.3 依赖追踪完整性

不能宣称追踪到了任意程序所有文件操作。

首版只支持通过受控 WorkspaceReader 读取依赖的可信工具。

如果某个工具调用原生文件系统接口，无法确认全部依赖，则禁止共享该工具。

这是设计边界，不是待隐藏的问题。

## 21.4 实验真实性

真实执行次数必须来自 PhysicalExecution。

不能将 LogicalToolCall 减少误认为真实计算减少。

CPU 时间必须来自真实进程监控。

等待时间必须来自真实时间戳。

不能直接按照假定加速比生成性能结果。

## 21.5 Agent Framework 无关性

核心 Runtime 不能直接依赖某个 Agent Framework 的内部 Scheduler 或状态对象。

适配器负责把外部调用边界转换为内部统一事件。

后续应该可以替换 Scripted Agent 为真实的外部 Agent Framework，而不重写共享执行核心。

---

# 二十二、必须提供的项目文档

## README.md

包含：

- AgentFuse 是什么。
- 解决什么问题。
- 系统主要功能。
- 技术栈。
- 安装方法。
- 启动方法。
- CLI 示例。
- Web UI 使用方法。
- 最小演示流程。
- 测试方法。

## ARCHITECTURE.md

包含：

- 系统模块图。
- 模块职责。
- 事件流。
- 执行图模型。
- Runtime 控制流程。
- 数据持久化。

## EXECUTION_MODEL.md

详细说明：

- Workflow。
- AgentInvocation。
- LogicalToolCall。
- PhysicalExecution。
- Resource Scheduling。
- Cancellation。
- Failure Recovery。

## SHARING_PROTOCOL.md

详细说明：

- CandidateKey。
- ShareGroup。
- Dependency Ledger。
- 增量验证。
- Late Join。
- 分离。
- 最终提交验证。
- 一致性与并发处理。

## ADAPTER_GUIDE.md

说明如何把现有多智能体中间层或其他框架连接到 AgentFuse。

## BENCHMARK.md

包含：

- 场景。
- 测试方法。
- 工作负载。
- 性能指标。
- 公平性原则。
- 实际测试结果。

## TEST_REPORT.md

包含：

- 实际测试命令。
- 测试数量。
- 成功与失败结果。
- 运行环境。
- 尚未验证的功能。

## KNOWN_LIMITATIONS.md

必须明确说明：

- 首版仅支持受控工具。
- 不提供通用安全沙箱保证。
- 不支持任意进程的完整依赖追踪。
- 不支持 GPU KV Cache 优化。
- 不支持分布式执行。
- 快照不包含进程内存。
- 内存预算未必等于操作系统硬限制。
- Scripted Agent 不等于 LLM Agent。

---

# 二十三、最终验收标准

只有以下条件全部满足，才能将项目声明为首版完成。

### 系统运行

- [ ] 可在单台电脑上启动。
- [ ] 无需外部 API。
- [ ] 无需外部模型。
- [ ] 可正常运行 CLI。
- [ ] 可打开本地 Web UI。
- [ ] SQLite 能保存真实执行记录。

### 执行与图重建

- [ ] 多个 Agent 可以并发运行。
- [ ] 可区分 Agent 和 Invocation。
- [ ] 可跟踪 LogicalToolCall。
- [ ] 可跟踪 PhysicalExecution。
- [ ] 执行图来自真实事件。
- [ ] 并发 Agent 不会产生错误身份绑定。
- [ ] 工具结果传递具有正确关联。

### 共享执行

- [ ] 等价调用可以共享。
- [ ] 共享后确实减少物理执行次数。
- [ ] 共享结果与独立执行一致。
- [ ] 不相关文件变化不会必然阻止共享。
- [ ] 实际依赖冲突时会阻止错误共享。
- [ ] 在线验证能够在执行过程中发现冲突。
- [ ] 冲突后 Follower 能提前独立执行。
- [ ] 未完成验证的调用无法接收共享结果。
- [ ] 取消和失败不会产生重复结果。

### 资源管理

- [ ] 有 Worker 并发限制。
- [ ] 能记录 CPU 时间。
- [ ] 能记录内存使用。
- [ ] 能处理超时。
- [ ] 能处理取消。
- [ ] 能正确释放资源。

### 产品完整性

- [ ] 提供共享执行演示。
- [ ] 提供动态冲突演示。
- [ ] 提供并发多 Agent 演示。
- [ ] 提供失败与取消演示。
- [ ] 可在 Web UI 查看运行图。
- [ ] 可查看依赖验证记录。
- [ ] 可查看物理执行情况。
- [ ] 可运行 Benchmark。
- [ ] 可对比 Baseline 与 Online Sharing。
- [ ] 有自动化测试。
- [ ] 有完整运行文档。

---

# 二十四、Coding Agent 的执行规则

请严格按照以下原则工作。

## 原则一：先检查现有工程

如果当前目录存在代码：

1. 阅读 README 和项目结构。
2. 识别已有的 Workflow Reconstruction 和 Runtime 相关实现。
3. 检查事件协议。
4. 检查 Agent 身份管理。
5. 检查工具调用与结果传递接口。
6. 检查测试与未提交修改。
7. 制定兼容当前工程的最小改动方案。

不得无故覆盖已有工作。

如果现有模块已具备功能，优先复用。

## 原则二：直接推进实际开发

不要只回复：

- 项目分析。
- 设计建议。
- 技术路线。
- 待办事项。
- 文件目录规划。

必须真正创建代码、执行程序、运行测试和修复问题。

## 原则三：每个阶段必须形成可运行结果

每完成一个阶段：

1. 执行当前可用测试。
2. 检查模块是否真正连接。
3. 检查错误日志。
4. 修复失败。
5. 更新文档。
6. 再继续下一阶段。

不能长期保留无法运行的半成品。

## 原则四：核心正确性优先

共享执行是一项语义敏感的优化。

如果无法证明结果安全复用，就必须执行独立工具计算。

不要为了提高共享率牺牲正确性。

## 原则五：性能收益必须真实测量

不要预设 AgentFuse 一定优于 Baseline。

共享可能引入：

- 等待时间。
- 依赖验证开销。
- 状态管理开销。
- 协调开销。
- CPU 与内存竞争。

必须保留实际测量结果，包括负收益。

## 原则六：维护开发进度

在 `docs/DEVELOPMENT_LOG.md` 中记录：

- 本阶段完成内容。
- 具体文件。
- 已验证功能。
- 遇到的问题。
- 解决方案。
- 下一阶段任务。

不能将未经执行的计划写成已完成。

## 原则七：范围控制

一个月首版只聚焦：

**真实运行图、受控共享执行、在线依赖验证、本地资源管理、Benchmark、可视化。**

如果开发过程中出现范围膨胀，应优先砍掉高级图形效果、复杂缓存和实验性调度功能。

不能砍掉核心正确性验证。

## 原则八：真实交付

最终需要给出：

- 实际实现的模块。
- 项目运行方式。
- CLI 指令。
- Web UI 访问方式。
- 可执行演示。
- 自动化测试结果。
- Benchmark 测量数据。
- 已知限制。
- 后续可扩展方向。

未执行的测试不能标记通过。

没有生成的构建产物不能声称已经交付。

---

# 二十五、立即开始执行

现在开始开发 AgentFuse。

按照以下顺序执行：

**第一步：** 检查当前代码仓库，识别可以复用的工作流重建逻辑和运行事件接口。

**第二步：** 建立最小 AgentFuse Runtime，首先实现一个真实的 Scripted Agent 工具调用。

**第三步：** 接入两个并发 Agent，完成真实事件记录与逻辑调用、物理执行关联。

**第四步：** 实现候选共享组和 In-flight Sharing，使两个相同条件的工具调用真正共享一次物理计算。

**第五步：** 实现版本化工作区和受控文件读取接口。

**第六步：** 实现在线依赖事件、增量验证、冲突分离和独立重执行。

**第七步：** 建立本地资源控制器，限制并发并记录实际 CPU/内存消耗。

**第八步：** 实现完整演示工作流和自动化测试。

**第九步：** 实现本地 Web 管理界面、执行时间线和 Benchmark 对比。

**第十步：** 运行完整测试，修复已发现的问题并完成文档。

不要在开始实现前反复向我确认已明确的技术细节。

对于未指定的实现细节，可以自主作出合理选择，但必须记录会影响正确性的重要假设。

不要把开发阶段写成未来会自动执行的任务。应当在当前运行环境与可用时间内持续完成实际工作，并准确报告进度。

---

# 最终项目愿景

AgentFuse 不是一个普通的 Agent Framework。

它是一个位于 Multi-Agent Framework 和实际执行环境之间的系统优化 Runtime。

它将观察到的工作流执行关系转化为能够真正影响系统执行的控制信息。

其最终核心能力为：

**Observe → Reconstruct → Validate → Share → Schedule → Measure**

即：

观察真实运行事件。

重建已确认的执行关系。

验证跨 Agent 计算是否等价。

共享能够安全复用的实际计算。

调度有限的本地 CPU 与内存资源。

测量共享的正确性、成本和实际收益。

**最终必须交付一个具有真实执行能力、正确性可验证、运行过程可观察、性能效果可测量，并能够与现有多智能体中间层集成的完整单机 Runtime 系统。**

现在开始实际开发，不要只输出设计方案。