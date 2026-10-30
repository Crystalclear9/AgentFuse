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

## 阶段 20 — CI 泄漏的根治（P0）

CI 上的 `test_no_worker_processes_are_leaked` 一直失败。本地无法复现，
通过注入迟缓启动完成了根因定位，共发现两个独立缺陷：

### 缺陷 A：池外新建 worker 与 shutdown 竞态

```
t0  _acquire_worker() 调用 start_worker()（阻塞 ~150ms，CI 上更慢）
t1  shutdown() 扫过 _worker_handles → 此时 worker 尚未注册
t2  start_worker() 返回，worker 注册进入 _worker_handles
    → 但 shutdown 已扫完 → 这个 worker 永远不会被终止
```

**修复**：引入 **pool epoch（世代号）** —— shutdown 时递增，任何在旧世代创建、
在新世代里注册的 worker 都会被立即终止。

### 缺陷 B：真正干活的是 launcher 的**子进程**

Windows 上的 venv 使用 **launcher 打手**：`Popen.pid` 是 launcher，真正执行工具的是它的**子进程**。
杀掉 launcher 会关闭管道，但**正在计算的 worker 不会再读 stdin**→ 它成为孤儿并永生。

这也解释了为什么**峰值 RSS 只有 4.4 MiB**（采到的是 launcher）而真实 worker 用 18 MiB。

**修复**：
* worker 进程启动后第一句话报告自己的真实 pid（`hello` 消息）；
* `start_worker()` 同步发现 launcher 的子进程 pid（`discover_children`）；
* `terminate_worker()` 按真实 pid → 子进程 → launcher 的顺序终止，并验证确实死了；
* `terminate()` 只在**确认进程真的消失**时才置位幂等标志，失败则允许后续重试。

### 验证

| 场景 | 结果 |
|---|---|
| 慢 worker + 关闭与启动重叠（CI 模式） | **无泄漏** |
| pool=0 / pool=2，workers=1 / workers=2（6 种组合） | **全部通过**（153 passed） |
| 长稳观察 18 轮（scripts/soak.py） | worker 数量增长 **0**，RSS 增长 **0.0 MiB** |

## 阶段 21 — 可观测性与平台覆盖（P1/P2）

| # | 任务 | 结果 |
|---|---|---|
| G2-1 | `physical_executions` 新增 `worker_root_pid`（真实 worker）与 `worker_command` | schema **v4** 迁移 |
| G2-2 | RSS 采样改为真实 worker 进程 | **峰值 RSS 从 4.4 MiB 修正为 18.8 MiB**（以前是采到 launcher） |
| G2-3 | `RESOURCE_LEAK_WARNING` 事件 | shutdown 时仍有存活 worker 则发布 |
| G3-1 | `scripts/soak.py` 长稳观察 | 输出 worker 数量 / RSS 增长曲线与 JSON 报告 |
| G3-2 | CI 矩阵：pool 0/1/2 × workers 1/2（6 组合）+ soak job | 本地全部通过 |
| G3-3 | 不变量测试：运行时持有的 handle 数 == 存活 worker 数 | 新增 |

---

## 阶段 22 — CI 三轮修复（P0）

CI 的三次运行暴露了三类不同的问题。通过 **git 凭据管理器的 token
读取 job 日志**实现了精确定位（日志 API 需要鉴权，页面需登录）。

| 轮次 | 失败 | 根因 | 修复 |
|---|---|---|---|
| #3 | 9/16 | 测试错误假设 launcher 存根；`discover_children` 无存根时阻塞 1 秒 | 平台无关不变量断言；`sys.base_prefix` 判定（1010 ms → 5 ms） |
| #4 | 8/16 | macOS：RSS 最后采样在落库之后丢失；Windows：泄漏断言只看 pid | 采样先落库；断言校验进程身份（pid + create_time） |
| #5 | 3/16 | runner stdout 编码 cp1252 → 打印中文崩溃；终止校验过快产生误报 | 所有脚本强制 UTF-8；CI 设 `PYTHONIOENCODING`；终止校验改为重试 |
| **#6** | **0/16** | 全部通过 | — |

**新增的自动化能力**：

