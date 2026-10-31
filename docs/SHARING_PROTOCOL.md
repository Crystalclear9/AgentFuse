# Sharing Protocol

本文档是 AgentFuse 共享执行语义的规范说明。

---

## 0. 首要原则

> **不共享未经验证的结果。**
> 没有证据时宁可不共享。

共享执行是一项语义敏感的优化。任何无法证明结果安全复用的情况，都必须退回独立执行。

---

## 1. CandidateKey

```text
CandidateKey = SHA256(
    ToolName
  + ToolVersion
  + CanonicalArguments
  + ExecutionEnvironmentSignature
)
```

| 组成 | 内容 |
|---|---|
| `ToolName` | 工具名，例如 `ast_analyzer` |
| `ToolVersion` | 工具实现版本，例如 `1.0.0` |
| `CanonicalArguments` | 参数的 canonical JSON（键排序、UTF-8、分隔符固定） |
| `ExecutionEnvironmentSignature` | Python 版本 + 实现 + 工具声明的 `env_signature` |

**候选键相同只代表"可能共享"，不能证明结果等价。**

### 候选键不包含什么

* **不包含 workspace snapshot id。** 两个分支的快照 id 必然不同，如果把它放进
  候选键，跨分支共享将永远不可能，而这恰恰是 AgentFuse 最需要解决的问题。
* 不包含 Agent 身份、调用方、时间戳。

真正决定共享正确性的是工具**实际读取过的依赖**。

### 参数规范化

工具可以通过 `argument_normalizer` 规范化参数。内置的三个文件类工具都使用
`sort_file_arguments`：把 `files` 列表排序。这只影响候选键，不改变工具结果
（这些工具内部本来就按排序顺序读取文件）。

---

## 2. 依赖指纹

指纹由 `agentfuse/workspace/fingerprint.py` 统一定义，**父子进程共用同一份实现**，
避免两侧口径漂移。

| 依赖类型 | 指纹定义 |
|---|---|
| `FILE_READ` | 文件原始字节的 SHA-256 |
| `DIRECTORY_LIST` | 规范化直接子项清单（`"<name>\t<kind>"` 按名字排序，kind ∈ file/dir/symlink）的 SHA-256 |
| `PATH_EXISTS` | `SHA256("exists")` |
| `PATH_NOT_EXISTS` | `SHA256("not_exists")` |
| `FILE_STAT` | `SHA256("<size>|<is_dir>")`（不含 mtime，见下） |

> **为什么 `FILE_STAT` 不包含 mtime？**
> fork 出来的快照内容相同但时间戳可能不同；时间戳对只读工具的结果没有影响。
> 内容层面的差异由同一路径上的 `FILE_READ` 依赖负责表达。

所有路径都是**相对工作区根目录**的规范化 POSIX 路径。`../`、绝对路径、
越界的符号链接一律被 `WorkspaceReader` 与 `DependencyValidator` 拒绝。

---

## 3. ShareGroup

共享组的字段：

| 字段 | 含义 |
|---|---|
| `share_group_id` | 共享组标识 |
| `candidate_key` | 候选共享键 |
| `leader_execution_id` | Leader 的物理执行 |
| `leader_logical_call_id` | Leader 的逻辑调用 |
| `creation_time` / `closed_time` | 生命周期 |
| `status` | `CREATED` / `EXECUTING` / `VALIDATING` / `COMPLETED` / `FAILED` / `CLOSED` |
| `participant_count` | 参与者数量 |
| `participant_calls` | 参与者的**逻辑调用 id 列表**（落库为 JSON） |
| `mode` | 该组运行在 `baseline` / `online` / `deferred` 哪种模式下 |

参与者的完整状态（验证前缀、分离原因、等待时长）在 `share_participants` 表中。

```text
CREATED ──► EXECUTING ──► VALIDATING ──► COMPLETED ──► CLOSED
    │            │              │
    └────────────┴──────────────┴──────────► FAILED ──► CLOSED
```

参与者状态：

```text
WAITING ──► SHARED_WAIT ──► COMPLETED
   │             │  └────► FAILED / CANCELLED
   │             ├────► RUNNING_INDEPENDENTLY ──► COMPLETED / FAILED
   │             └──► DETACHED ──► RUNNING_INDEPENDENTLY ──► COMPLETED
   └──► RUNNING_INDEPENDENTLY (Leader 拥有自己的物理执行)
```

