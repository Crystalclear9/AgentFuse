# Development Log

按阶段记录**实际完成**的内容、验证方式、遇到的问题与下一阶段任务。
未执行的事情不会写在这里。

---

## 阶段 1 — 基础设施（S1）

**完成内容**

* `pyproject.toml`（setuptools，`agentfuse` 命令入口，`[dev]` 附加依赖）
* `.gitignore`、`config/default.toml`（runtime / workspace / api / database 四段）
* `agentfuse/settings.py`：home 目录解析（显式参数 > `AGENTFUSE_HOME` > 仓库根 `.agentfuse`）、
  TOML 读取（`tomllib`）、不可变 `RuntimeSettings`
* `agentfuse/core/ids.py`、`core/state.py`（全部状态枚举）、`core/events.py`
  （全局唯一 event_id、workflow 内稳定序号、订阅分发、`wait_for`）
* `agentfuse/storage/database.py`：13 张表 + `PRAGMA user_version` + 后台写入线程
* `agentfuse/storage/models.py`、`storage/repository.py`
* `agentfuse/app.py` 组合根（settings → db → repository → snapshots → registry →
  event bus → scheduler → coordinator）

**已验证**：`agentfuse init` 建库成功；`agentfuse tools` 列出注册表。

---

## 阶段 2 — 受控工具与工作区（S2）

**完成内容**

* `agentfuse/workspace/fingerprint.py`：**唯一**的指纹实现（父子进程共用）
* `agentfuse/workspace/reader.py`：受控读取接口（路径规范化、边界校验、符号链接
  逃逸拒绝、即时上报、重复依赖去重）
* `agentfuse/workspace/snapshots.py`：不可变快照、fork、可写副本、compare、
  完整性校验、文件数/体积上限、排除规则
* 4 个真实工具：`source_index` / `ast_analyzer`（真用 `ast` 解析）/
  `dependency_analyzer`（真解析 import 关系）/ `content_digest`（真 SHA-256 + 真实
  CPU 工作量，不是 `sleep`）

**已验证**：在真实快照上跑出真实结果（9 函数 / 10 import / 4 条本地依赖边）。

**遇到的问题**

* `content_digest` 的工作缓冲区构造是死循环（`sha256` 摘要恒为 32 字节，
  `while len(seed) < 32768` 永不退出）。改为循环追加摘要。
* `fingerprint_stat` 原本包含 mtime，导致 fork 出来的相同内容可能产生不同指纹。
  改为只包含 size 与 is_dir。

---

## 阶段 3 — 执行器与协调器（S3）

**完成内容**

* `agentfuse/runtime/protocol.py`：消息协议
* `agentfuse/runtime/worker.py` + `worker_child.py`：独立进程执行工具，
  依赖事件在读取瞬间流式上报
* `agentfuse/runtime/scheduler.py`：FIFO + Worker 上限 + 内存准入 + 资源快照
* `agentfuse/runtime/coordinator.py`：逻辑调用、物理执行、共享组、交付、取消、
  超时、失败重试的唯一权威

**关键设计决策**

* 最初使用 `multiprocessing`（spawn）启动 worker。改为 **`subprocess` +
  `python -m agentfuse.runtime.worker_child`**：worker 不会重新导入宿主的
  `__main__`（对库使用者更安全），协议是可读的 JSON 行，进程树可可靠终止。
* 协调器的所有共享状态转换都在**单一 `asyncio.Lock`** 下完成。

---

## 阶段 4 — 接入层与执行图（S4）

**完成内容**

* `agentfuse/adapters/base.py`：`AgentAdapter` 抽象基类（六个方法）
* `agentfuse/adapters/scripted_adapter.py`：`ScriptedAdapter` + `ScriptedAgent`
  + `AgentContext` + `ToolStep` / `CallableStep`
* `agentfuse/core/workflow.py`：Workflow 生命周期、Agent 身份绑定、Invocation 管理
* `agentfuse/core/execution_graph.py`：六类节点、七类边，**只由事件构建**

**已验证**：两个脚本 Agent 并发执行，产出 2 个 Agent、2 个 Invocation、
2 个逻辑调用、1 个物理执行，结果哈希一致。