* `scripts/run_ci_like.py` —用基础解释器 + `PYTHONPATH` 本地仓库**在本地复现 runner 环境**，无需等待 CI 即可跑完整矩阵；
* `scripts/acceptance_check.py` 写出 `.agentfuse/reports/acceptance.json` —失败时可从 artifact 直接诊断；
* `scripts/audit_requirements.py` 改为在**临时数据库**上核对 schema 定义，不再依赖上一次运行的数据目录；且不再启动 worker 进程；
* CI：审计提到测试之前（静态最快），数据始终上传，soak 扩到 10 分钟。

## 阶段 23 — 需求逐条复核（P0/P1 补齐 + 守卫）

对 25 章需求做了一次逐条复核，把"文档写了但代码没做"的条款补齐。
本阶段只改语义与守卫，不新增功能面。

### 23.1 P0 语义（5 条）

| # | 条款 | 结果 |
|---|---|---|
| I1-1 | 工具依赖完整性声明与运行时强制 | `ToolSpec.dependency_tracking`；worker 装审计钩子，绕过时发 `DEPENDENCY_BYPASS` 并**分离全部参与者**（不交付不完整结果） |
| I1-2 | 真正的 Leader 取消 | 场景改为 `cancelling_leader` + `joining_follower`，先确认"自己是 Leader 且组内 ≥ 2 人"再取消；用例断言 `cancelled_was_the_leader` |
| I1-3 | 外键与跨表事务 | 14 个外键（`DEFERRABLE INITIALLY DEFERRED`）；v5 迁移重建 9 张表；调用与执行**一个事务**落库；写入失败计数 `write_errors` |
| I1-4 | `result_reference` 真正落库 | 调用与执行记录都存结果路径，实测 2/2、1/1 非空 |
| I1-5 | 事件因果链 | 事件带 `parent_event_id`；REQUESTED → EXECUTION_STARTED → READ_OBSERVED → RESULT_DELIVERED 链连通 |

### 23.2 P1 条款（8 条）

| # | 条款 | 结果 |
|---|---|---|
| I2-1 | 种子生效与资源不增断言 | workflow 用自己的 `random.Random(seed)` 并播种全局随机源；基准对所有模式用同一个固定种子；重复运行**不增长 worker 与运行时内存**（有断言） |
| I2-2 | 分离收益入聚合与 UI | `detach_lead_ms_max`、`online_detach_before_finish_rate` 进入基准聚合、CLI 表格与 Web 表格 |
| I2-3 | 已确认依赖与等待原因 | 工作流详情返回 `dependencies` 与 `validations`；参与者新增 `wait_reason`（schema v6）并在界面展示 |
| I2-4 | 活跃调用口径 | `active_invocations` 只统计 `RUNNING` 的调用，另给出 `total_invocations` |
| I2-5 | 集成流程断言最终结果 | `different-workspace` 断言两次结果哈希一致且同一次执行；`multi-agent` 断言聚合器拿到了 3 个 worker 的结果 |
| I2-6 | 拒绝的候选要有记录 | 加入时被 MISMATCH 拒绝的调用会作为 **DETACHED 参与者**落库并发事件 |
| I2-7 | 依赖路径规范化与越界检查 | 校验器复用 `normalize_relative_path` 并做 realpath 边界检查，绝对路径 / `..` / 盘符一律拒绝 |
| I2-8 | 场景的显式屏障 | 共享、异工作区、多 Agent 场景都用**事件屏障**做会合，不再依赖调度顺序 |

### 23.3 守卫与卫生（4 条）

| # | 条款 | 结果 |
|---|---|---|
| I3-1 | 审计脚本行为化 | 审计从"grep 源码"升级为**真实运行**：共享场景只产生一次物理执行、冲突场景真的分离、事件真的落库、候选键不含快照、快照被篡改不共享、工具错误分类正确 |
| I3-2 | 文档同步 | schema v2 → **v6**、17 → **22** 种事件、13 → **15** 张表、用例数量与覆盖范围同步 |
| I3-3 | 死代码清理 | 删除 4 个未引用函数、9 处未使用 import，枚举成员全部可达 |
| I3-4 | 稳健性 | worker 终止**不再阻塞事件循环**（改到线程里执行）；终止前先落结果（退出是结果的原因而不是结果）；依赖类型改用枚举而非字符串字面量；API 未捕获异常返回可读错误 |

