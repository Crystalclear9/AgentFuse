# Benchmark

本文档描述 AgentFuse 的对比实验方法与**实际测量结果**。

> 所有数字都来自真实运行（真实进程、真实 CPU 时间、真实 RSS 采样、真实事件时间戳）。
> 没有任何结果是按照假定加速比生成的。**负面结果同样如实展示。**

---

## 1. 测量环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11（x64），WSL2 Ubuntu-22.04（交叉验证） |
| Python | 3.11.5 (CPython, Windows) / 3.11.16 (CPython, WSL2) |
| 逻辑处理器 | 20 |
| 内存 | 31.6 GiB |
| 单次执行超时 | 60 s（故障注入场景另行指定） |
| 随机种子 | 固定（`--seed`），所有模式一致 |
| 重复次数 | 每个场景 5 次，报告中给出均值与标准差 |

复现命令：

```bash
# 资源充裕（2 个 worker）
agentfuse compare --scenario shared --repeat 5 --modes baseline online deferred --json

# 资源受限（1 个 worker）—— 共享执行真正节省时间的地方
agentfuse compare --scenario shared-heavy --repeat 5 --workers 1 --modes baseline online deferred --json
```

原始 JSON 输出保存在 `.agentfuse/reports/bench-<scenario>[-w1].json`。

## 2. 比较模式

| 模式 | 说明 |
|---|---|
| `baseline` | 所有工具调用独立执行，不启用任何共享 |
| `online` | 候选识别 + 在线动态依赖验证 + 分离机制 + 共享结果分发 |
| `deferred` | 允许加入正在执行的候选组，但推迟到 Leader 结束后统一验证依赖 |

## 3. 场景

| 场景 | 内容 | 预期 |
|---|---|---|
| `shared` | 两个 Agent、同一快照、同一 AST 分析请求 | 2 逻辑调用 → 1 物理执行 |
| `shared-heavy` | 两个 Agent、同一快照、同一内容摘要请求（真实 CPU 负载） | 2 逻辑调用 → 1 物理执行，CPU 减半 |
| `different-workspace` | 两个分支（分支 A 只改 README），分析相同的 `src/*.py` | 快照不同但依赖一致 → 仍然共享 |
| `divergent` | 两个分支在 `src/zeta_variant.py` 上内容不同 | 在线检测冲突 → 分离 → 各自独立执行 |
| `multi-agent` | Supervisor → A/B/C → Aggregator | A 与 B 共享，C 与 Aggregator 独立 |
| `cancel-failure` | 取消 / 超时 / 崩溃 / 共享失败 | 不产生错误结果、不永久等待、不泄漏资源 |

## 4. 公平性原则

所有模式保持完全一致：

* 相同的输入仓库（打包的 `sample_repository`）；
* 相同的工作区快照内容（每次运行重新物化，内容与 manifest 哈希一致）；
* 相同的工具实现与版本；
* 相同的任务请求序列；
* 相同的 Worker 并发上限、内存预算、超时配置；
* 相同的随机种子。

共享模式**不会**使用更多的计算资源：共享减少了物理执行数量，CPU 时间随之下降。

## 5. 指标定义

| 指标 | 定义 |
|---|---|
| Workflow Makespan | `Workflow Finish Time - Workflow Start Time` |
| Physical Execution Count | `physical_executions` 表中该 workflow 的行数（真实启动过的进程数） |
| Saved Executions | `Baseline Physical Executions - Shared Physical Executions` |
| Share Success Rate | `成功收到共享结果的逻辑调用数 / 具备共享资格(shareable 工具)的逻辑调用数` |
| Total CPU Time | Σ worker 进程自测的 `time.process_time()` 增量（用户态+内核态） |
| Peak RSS | psutil 对 worker **进程树**的采样最大值，采样间隔 50 ms |
| Shared Wait Time | 参与者从加入共享组到收到结果或被分离的持续时间 |
| Validation Overhead | 验证次数、验证累计耗时、验证失败次数、分离次数 |
| Online Detach Benefit | 冲突首次被观测的时刻 与 Follower 独立执行开始时刻 之差 |
| Correctness | 逐调用比对 online/deferred 与 baseline 的 `(输入 manifest 哈希, 结果哈希)` |
| Speedup | `Baseline Makespan / AgentFuse Makespan` |

### 共享成功率的分母

分母 = **该 workflow 中使用 shareable 工具（deterministic 且 read_only）的逻辑调用总数**。
分子 = **实际收到由其它 Agent 的物理执行产生的结果的逻辑调用数**（即共享组的 Follower）。
Leader 拥有自己的物理执行，不计入分子 —— 这是保守口径。

---

## 6. 实测结果