---

## 阶段 5 — 共享执行（S5）

**完成内容**

* `agentfuse/sharing/candidate.py`：CandidateKey（**不含** snapshot id）
* `agentfuse/sharing/share_group.py`：共享组与参与者状态机（非法转换抛异常）
* `agentfuse/sharing/dependency.py`：只增依赖账本
* `agentfuse/sharing/validator.py`：在其它快照上重算指纹并比对（UNKNOWN ≠ MATCH）
* `agentfuse/sharing/result_delivery.py`：结果落盘 + 内容哈希 + 幂等索引
* Coordinator：候选查找、加入、Leader 创建、最终提交门禁、幂等交付

**已验证**：两个等价调用只产生 1 次物理执行，两个逻辑调用得到相同结果。

---

## 阶段 6 — 快照分支与在线验证（S6）

**完成内容**

* 分支创建：fork → 可写副本 → 修改 → 生成新的不可变快照
* 在线依赖验证：Leader 每读一个新依赖立即在每个参与者快照上比对
* 冲突分离：立即 DETACH → 独立执行（使用自己的快照）
* 最终提交门禁：`validated_prefix == len(ledger)` 且全部 MATCH 才交付
* `deferred` 模式：推迟到 Leader 结束后统一验证

**遇到的问题**

* 分离后每个参与者都各自新建执行 → 重复计算。改为：分离后先尝试加入同
  CandidateKey 的其它活跃组，失败才自建新组。压力场景的物理执行数从 6 降到 2。

---

## 阶段 7 — 取消 / 失败 / 资源（S7）

**完成内容**

* Follower 取消、Leader 逻辑调用取消、全部参与者取消
* 超时终止（终止进程树）、worker 崩溃检测、工具业务错误不重试 / 崩溃重试一次
* CPU 时间（worker 自测）、峰值 RSS（psutil 采样进程树）
* `agentfuse/monitoring/metrics.py`：完整指标集

**遇到的问题（重要）**

* **数据库写入线程空转 bug**：`_writer_loop` 的超时分支 `continue` 时没有更新
  `last_flush`，导致下一次 `queue.get(timeout=0)` 立即抛 `Empty` —— 线程 100% CPU
  空转，持续争抢 GIL，使主线程的文件读取从 0.2 ms 劣化到 30–100 ms，
  依赖验证耗时暴涨，整个 workflow 耗时从 0.17 s 变成 0.87 s。
  定位方法：对比 `perf_counter` 与 `thread_time`（发现 CPU 时间为 0，即纯等待），
  再逐个禁用后台线程二分定位。修复后验证耗时回到 0.2 ms。
* worker 进程的 RSS 采样最初拿到的是解释器启动器进程（4 MiB），
  改为采样**进程树最大值**后得到真实值（≈27 MiB）。

---

## 阶段 8 — 演示场景与基准测试（S8）

**完成内容**

* 5 个离线场景：`shared` / `different-workspace` / `divergent` / `multi-agent` /
  `cancel-failure`
* `agentfuse/benchmarks/runner.py`：三种模式对比、重复测量、正确性逐调用校验、
  加速比计算
* `agentfuse/cli.py`：`init / serve / demo / compare / trace / inspect / tools`
* 指标补充：`detach_lead_ms_max`、`online_detach_before_finish`、
  `detach_to_independent_ms`

**关键设计**：`divergent` 场景中 Follower 等待 Leader 报告**第一条**依赖后才提交
请求 —— 这是一个确定性会合点，保证 Follower 一定在冲突被观测**之前**加入共享组，
从而真实演示"在线分离"。冲突文件 `src/zeta_variant.py` 的名字按排序放在最后，
保证冲突在其它依赖都已验证之后才出现。

---

## 阶段 9 — 本地 Web 界面（S9）

**完成内容**

* `agentfuse/api/`：FastAPI 应用 + 5 组路由（overview / workflows / executions /
  benchmarks / tools）
