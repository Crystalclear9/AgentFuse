# Test Report

## 1. 测试命令

```bash
# 全量
pytest -q

# 分目录
pytest tests/unit -q
pytest tests/integration -q
pytest tests/stress -q

# 交付验收自检
python scripts/acceptance_check.py
```

## 2. 测试环境

| 项 | Windows | WSL2 |
|---|---|---|
| 操作系统 | Windows 11 (x64) | Ubuntu-22.04 (WSL2, kernel 6.18) |
| Python | 3.11.5 (CPython) | 3.11.16 (CPython, uv 管理) |
| 解释器 | `.venv`（项目目录内） | `.agentfuse/wsl/venv`（项目目录内） |
| 依赖 | fastapi 0.162, uvicorn 0.54, psutil 7.2.2, pytest 9.2, pytest-asyncio 1.4.1, httpx 0.28.1 | 同左（uv 安装） |
| 逻辑处理器 | 20 | 与宿主共享 |
| 内存 | 31.6 GiB | 与宿主共享 |

## 3. 测试结果

### 3.1 Windows 11

```text
124 passed, 1 skipped in 35.10s
```

### 3.2 WSL2 / Ubuntu-22.04

```text
125 passed in 85.01s
```

**两个平台的差异只有那一个跳过项**：符号链接逃逸测试在 Windows 上需要管理员
权限才能创建符号链接，因此被跳过；在 Linux 上它**真实执行并通过**。

### 3.3 交付验收自检

| 平台 | 结果 |
|---|---|
| Windows 11 | 18/18 项通过 |
| WSL2 | 18/18 项通过 |

### 3.4 用例分布

| 目录 | 数量 | 结果 |
|---|---|---|
| `tests/unit` | 65 passed, 1 skipped（Windows）/ 66 passed（Linux） | 全部通过 |
| `tests/integration` | 41 passed | 全部通过 |
| `tests/stress` | 2 passed | 全部通过 |

### 3.5 单元测试覆盖

| 文件 | 覆盖内容 |
|---|---|
| `test_candidate.py` | 候选键稳定性、参数/版本/环境签名敏感性、候选键不含快照、canonical JSON |
| `test_reader.py` | 受控读取、依赖上报类型、路径越界拒绝、符号链接越界拒绝、重复依赖去重 |
| `test_snapshots.py` | 排除规则、快照不可变性、manifest 一致性、fork 隔离、compare、删除、文件/体积上限、可写副本 |
| `test_share_group.py` | 参与者/共享组状态机、非法转换拒绝、等待时长计算 |
| `test_events_and_graph.py` | event_id 唯一性、workflow 内序号、订阅分发、图只包含被观察到的边 |
| `test_tools.py` | 工具注册、可共享性、6 个工具的真实输出、确定性、schema 校验 |
| `test_scheduler.py` | Worker 上限、内存预算阻塞、死锁避免、FIFO、快照数据 |
| `test_database.py` | 新建库 schema、v1→v2 迁移、迁移前备份、孤儿 workflow 回收、已完成记录不被回收 |

### 3.6 集成测试覆盖

| 文件 | 覆盖内容 |
|---|---|
| `test_shared_execution.py` | 等价调用共享一次执行、结果一致、逻辑调用身份独立、baseline 不共享、参数/工具不同不共享、不可共享工具不共享、无关文件变化仍可共享、脚本 Agent 产生真实执行图 |
| `test_divergent.py` | 冲突依赖导致分离、分离发生时 Leader 仍在运行、分离后使用自己的快照、依赖账本完整、迟到加入者不得复用已完成的执行、场景级指标 |
| `test_dependency_types.py` | 目录列表变化被检测、文件存在性变化被检测、单 Agent 显式流程、依赖事件早于执行结束 |
| `test_events.py` | `SHARE_CANDIDATE_FOUND` 被发布、交付事件只出现一次、适配器边界不重复权威事件、重试用独立事件、乱序事件不产生错误边 |
| `test_concurrency.py` | 10 个并发相同调用只产生 1 次执行、无重复交付、Follower 取消不影响 Leader、Leader 取消不影响 Follower、全部取消终止执行、共享失败释放所有参与者、崩溃检测、超时终止、无 worker 进程泄漏 |
| `test_resources_and_integrity.py` | RSS 软限制阻止准入、等待队列给出等待原因、资源快照字段、被篡改的快照不参与共享、完整快照通过校验 |
| `test_scenarios.py` | 5 个演示场景的期望指标、聚合器必须在 worker 之后启动、失败/取消场景覆盖全部 5 种情况 |
| `test_api.py` | 健康检查、静态资源、总览、工具列表、workflow/graph/timeline/events、execution/dependencies、404、基准测试端到端 |
| `test_benchmark.py` | 执行次数真实下降、结果与 baseline 一致、divergent 场景两侧结果正确、deferred 验证次数更少、跨运行哈希稳定、正确性检查能发现差异 |

### 3.7 压力测试覆盖

| 文件 | 覆盖内容 |
|---|---|
| `test_concurrency_race.py` | 10 个 Agent 分属两个分支 → 每个结果与其分支一致、物理执行数收敛为 2、重复提交不产生重复执行 |

## 4. 跨平台验证发现并修复的真实缺陷

WSL2 实测（而非仅修改文档）暴露了两个 **Linux / 跨系统专属问题**：

### 4.1 未回收的 worker 进程（僵尸进程）