`SHARED_WAIT ──► RUNNING_INDEPENDENTLY` 是**共享执行失败后进入独立重试**的路径：
会被重试的参与者离开这个组，它不是失败的。

非法状态转换会抛出 `IllegalTransition`（见 `sharing/share_group.py`）。

**同一个逻辑调用不允许同时属于两个正在执行的物理任务。**

---

## 4. 依赖账本（Dependency Ledger）

每个 PhysicalExecution 拥有一条**只增**的依赖账本：

```text
R = [r1, r2, ..., rk]
```

参与者维护：

```text
validated_prefix_length   # 已成功验证的前缀长度
validation_status         # MATCH / MISMATCH / UNKNOWN / PENDING
```

账本条目在**读取发生的瞬间**写入，并通过 IPC 立刻送达协调者。

---

## 5. 加入共享组（Late Join 验证）

新调用到达时：

1. 计算 CandidateKey；
2. 按"最新 → 最旧"遍历该 key 下的活跃共享组；
3. 对每个组，用调用者自己的快照验证账本的**已有前缀**：
   * 全部 `MATCH` → 加入该组，参与者 `validated_prefix_length = 已验证条数`；
   * 出现 `MISMATCH` → 记录验证失败，继续尝试下一个组；
   * 出现 `UNKNOWN`（无法在快照中求值）→ 保守处理，继续尝试下一个组；
4. 没有可加入的组 → 创建新组，当前调用成为 Leader。

**Late Join（中途加入）：参与者必须先验证已有全部记录，再订阅后续依赖事件** —— 上面的
"已有前缀"校验正是为此。

---

## 6. 在线增量验证

这是首版的核心能力。

当 Leader 工具读取一个新依赖：

```text
Read Event: FILE_READ(src/main.py, hash=H1)
```

协调者**立即**（不等 Leader 执行结束）：

1. 把记录追加到依赖账本；
2. 读取每个候选参与者**在其自己快照上**的同一依赖指纹；
3. 比较：
   * `MATCH` → 参与者 `validated_prefix_length += 1`，保持 `SHARED_WAIT`；
   * `MISMATCH` → 立即 `DETACH`；
   * `UNKNOWN` → 保守地 `DETACH`；
4. 被分离的调用进入**独立执行队列**，使用自己的快照重新计算。

### 为什么在线验证有价值

```text
t0:  Leader 读取 src/config.py  ──►  Follower 立即发现不一致
t1:  Follower 立刻开始自己的独立执行   ┐
t2:  Leader 继续执行剩余计算            ┴─ 两边并行
t3:  Leader 完成
```

如果等到 Leader 结束后才统一验证，Follower 只能在 `t3` 之后才开始，
白白多等了整段 Leader 的计算时间。`metrics.detach_lead_ms_max` 记录的就是
"冲突被发现时刻比 Leader 完成时刻提前了多少毫秒"。

---

## 7. 分离（Detach）

```text
Agent A:  src/config.py -> hash_A
Agent B:  src/config.py -> hash_B      (hash_A != hash_B)
```

处理：

1. Agent B 从共享组中移除（参与者状态 `DETACHED`，记录原因）；
2. Agent A 的物理执行**继续**；
3. Agent B 使用**自己的工作区快照**创建独立物理执行；
4. 两者分别产生真实结果；
5. 记录分离的依赖、时刻与原因。

### 分离后的再归组

分离往往同时影响同一分支的多个参与者。为了避免它们各自重复计算，
被分离的调用会先尝试加入**同 CandidateKey 的其它活跃组**（逐个验证账本），
只有当没有任何兼容的组时才自己创建一个新组并成为 Leader。

---

## 8. 最终提交验证（Final Commit Validation）

Leader 完成计算后**不能**立即广播结果。必须：

1. 确认工具正常完成（`execution_status == COMPLETED`）；
2. **冻结**本次物理执行的依赖账本；
3. 对每个仍在共享组中的参与者，确认：
   * `validated_prefix_length == len(ledger)`，且
   * `validation_status == MATCH`；
4. 确认工具输出符合上下文无关的共享契约（工具必须是 deterministic + read_only
   且经 `WorkspaceReader` 读取）；