* `agentfuse/monitoring/timeline.py`：泳道时间线构建
* `agentfuse/monitoring/dependencies.py`：依赖账本 + 验证结果合并
* `agentfuse/web/`：中文界面（原生 HTML/CSS/JS，无 CDN、无构建链）
  * 总览、执行关系图（可点击节点）、执行时间线、依赖检查器、基准测试、工具注册表
* 基准测试按钮真实调用后端并轮询任务状态

**已验证**：`agentfuse serve` 后用 Edge headless 截图确认界面渲染真实数据，
截图保存在 `.agentfuse/screenshots/`。

---

## 阶段 10 — 测试与文档（S10）

**完成内容**

* 100 个自动化测试（unit / integration / stress），全部通过
* 文档：README + ARCHITECTURE + EXECUTION_MODEL + SHARING_PROTOCOL +
  ADAPTER_GUIDE + BENCHMARK + TEST_REPORT + KNOWN_LIMITATIONS + 本文件

---

## 阶段 11 — P0 合规收口（对照需求逐条核查后的补齐）

**触发**：对原始需求 25 章做了逐条代码级核查，发现 9 项缺口 + 1 项新发现。

| # | 任务 | 结果 |
|---|---|---|
| A1 | 发布 `SHARE_CANDIDATE_FOUND` | 新建组时与发现同 key 活跃组时各发布一次，metadata 含 `candidate_key/share_group_id/joinable/matched_prefix/validation_status` |
| A2 | 启动时回收孤儿运行 | 新增 `Repository.recover_interrupted_workflows()`：RUNNING 且无 end_time 的 workflow 及其 physical_executions / logical_tool_calls 全部标记 `INTERRUPTED`；`AgentFuseApp` 启动时调用并记录日志 |
| A3 | 统一交付事件 | 新增 `FRAMEWORK_RESULT_RECEIVED`；适配器 `deliver_result` 改用它，Runtime 的权威 `TOOL_RESULT_DELIVERED` 只会出现一次 |
| A4 | 拆分等待与重试事件 | 新增 `TOOL_CALL_RETRYING`，`TOOL_CALL_WAITING` 只表示共享等待 |
| A5 | **实现 schema 迁移机制** | `SCHEMA_VERSION` 1→2；`initialize()` 按 `user_version` 顺序执行 `_MIGRATIONS`，迁移前自动备份到 `<home>/backups/`，`ALTER TABLE ADD COLUMN` 先检查列是否已存在 |

**新发现的缺口**：原实现只有 `CREATE TABLE IF NOT EXISTS`，**没有任何迁移路径**
（§16 要求"初始版本提供自动建表和必要迁移"）。C1 的字段变更依赖它，因此列为 P0。

**已验证**：新增 `tests/unit/test_database.py`（5 例）、`tests/integration/test_events.py`（5 例）。

## 阶段 12 — 测试补齐与工程卫生

| # | 任务 | 结果 |
|---|---|---|
| B1 | 补 4 类缺失用例 | 新增 `tests/integration/test_dependency_types.py`：目录列表变化、文件存在性变化、单 Agent 显式流程；乱序事件用例加入 `test_events.py` |
| B2 | 依赖事件早于执行结束 | 新增显式时间戳断言 |
| B3 | 清理死代码 | 20 处未使用导入、5 处死函数（`_iter_dicts`、`detach_reason_for`、`participants_to_rows`、`waiting_participants` 等）全部清除 |
| B4 | 结构对齐 §20 | 新增 `agentfuse/workspace/manifest.py`（manifest 构造/校验/比较独立成模块）；新增 `agentfuse/scenarios/catalogue.py`（场景说明目录），根目录 `benchmarks/scenarios.py` 改为薄封装 |
| B5 | 修正文档口径 | README 的 WSL2 声明改为"已实测"并给出 uv 安装步骤；TEST_REPORT 同步 |

**顺带修复**：`ExecutionGraph.add_edge` 现在会为尚未观察到的事件创建
**状态为 `UNKNOWN` 的占位节点**（§11.3 "可以展示 UNKNOWN 状态"），
乱序到达的事件不再产生指向不存在节点的边。

## 阶段 13 — P1 能力增强