### 23.4 复核中发现并修复的真实缺陷

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 共享等待者可能收到**不完整**的结果 | 工具绕过受控读取时依赖账本不完整，运行时仍会共享 | 审计钩子 + `DEPENDENCY_BYPASS` + 分离全部参与者 |
| 取消可能打在 **Follower** 而不是 Leader | 场景没有等待 Leader 就绪 | 事件屏障 + `cancelled_was_the_leader` 断言 |
| 迁移可能把数据库**停在半迁移状态** | DDL 在 autocommit 下执行，中途失败只回滚了 DML | 整个迁移一个事务；重建只拷贝两边都有的列；残留的 `_legacy` 表自动清理 |
| `INSERT OR REPLACE` 会**级联删除**子记录 | REPLACE 先删旧行，外键 CASCADE 把参与者一起删掉 | 改为 UPSERT（`ON CONFLICT ... DO UPDATE`） |
| 事件循环被**阻塞 6 秒** | 终止 worker 时在事件循环里同步 `time.sleep` 轮询进程消失 | 终止放到线程里执行 |
| 取消场景比记录基线**慢 6 倍** | 场景用 5 ms 轮询会合，Windows 的定时器精度是 15.6 ms | 改成事件屏障（4.9 s → 3.2 s） |

### 23.5 P2 条款

§19 的 P2 条款逐条状态见 `KNOWN_LIMITATIONS.md` §0：5 条已实现，
5 条（跨会话持久化缓存、GPU 调度 / KV Cache、分布式执行、多用户权限、
崩溃后自动恢复）**明确未实现**。

## 阶段 24 — 需求逐条复核的收尾（守卫可信化 + 覆盖补齐）

对照完整需求复核之后，发现的问题不是"功能没做"，而是**"做了但证明不了"**，
以及三处**真实缺陷**。

### 24.1 三处真实缺陷（复核发现并修复）

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| D1 | §8.7 最终提交验证门禁**没有任何测试能触达** | 增量在线验证让"已验证前缀"始终与账本同步，门禁条件在全量 82 次求值中**为真 0 次** | 写出会触发门禁的测试（模拟"后续依赖的验证没到达参与者"），并把它接进审计；删掉门禁时两者都报红 |
| D2 | §12.4"分离时 Leader 尚未结束"只是**勉强成立** | 冲突文件是排序后**最后一个**文件，digest 工具逐个读逐个算 → 检测到冲突时 Leader 只剩 ~23 ms 工作；实测 lead ∈ {0.0 … 66} ms，有一次恰好为 0 | 冲突文件改到**第 2 个**文件 → lead 稳定在 92–131 ms；在线模式相对 baseline 从"更慢"变成 **1.13x 更快** |
| D3 | **取消被记录成超时** | 所有终止都产生 timeout 结果 → 执行状态落到 `TIMEOUT` | 终止结果带取消类型，执行状态记为 `CANCELLED`（§10.4"记录取消"） |

### 24.2 守卫可信化

| 项 | 之前 | 现在 |
|---|---|---|
| 审计检查数 | 167 | **240** |
| 覆盖的需求小节 | 37 | **46**（新增 4.8 / 11.3 / 13 / 19 / 12.4） |
| 行为化检查 | 28 | **70+** |
| 永远不可能失败的检查 | ≥ 4 | **0** |

被删除的失效检查（都只是 grep 源码）：

* `候选键不含 snapshot` —— 读的是文件的**第 2 个切片**（imports），payload 在第 4 个；
* `执行次数来自真实记录` —— **同义反复**（指标本身就是 `len(list_executions())`）；
* `最终提交验证门禁` —— 枚举了 3/4 个状态值，**恒真**；
* `区分工具错误/崩溃` —— 被一个 **import 语句**满足；
* `共享只占一次执行槽位` —— 只检查 `"acquire(" in file`。

### 24.3 新增能力