### 6.0 两组配置：资源充裕 vs 资源受限

这是本报告最重要的结论，必须先说清楚：

* **worker = 2（默认）**：两个 Agent 的执行本来就能**并行**跑完，共享执行节省的是
  **计算量**（CPU 时间减半），**不缩短墙钟时间**；makespan 由 worker 启动开销主导。
* **worker = 1（资源受限）**：baseline 必须把两次计算**排队串行**执行，共享模式只算一次
  —— 这时共享执行同时节省**计算量**和**等待时间**，makespan 明显下降。

一个资源管理器真正解决的是第二种情况，因此下面同时给出两组数据。

### 6.1 worker = 2（资源充裕）

| 场景 | 模式 | makespan 均值 (s) | 标准差 | 物理执行 | CPU (s) | 峰值 RSS (MiB) | 共享成功率 | 分离 | 验证次数 |
|---|---|---|---|---|---|---|---|---|---|
| shared | baseline | 0.230 | 0.033 | 2.0 | 0.003 | 23.9 | 0% | 0 | 0 |
| shared | **online** | **0.230** | 0.037 | **1.0** | 0.006 | 26.0 | 50% | 0 | 5 |
| shared | deferred | 0.201 | 0.031 | 1.0 | 0.003 | 24.9 | 50% | 0 | 1 |
| shared-heavy | baseline | 0.444 | 0.006 | 2.0 | **0.469** | 27.7 | 0% | 0 | 0 |
| shared-heavy | **online** | **0.435** | 0.006 | **1.0** | **0.234** | 27.5 | 50% | 0 | 5 |
| shared-heavy | deferred | 0.423 | 0.012 | 1.0 | 0.228 | 27.6 | 50% | 0 | 1 |
| different-workspace | baseline | 0.241 | 0.018 | 2.0 | 0.013 | 25.2 | 0% | 0 | 0 |
| different-workspace | **online** | **0.217** | 0.033 | **1.0** | 0.003 | 24.6 | 50% | 0 | 5 |
| different-workspace | deferred | 0.213 | 0.024 | 1.0 | 0.006 | 25.6 | 50% | 0 | 1 |
| multi-agent | baseline | 0.588 | 0.037 | 4.0 | 0.006 | 25.8 | 0% | 0 | 0 |
| multi-agent | **online** | **0.407** | 0.023 | **3.0** | 0.013 | 25.2 | 25% | 0 | 5 |
| multi-agent | deferred | 0.410 | 0.024 | 3.0 | 0.019 | 25.0 | 25% | 0 | 1 |
| divergent | baseline | **0.502** | 0.017 | 2.0 | 0.234 | 27.6 | 0% | 0 | 0 |
| divergent | online | 0.588 | 0.028 | 2.0 | 0.234 | 27.6 | 0% | **1** | 4 |
| divergent | deferred | 0.603 | 0.014 | 2.0 | 0.225 | 27.6 | 0% | **1** | 1 |

* 加速比（online）：shared 1.00x、shared-heavy 1.02x、different-workspace 1.11x、
  multi-agent 1.44x、**divergent 0.85x（更慢）**
* 正确性：**所有场景、所有模式 5/5 次与 baseline 完全一致，0 次不一致**
* `shared-heavy` 的 **CPU 时间从 0.469 s 降到 0.234 s（减半）** —— 这是共享执行
  真正省下的计算量；makespan 不变是因为两次计算本来就能并行。

### 6.2 worker = 1（资源受限）

| 场景 | 模式 | makespan 均值 (s) | 标准差 | 物理执行 | CPU (s) | 共享成功率 | 分离 |
|---|---|---|---|---|---|---|---|
| shared | baseline | 0.362 | 0.020 | 2.0 | 0.006 | 0% | 0 |
| shared | **online** | **0.201** | 0.022 | **1.0** | 0.000 | 50% | 0 |
| shared | deferred | 0.183 | 0.008 | 1.0 | 0.006 | 50% | 0 |
| shared-heavy | baseline | 0.818 | 0.024 | 2.0 | 0.450 | 0% | 0 |
| shared-heavy | **online** | **0.436** | 0.008 | **1.0** | 0.228 | 50% | 0 |
| shared-heavy | deferred | 0.427 | 0.013 | 1.0 | 0.225 | 50% | 0 |
| different-workspace | baseline | 0.337 | 0.012 | 2.0 | 0.003 | 0% | 0 |
| different-workspace | **online** | **0.184** | 0.005 | **1.0** | 0.003 | 50% | 0 |
| different-workspace | deferred | 0.180 | 0.004 | 1.0 | 0.000 | 50% | 0 |
| multi-agent | baseline | 0.720 | 0.023 | 4.0 | 0.013 | 0% | 0 |
| multi-agent | **online** | **0.534** | 0.022 | **3.0** | 0.019 | 25% | 0 |
| multi-agent | deferred | 0.560 | 0.012 | 3.0 | 0.013 | 25% | 0 |
| divergent | baseline | 0.582 | 0.028 | 2.0 | 0.225 | 0% | 0 |
| divergent | online | 0.576 | 0.011 | 2.0 | 0.241 | 0% | **1** |
| divergent | deferred | 0.589 | 0.014 | 2.0 | 0.225 | 0% | **1** |