| # | 任务 | 结果 |
|---|---|---|
| C1 | 资源等待原因可见 | `physical_executions` 新增 `admission_reason` / `queued_time`（走 v2 迁移）；调度器暴露等待队列及**阻塞原因**；API 与 Web 界面显示"哪些执行在等、为什么等" |
| C2 | 快照完整性校验接入验证路径 | 加入共享组之前 + 最终提交之前各校验一次；被篡改的快照拒绝共享（新增 `tests/integration/test_resources_and_integrity.py`） |
| C3 | 更精细的内存准入 | 新增 `max_rss_soft_limit_mb`（实测 RSS 软限制，超限阻止新准入并发布 `RESOURCE_LIMIT_WARNING`） |
| C4 | 两个新工具 | `import_graph`（跨目录 import 图 + 环检测）、`duplicate_blocks`（重复代码块检测，缺失文件通过受控读取接口报告存在性依赖） |

## 阶段 14 — 跨平台实测（WSL2 / Ubuntu-22.04）

**做法**：本机已有 Ubuntu-22.04，但系统 Python 是 3.10 且无 pip、无 sudo。
使用已安装的 `uv` + uv 管理的 CPython 3.11.16，在**项目目录内**创建
`.agentfuse/wsl/venv`，满足"所有文件都在当前文件夹"的约束。

**实测结果**

```text
WSL2  : 125 passed（符号链接逃逸用例在 Linux 上真实执行并通过）
Windows: 124 passed, 1 skipped（符号链接需管理员权限）
两端验收自检: 18/18
```

**发现并修复了一个真实的 Linux 专属缺陷**

* 症状：`test_no_worker_processes_are_leaked` 在 Linux 失败。
* 根因：父进程用 `subprocess.Popen` 启动 worker 后**从未 `wait()`**，
  POSIX 下一个已退出但未回收的子进程会变成**僵尸进程**并持续占用进程表槽位。
* 修复：`runtime/coordinator.py::_reader_loop` 在 EOF 后调用 `process.wait(timeout=5)`。
* 价值：这正是"实测"与"只改文档口径"的区别 —— 该缺陷在 Windows 上不可能复现。

---

## 真实测量结果（5 次重复，worker=2 / worker=1 两组）

| 场景 | baseline (w=2) | online (w=2) | baseline (w=1) | online (w=1) | 物理执行 | 正确性 |
|---|---|---|---|---|---|---|
| shared | 0.230 s | 0.230 s | 0.362 s | **0.201 s** | 2 → 1 | 5/5 一致 |
| shared-heavy | 0.444 s | 0.435 s | 0.818 s | **0.436 s** | 2 → 1 | 5/5 一致 |
| different-workspace | 0.241 s | 0.217 s | 0.337 s | **0.184 s** | 2 → 1 | 5/5 一致 |
| multi-agent | 0.588 s | 0.407 s | 0.720 s | **0.534 s** | 4 → 3 | 5/5 一致 |
| divergent | **0.502 s** | 0.588 s | 0.582 s | 0.576 s | 2 → 2 | 5/5 一致 |

**结论（真实、不修饰）**

1. 共享执行确实减少了真实计算：物理执行 2→1 / 4→3，`shared-heavy` 的
   **总 CPU 时间直接减半**（0.469 s → 0.234 s）。
2. **worker=2（资源充裕）时墙钟时间几乎不变** —— 两次计算本来就能并行；
   **worker=1（资源受限）时加速 1.35x–1.88x** —— baseline 必须串行算两次。
   共享执行对资源管理器的价值恰好体现在资源紧张时。
3. `divergent` 没有收益（0.85x / 1.01x）：输入不同、无工作可省，只剩验证与协调开销。

完整数据见 `BENCHMARK.md`。

---

## 阶段 15 — 复核后的遗留项收口

对照原始需求逐条复核后补齐的三项：

| # | 任务 | 结果 |
|---|---|---|
| R1 | FastAPI `lifespan` | `on_event("shutdown")` 已弃用（复现出 190 条警告），改为 lifespan 上下文管理器，警告清零 |
| R2 | `runtime/execution.py` + `runtime/cancellation.py` | 把物理执行模型与取消/终止语义从 coordinator 中拆出，目录结构与 §20 建议对齐 |
| R3 | 未知节点的 UNKNOWN 占位 | 乱序事件不再产生指向不存在节点的边（§11.3） |