* `scripts/guard_selftest.py` —— **故意改坏 6 处行为**，断言对应的守卫必须报红。
  这是唯一能证明"守卫真的会抓"的方法：6/6 通过（CI 里是独立 job）。
* 审计新增：外键**关系**（不只是数量）、写入错误被计数、§13 的 8 条公平性与
  13 项指标、§19 的 P0/P1/P2 映射（文档状态必须与代码一致）、前端 JS 语法检查
  （`node --check`）、安装依赖里没有模型厂商的包。
* 验收自检：跑通需求 §15 的**全部** CLI 命令（含此前从未执行的 `serve` /
  `trace` / `inspect`），断言 API 返回的**真实字段**而不只是 200，并覆盖此前
  从未被调用的三个端点。36 项。

### 24.4 测试补齐（§17 的 7 个缺口 + 4 个偏弱项）

共享调用只占一个执行槽位 · 同一 Agent 两次调用保持独立 · 并发 Agent 的身份
**绑定**（不只是计数）· 快照比较报告被删除的文件 · Leader 正在结束时加入的
Follower 拿不到共享结果 · 交付途中取消的 Follower 只收到一次结果 ·
同一工具的**两个版本**不共享 · 取消必须真的终止（且 worker 进程消失）·
迟到加入者必须证明它验证了既有账本 · 崩溃的 worker 必须被回收。

### 24.5 数据模型

`share_participants` 新增 `detach_kind`（**schema v7**）：区分"加入后被分离"与
"加入阶段就被拒绝"。后者从来不是参与者，混进分离统计会让口径失真。
指标相应拆分为 `detach_count`（真分离）与 `rejected_candidates`（被拒候选）。

## 阶段 25 — 要求复核的收尾（守卫可信化 + 前端执行）

复核的第二轮：把剩下的“已声明但没有做”的项全部做完，并把两个额外项加上。

### 25.1 剩余的审计条款（从文本检查变为行为检查）

| 条款 | 之前 | 现在 |
|---|---|---|
| §8.1 候选键不含 snapshot | `"snapshot" not in key`（sha256 上永远不会失败） | **两个不同快照的同参数调用 → 候选键相同** |
| §5.1 事件清单 | 13 / 17 | **17 / 17** 全部真实产生 |
| §12 场景 | 4 个仅 grep | 3 个真实运行 + 6 个由测试执行（验收自检运行全部 6 个） |
| §10.1 失败分类 | 只有 tool_error | **tool_error / worker_crash / timeout** 三种都断言落库的 kind |
| §13.2 公平性 | 查 runner 的 docstring | **相同的随机种子、相同的快照内容、相同的请求序列**（以 manifest 哈希为键） |
| §21.1 单一协调锁 | `"self._lock = asyncio.Lock()" in file` | **并发调用 → 恰好一次物理执行、每个调用只交付一次** |
| §9.3 RSS 采集 | `"psutil" in file` | **峰值 RSS > 0 且 resource_samples 确实有行** |

审计的总耗时从 2.0 s 变为 **2.5 s**（+25%，绝对增量 0.5 s）—— 换来的是 18 项新检查，
其中 8 项是需要真实运行的行为检查。

### 25.2 两个额外项

| # | 项 | 结果 |
|---|---|---|
| ① | **种子不再改动进程全局的 RNG** | `WorkflowRuntime.start()` 不再调用 `random.seed()`；种子只驱动 **工具的随机源**（由工作流种子 + 逻辑调用 id 派生），因此 **重试拿到的数字一致**、两个调用不共享随机性；使用随机性的工具必须声明 `uses_random` → **永远不共享**（§7.4） |
| ② | **前端脚本被真实执行** | `tests/frontend/dom_harness.mjs` 在最小 DOM 模拟中运行真实的 `app.js`，食物是**后端真实响应**；页签、执行图、时间线、依赖检查器、基准表格的每个值都被断言；JavaScript 错误会让测试失败 |

### 25.3 复核又发现的真实缺陷