5. 将结果安全绑定到对应的 LogicalToolCall（幂等保护）；
6. 分别发送 `TOOL_RESULT_DELIVERED` 事件。

**任何尚未完成依赖验证的逻辑调用，都不能对外暴露共享执行结果。**
无法通过验证的参与者必须独立执行。

---

## 9. 快照完整性校验

快照是共享正确性的前提：如果一个快照在运行时被外部进程修改，
参与者快照上的依赖指纹就不再描述工具实际读到的状态。

因此协调者在两个位置校验快照完整性：

1. **加入共享组之前**：`SnapshotStore.check_integrity(snapshot_id)` 重新计算
   manifest 并与记录的哈希比对；不一致则**拒绝加入**，该调用改为独立执行；
2. **最终提交之前**：对每个即将接收共享结果的参与者再强制校验一次
   （`force=True`，绕过缓存）；不一致则分离并独立执行。

校验结果按快照缓存，避免同一次运行内重复扫描文件树。

## 10. 会话内已完成结果复用（可选，默认关闭）

除了"正在执行中的调用合并"，协议允许在同一运行会话内扩展**有限的已完成结果复用**：

```text
runtime.result_cache_entries = 64      # 0 = 关闭（默认）
```

命中缓存必须同时满足三个条件：

1. **CandidateKey 相同** —— 同一个工具、同一个版本、同一份规范化参数、同一个环境签名；
2. **快照内容相同** —— 用快照的 manifest 哈希证明（不是快照 id）；
3. **依赖复验通过** —— 原执行观察到的每一条依赖都在调用者自己的快照上重新验证。

因为可共享工具必须是**确定性 + 只读**的，这三个条件合起来即意味着结果相同。
缓存保存在 Runtime 内存里，**不跨进程重启、不跨会话**。

默认关闭是刻意的：它会改变工作流的**可观测行为**（重复请求不再启动新的物理执行），
因此属于需要显式打开的扩展。

## 11. Deferred Validation 模式

`deferred` 模式允许调用加入正在执行的共享组，但**推迟**全部依赖比较：

* 加入时不做账本前缀校验；
* Leader 读取依赖时不触发在线比较；
* 在最终提交阶段一次性验证整个账本。

用途：量化"在线分离"相对"延迟分离"的等待时间差。
实测结果见 `BENCHMARK.md`。

---

## 12. 一致性与并发处理

| 竞争场景 | 处理方式 |
|---|---|
| 已有依赖验证与新依赖写入同时发生 | 单一 `asyncio.Lock` 串行化全部共享状态转换 |
| 参与者加入时 Leader 恰好完成 | 完成路径与加入路径在同一把锁下；完成时仍在组内的参与者照常校验并交付 |
| 参与者取消时共享组正在分发结果 | 取消与交付都在锁下完成；`_deliver_locked` 带有幂等保护，已完成的调用不会被二次交付 |
| 分支分离后又出现相同参数的新调用 | 新调用按 CandidateKey 找到**未关闭**的现存组并重新验证；**已关闭**的组不再复用；分离后的参与者也会尝试再归组 |
| 结果重复交付 | `_deliver_locked` 检查调用状态与 future 是否已完成 |
| 共享执行失败，参与者还在等待 | 等待中的参与者收到 `FAILED`；会被重试的参与者离开组并以 `RUNNING_INDEPENDENTLY` 继续，参与者行与调用行一致 |

---

## 13. 正确性清单

系统必须始终满足：

1. 不共享未经验证的结果。 ✔
2. 不复用可能依赖不同输入状态的结果。 ✔
3. 不将工具参数相同视为计算等价。 ✔
4. 不将不同 Agent 的逻辑调用合并成一个身份。 ✔
5. 共享失败时不会造成结果错发。 ✔
6. 同一调用不会被重复交付。 ✔
7. 分离后使用正确的工作区状态独立执行。 ✔
8. 共享不能改变工具的逻辑输出。 ✔
9. 没有证据时宁可不共享。 ✔
10. 共享必须有可检查的验证记录（`validation_records` 表）。 ✔
11. 快照被篡改时必须拒绝共享（完整性校验）。
12. **参与者的状态与它的逻辑调用状态必须一致**：被标记为 `FAILED` 的参与者不得拿到结果，
    被重试的参与者离组并以 `RUNNING_INDEPENDENTLY` 继续。 ✔