* 加速比（online）：**shared 1.80x、shared-heavy 1.88x、different-workspace 1.84x、
  multi-agent 1.35x、divergent 1.01x**
* 正确性：同样 **5/5 次与 baseline 完全一致**

### 6.3 divergent —— 负面结果（两种配置下都没有收益）

| 模式 | makespan (worker=2) | makespan (worker=1) | 物理执行 | CPU (s) |
|---|---|---|---|---|
| baseline | **0.502** | 0.582 | 2.0 | 0.234 |
| online | 0.588 | **0.576** | 2.0 | 0.234 |
| deferred | 0.603 | 0.589 | 2.0 | 0.225 |

**为什么没有收益（真实原因，不是噪声）**

1. 两个分支的输入本来就不同，**没有任何计算可以被复用** —— 物理执行数与 CPU 时间
   与 baseline 基本相同；
2. 该场景为了确定性地演示"在线分离"，Follower 故意等到 Leader 报告第一条依赖
   之后才提交请求（见 `agentfuse/scenarios/definitions.py` 的 `follower_step`）。
   这段等待在 baseline 中不存在；
3. 在线模式额外付出 4 次依赖验证（deferred 模式 1 次）。

**在线分离的价值仍然成立**：`metrics.detach_lead_ms_max` 显示冲突在 Leader
完成之前约 **40–140 ms** 就被发现，Follower 因此可以与 Leader **并行**执行自己的
独立计算；deferred 模式必须等到 Leader 结束才能发现冲突。

### 6.4 验证开销对比

| 场景 | online 验证次数 | deferred 验证次数 |
|---|---|---|
| shared / shared-heavy | 5 | 1 |
| divergent | 4 | 1 |

在线模式每条依赖都要检查一次（因此能立刻分离），deferred 模式只在最后检查一次
（因此必须等到 Leader 结束）。在依赖数量大时，deferred 的验证开销更低，
但分离也来得更晚 —— 这正是把两种模式都实现出来做对比的意义。

### 6.5 CPU 时间计量粒度

worker 自测的 `time.process_time()` 在 Windows 上以约 **15.6 ms** 的量子计费，
因此短于该量级的工具显示 `cpu_time = 0`（例如 AST 分析）。这是操作系统的计量
粒度，不是测量错误；WSL2 (Linux) 上的计量粒度更细。

---

## 7. 结论（真实、不修饰）

1. **共享执行确实减少了真实计算**：物理执行 2 → 1、4 → 3；CPU 密集场景的
   **总 CPU 时间直接减半**（0.469 s → 0.234 s）。
2. **收益是否体现在墙钟时间上，取决于资源是否紧张**：
   * worker=2（资源充裕）：两次计算本来就能并行，makespan 基本不变（1.00x–1.44x）；
   * worker=1（资源受限）：baseline 必须串行执行两次，共享模式只算一次，
     **makespan 缩短 1.35x–1.88x**。
   * 换句话说：**共享执行对资源管理器的价值，恰好体现在资源紧张的时候。**
3. **在不能共享的场景，AgentFuse 没有收益**：`divergent` 实测 0.85x（worker=2），
   因为输入不同、没有任何工作可以省，额外付出的是验证与协调开销。
4. **正确性在所有场景、所有模式、所有 worker 配置下均与 baseline 完全一致**
   （共 40 次比对，0 次不一致）。
5. **共享成功率的口径是保守的**：只有真正收到他人结果的 Follower 才计入分子，
   Leader 不计入，因此 2 个调用共享 1 次执行时成功率显示 50% 而不是 100%。

## 8. 如何复现

```bash
agentfuse init
agentfuse compare --scenario shared --repeat 5 --modes baseline online deferred
agentfuse compare --scenario shared-heavy --repeat 5 --workers 1 --modes baseline online deferred
agentfuse compare --scenario divergent --repeat 5 --modes baseline online deferred
agentfuse compare --scenario multi-agent --repeat 5 --modes baseline online deferred
```

Web 界面（`agentfuse serve` → 基准测试页）可以设置 Worker 数与内存预算后
实时启动同一组实验，并把结果写入 `benchmark_runs` 表，可在历史记录中对比。