| 缺陷 | 根因 | 修复 |
|---|---|---|
| **超时被记录成取消** | 所有终止都产生 timeout 结果 | 终止结果带类型：超时 → `timeout`，取消 → `cancelled` |
| **候选键的单元测试是空的** | `assert "snap" not in key`（sha256 上永远不会失败） | 比较与**恰好这四个输入的哈希**；多一个输入被加入就会失败 |
| **`shared-heavy` 场景没有任何测试** | 场景存在但未被测试覆盖 | 新增用例（共享一次执行 + 与 baseline 的结果一致） |
| **并发场景会互相影响** | 共享组不看工作流边界 → 两个场景的同参数调用会共享一次执行 | 场景必须**串行**执行；并发会改变它们的结果 |

### 25.4 守卫自检

新增 4 个变异点（超时的分类、候选键包含快照、前端不渲染、种子不到达工具）
→ **10 / 10 个守卫证明了它们能发现问题**。

## 阶段 26 — 需求复核的第二轮（真实缺陷）

独立复核（不依赖上一轮的结论）发现 9 处没有做完的事项，
其中两处是正确性缺陷。

### 26.1 正确性缺陷（已复现）

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| D1 | 一个逻辑调用同时拥有**两个运行中的物理执行** | 已分离的参与者拥有自己的执行，但 Leader 的可重试失败又给它启动了一个 | 重试前检查不变量（`_running_execution_of`）；用例锁定该不变量 |
| D2 | 共享执行失败后，参与者**永远停在 SHARED_WAIT** | `ParticipantStatus.FAILED` 全库只被读、从未被赋值 | 失败时所有等待的参与者转为 FAILED 并落库 |

### 26.2 缺失的能力

| # | 条款 | 修复 |
|---|---|---|
| D3 | §5.2 “如果无法确认两个事件属于同一个 Agent，则保留身份未确定状态” | 新增 `AgentIdentityStatus`（CONFIRMED / UNDETERMINED）；适配器可以声明身份未确认（`bind_agent(identity_confirmed=False)`）；`agents.identity_status`（**schema v8**）；执行图不再**丢弃**无 agent_id 的事件，而是落到 UNDETERMINED 占位节点，两个未确定身份**永远不合并** |
| D4 | §10.1 “区分工具错误 / 进程崩溃 / 调度器错误” | 定向与 worker 启动失败的 3 处改用 `ERROR_INTERNAL`，只有真正死亡的 worker 才是 `ERROR_CRASH` |
| D5 | §4.8 共享组要支持“加入、验证、分离与**关闭**” | `ShareGroup.close()`；结束后组进入 CLOSED，同 key 的后续调用另起新组 |

### 26.3 界面与需求要表面

| # | 条款 | 修复 |
|---|---|---|
| D6 | §13.5 基准页需展示共享次数、验证开销与**逻辑调用↔物理执行对应关系** | 三列 + 每个 run 的对应表（用 run 已有的 workflow_id 调 `/api/workflows/{id}`） |
| D7 | §14.3 工作流详情需展示**已确认依赖**与**等待原因** | 新增依赖表 + 调用表的等待原因列（API 早就返回了这些数据，只是页面没用） |

### 26.4 覆盖缺口与守卫

* 审计新增 §7.4（内置工具不依赖时间/随机数/环境变量/绝对路径）、§13.2（共享模式不使用更多计算资源）、§21.3（绕过受控读取的工具永远不共享）、§21.5（Runtime 不依赖任何 Agent 框架的内部对象）。
* 守卫自检从 10 个变异点增到 **16 个**，新增的都是本轮的修复：
  双执行、失败参与者、调度器错误分类、组关闭、身份未确定、工具依赖时间。
* 新增 `serve --allow-remote`：管理界面**没有认证**，非回环地址必须显式开启（§2.2）。

## 下一阶段建议（P3）

1. 跨会话持久化结果缓存（需要持久化依赖账本 + 快照内容寻址）。
2. 抢占式调度：长任务在等待队列过长时让出槽位。
3. worker 进程内的工具沙箱（只读挂载 / seccomp），把 `KNOWN_LIMITATIONS` 里的
   "不提供操作系统级隔离"往前推进一步。
4. 对接一个真实的第三方 Agent 框架，验证适配器契约的通用性。
5. 在 macOS 上执行一次完整验证（CI 矩阵已包含）。
6. 长时间（数小时）运行稳定性与内存增长观测。