* **症状**：`test_no_worker_processes_are_leaked` 在 Linux 上失败 —— 一个 worker
  进程在调用完成后仍然存活。
* **根因**：父进程用 `subprocess.Popen` 启动 worker 后**从未调用 `wait()`**。
  在 POSIX 系统上，已退出但未被回收的子进程会变成**僵尸进程**，持续占用进程表
  槽位，直到父进程退出。Windows 没有这个问题，所以 Windows 测试无法发现它。
* **修复**：`runtime/coordinator.py::_reader_loop` 在读到 EOF 后调用
  `process.wait(timeout=5)` 回收子进程。
* **验证**：修复后 Windows 124 passed / WSL2 125 passed，两个平台都通过。

### 4.2 跨操作系统的 SQLite WAL 锁

* **症状**：在 WSL2 里运行 CLI 时报 `sqlite3.OperationalError: disk I/O error`。
* **根因**：**同一个 home 目录被两个 AgentFuse 实例同时使用**（Windows 上运行着
  Web 服务端，WSL2 里跑 CLI）。SQLite 的 WAL 需要 `-shm` 共享内存索引与字节范围锁，
  这在跨操作系统的挂载盘上无法共享，第二个实例拿不到锁。
* **修复**：
  1. `Database._connect()` 在 WAL 失败时回退到回滚日志模式（`TRUNCATE`），
     以适配本身不支持 WAL 的文件系统；
  2. 两种模式都失败时抛出**明确的诊断信息**（指出可能是另一个实例占用了
     同一 home 目录，并给出 `AGENTFUSE_HOME` / `--home` 的解决建议），
     而不是把晦涩的 `disk I/O error` 直接抛给用户。
* **验证**：停止并发实例后，WSL2 CLI 与验收自检均通过（18/18）。

## 5. 验收项与对应测试

| 验收项 | 验证方式 |
|---|---|
| 两个 Agent 真实并发运行 | `test_two_scripted_agents_produce_a_real_execution_graph` |
| 区分 Agent 与 Invocation | `test_events_and_graph.py::test_the_graph_only_contains_observed_relations` |
| 跟踪 LogicalToolCall / PhysicalExecution | `test_shared_execution.py` 全部 |
| 执行图来自真实事件 | `test_events_and_graph.py` |
| 等价调用可以共享 | `test_two_equivalent_calls_share_a_single_physical_execution` |
| 共享后确实减少物理执行次数 | `test_the_comparison_reports_a_real_reduction` |
| 共享结果与独立执行一致 | `test_the_shared_results_are_identical_to_the_baseline` |
| 不相关文件变化不阻止共享 | `test_an_unrelated_file_change_does_not_prevent_sharing` |
| 实际依赖冲突阻止错误共享 | `test_a_conflicting_dependency_detaches_the_follower` |
| 目录列表变化被检测 | `test_a_changed_directory_listing_is_detected` |
| 文件存在性变化被检测 | `test_a_changed_file_existence_is_detected` |
| 在线验证在执行过程中发现冲突 | `test_the_detection_happens_while_the_leader_is_still_running` |
| 冲突后 Follower 提前独立执行 | `detach_lead_ms_max` + `test_the_detection_happens_while_the_leader_is_still_running` |
| 未完成验证的调用无法接收共享结果 | `test_a_late_joiner_validates_the_existing_ledger` |
| 取消与失败不产生重复结果 | `test_no_logical_call_is_delivered_twice`、`test_concurrency.py` |
| Worker 并发限制 | `test_scheduler.py::test_the_worker_limit_is_enforced` |
| 记录 CPU 时间与内存 | `benchmark_runs.total_cpu_time` / `peak_rss` + `resource_samples` 表 |
| 处理超时与取消 | `test_a_hanging_worker_is_terminated_by_the_timeout` 等 |
| 正确释放资源 | `test_no_worker_processes_are_leaked`（两个平台） |
| 崩溃后状态可识别 | `test_interrupted_workflows_are_recovered` |
| 快照被篡改时拒绝共享 | `test_a_modified_snapshot_is_not_shared` |
| 提供共享/冲突/并发/失败演示 | `test_scenarios.py`（5 个场景全部断言） |
| Web UI 可查看运行图与依赖 | `test_api.py`（graph / timeline / dependencies 端点） |
| 可运行 Benchmark 并对比 | `test_the_benchmark_endpoint_runs_a_comparison` |

## 6. 尚未验证的功能

以下内容**没有**被自动化测试覆盖，如实记录：

1. **真实外部 Agent 框架接入**：`ADAPTER_GUIDE.md` 描述的是接口契约，
   本仓库只提供了 Scripted Agent 这一种实现，没有对接任何第三方框架。
2. **长时间运行的资源稳定性**：压力测试只覆盖秒级场景，
   "运行数小时不残留进程" 未做长时间验证。
3. **多用户并发访问同一个 Web 服务**：接口按单进程单 runtime 设计，
   未做并发写入压力测试。
4. **磁盘故障 / 断电时的数据一致性**：SQLite 事务 + WAL 保证普通异常下的完整性，
   但未做断电模拟。迁移前的自动备份只覆盖 schema 升级，不覆盖数据损坏。
5. **macOS**：只验证了 Windows 11 与 WSL2/Ubuntu 两个平台。
6. **符号链接逃逸在 Windows 上未执行**（需要管理员权限创建符号链接）。

## 7. 性能数据

见 `BENCHMARK.md` 第 6 节（5 个场景 × 3 种模式 × 2 种 worker 配置 × 5 次重复）。