顺带修复：worker 的 stdout/stderr/stdin 管道未显式关闭 → 每次执行泄漏一个文件句柄
（复现出 86 条 ResourceWarning），现在全部关闭，警告清零。

## 阶段 16 — P2 能力补齐

| # | 任务 | 结果 |
|---|---|---|
| P2-1 | **可插拔调度策略** | `runtime/strategy.py`：`fifo`（默认，公平）与 `sjf`（最短作业优先，吞吐更高）；由 `runtime.scheduling_strategy` 选择，配置 `scheduling_strategy` |
| P2-2 | **会话内已完成结果复用** | `sharing/result_cache.py`：CandidateKey 相同 + 快照**内容**相同 + 依赖复验通过才命中；默认关闭（`result_cache_entries = 0`），因为它会改变工作流的���观测行为 |
| P2-3 | **Worker 预热池** | `runtime/pool.py`：worker 变成"多任务"进程（按行读取 payload），池在 Runtime 空闲时预启动；`runtime.worker_pool_size`（默认 2） |
| P2-4 | **性能基线与回归守卫** | `benchmarks/baseline.json` + `scripts/check_regression.py`（容差同时考虑百分比、3 倍标准差与 50 ms 绝对噪声底线） |
| P2-5 | **CI 工作流** | `.github/workflows/ci.yml`：Windows / Ubuntu / macOS × Python 3.11–3.13，跑 pytest + 验收自检 + 性能守卫 |

### 16.1 性能优化：把进程启动移出关键路径

| 改动 | 效果 |
|---|---|
| `core` / `workspace` / `runtime` 三个包改为 :pep:`562` 惰性导出 | worker 导入 115 ms → 49 ms |
| Worker 冷启动（执行开始 → 第一个依赖） | **225 ms → 99 ms** |
| Worker 预热池 | 4 次顺序执行 **712 ms → 167 ms**（4.3x 吞吐） |
| 执行结束时补采一次 RSS | 短执行不再漏采峰值内存 |

## 阶段 17 — 最终基准（7 次重复，双 worker 配置）

| 场景 | baseline (w=2) | online (w=2) | baseline (w=1) | online (w=1) | 物理执行 | 正确性 |
|---|---|---|---|---|---|---|
| shared | 0.068 s | 0.053 s (1.27x) | 0.081 s | 0.063 s (1.29x) | 2 → 1 | 7/7 一致 |
| shared-heavy | 0.322 s | 0.307 s (1.05x) | 0.511 s | **0.269 s (1.90x)** | 2 → 1 | 7/7 一致 |
| different-workspace | 0.069 s | 0.065 s (1.06x) | 0.069 s | 0.063 s (1.09x) | 2 → 1 | 7/7 一致 |
| multi-agent | 0.137 s | 0.122 s (1.12x) | 0.157 s | 0.130 s (1.20x) | 4 → 3 | 7/7 一致 |
| divergent | **0.188 s** | 0.274 s (0.69x) | 0.293 s | 0.287 s (1.02x) | 2 → 2 | 7/7 一致 |

**结论（真实、不修饰）**

1. 共享执行确实减少了真实计算：物理执行 2→1 / 4→3，`shared-heavy` 的
   **总 CPU 时间直接减半**（0.480 s → 0.237 s）。
2. **worker=2（资源充裕）时墙钟时间只缩短 5%–27%** —— 两次计算本来就能并行；
   **worker=1（资源受限）时最多 1.90x** —— baseline 必须串行算两次。
   共享执行对资源管理器的价值恰好体现在资源紧张时。
3. `divergent` 没有收益（0.69x）：输入不同、无工作可省，只剩验证与协调开销；
   该场景为了让"在线分离"可确定性复现，Follower 会等待 Leader 的第一条依赖，
   这段等待是**演示成本**，不是运行时缺陷。
4. 预热池把轻负载场景的 makespan 压到 20–80 ms，此时**测量噪声与效应同量级**，
   轻负载场景应看物理执行数与 CPU 时间，而不是 makespan。
5. 正确性在所有场景 / 模式 / worker 配置下均与 baseline 完全一致（70 次比对，0 次不一致）。

---

## 阶段 18 — CI 暴露的预热池竞态（P0）

CI 在 Windows runner 上报出两个失败，本地无法复现：

| 失败 | 现象 |
|---|---|
| `test_a_pooled_worker_is_reused` | 两次执行的 `worker_pid` 不同 |
| `test_no_worker_processes_are_leaked` | `shutdown()` 之后仍有 worker 进程残留 |

**根因（同源）**：预热线程与首个执行的竞态。

```
t0  预热线程尚未完成，池为空
t1  exec1 → _acquire_worker() → 自建 W0（不是池里的 worker）
t2  预热线程把 W1、W2 放进池 → 池 = [W1, W2]
t3  exec1 结束 → 池已满 → W0 被丢弃
t4  exec2 从池里取 W2 → worker_pid 不同 → 断言失败
```

`shutdown()` 只清理了 `_idle_workers`，而预热线程**未被停止** ——
shutdown 之后新建的 worker 永远不会被回收 → 进程泄漏。

CI 机器更慢，预热耗时超过首个执行，因此只在 CI 暴露。

**修复（F1）**

| # | 改动 | 作用 |
|---|---|---|
| F1-1 | `_acquire_worker()` 等待预热完成（有界，在池锁之外） | 池状态确定化，复用不再依赖机器速度 |
| F1-2 | 池满时淘汰**最旧**的空闲 worker（而不是丢弃刚释放的） | 刚完成任务的 worker 保持热身 |
| F1-3 | 运行时改变池大小时裁剪超额的空闲 worker | `worker_pool_size = 0` 时预热的 worker 也要回收 |
| F1-4 | `shutdown()` 停止并 join 预热线程，然后回收**全部**句柄而不只是空闲的 | 泄漏消失 |
| F1-5 | `WorkerHandle.threads_started` 显式声明；`terminate()` 幂等 | 幂等性 |
| F1-6 | 新增 6 个用例（含**主动制造竞态**） | 防回归 |

## 阶段 19 — 需求条款对齐与自动守卫（P0/P1）

复核发现的两处**按名称**未落实的条款，以及一个缺口：没有自动化守卫。

| # | 任务 | 结果 |
|---|---|---|
| F2-1 | 资源控制器按规范命名为 **`LocalResourceController`**（保留 `ResourceScheduler` 别名） | §9.1 逐字命中 |
| F2-2 | ShareGroup 新增 **`participant_calls`** （逻辑调用列表）+ schema **v3 迁移** | §4.8 逐字命中 |
| F3-1 | SHARING_PROTOCOL 显式使用 `Late Join` 术语 | §22 章节 8/8 |
| F3-2 | BENCHMARK 新增「工作负载」小节 | §22 章节 6/6 |
| F3-3 | KNOWN_LIMITATIONS 用词对齐规范列举项 | §22 章节 8/8 |
| F4-1 | 新增 `scripts/audit_requirements.py`：把 25 章的核对表**代码化** | **139 条款项全部通过** |
| F4-2 | 审计纳入验收自检（18 → **19** 项）与 CI | 回归即红 |

**验证门禁（四项）**

```
pytest tests -q                       145 passed, 1 skipped
python scripts/acceptance_check.py    19/19
python scripts/audit_requirements.py  139/139
python scripts/check_regression.py    no performance regression detected
```

---

## 下一阶段建议（P3）

1. 跨会话持久化结果缓存（需要持久化依赖账本 + 快照内容寻址）。
2. 抢占式调度：长任务在等待队列过长时让出槽位。
3. worker 进程内的工具沙箱（只读挂载 / seccomp），把 `KNOWN_LIMITATIONS` 里的
   "不提供操作系统级隔离"往前推进一步。
4. 对接一个真实的第三方 Agent 框架，验证适配器契约的通用性。
5. 在 macOS 上执行一次完整验证（CI 矩阵已包含）。
6. 长时间（数小时）运行稳定性与内存增长观测。
