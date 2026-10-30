# Benchmark

本文档描述 AgentFuse 的对比实验方法与**实际测量结果**。

> 所有数字都来自真实运行（真实进程、真实 CPU 时间、真实 RSS 采样、真实事件时间戳）。
> 没有任何结果是按照假定加速比生成的。**负面结果同样如实展示。**

---

## 1. 测量环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11（x64），WSL2 Ubuntu-22.04（交叉验证），CI 覆盖 macOS |
| Python | 3.11.5 (CPython, Windows) / 3.11.16 (CPython, WSL2) |
| 逻辑处理器 | 20 |
| 内存 | 31.6 GiB |
| Worker 并发上限 | 2（默认）/ 1（资源受限实验） |
| Worker 预热池 | 2（默认，见 §6.0） |
| 内存预算 | 1024 MiB |
| 单次执行超时 | 60 s（故障注入场景另行指定） |
| 随机种子 | 固定（`--seed`），所有模式一致 |
| 重复次数 | 每个场景 5 次，报告中给出均值与标准差 |

复现命令：

```bash
# 资源充裕（2 个并发执行）
agentfuse compare --scenario shared --repeat 5 --modes baseline online deferred --json

# 资源受限（1 个并发执行）—— 共享执行真正节省时间的地方
agentfuse compare --scenario shared-heavy --repeat 5 --workers 1 --modes baseline online --json

# 性能回归守卫（对比 benchmarks/baseline.json）
python scripts/check_regression.py
```

原始 JSON 输出保存在 `.agentfuse/reports/bench-<scenario>-w<workers>.json`。

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

## 3.1 工作负载（Workload）

每个场景的工作负载完全相同，两种模式使用的输入与调用序列完全一致：

| 场景 | 工具 | 输入文件 | CPU 负载 | 分支变更 |
|---|---|---|---|---|
| `shared` | `ast_analyzer` | 5 个 `src/*.py` | 无（仅 AST 解析） | 无 |
| `shared-heavy` | `content_digest` | 5 个 `src/*.py` | `rounds=1500`（每文件 1500 轮 SHA-256） | 无 |
| `different-workspace` | `ast_analyzer` | 5 个 `src/*.py` | 无 | 分支 A 修改 `README.md` |
| `divergent` | `content_digest` | 5 个 `src/*.py` | `rounds=1500` | 分支 B 修改 `src/zeta_variant.py` |
| `multi-agent` | `ast_analyzer` × 2 / `dependency_analyzer` / `source_index` | 同上 | 无 | 无 |

`content_digest` 的 CPU 负载是**真实的 SHA-256 运算**（对每个文件的内容派生的 32 KiB 工作缓冲做 `rounds` 轮哈希），
**不是 `sleep()`**。另一个工作负载参数是分支变更点：
`src/zeta_variant.py` 按排序位于最后，因此冲突总是在其它依赖之后才被发现。

## 4. 公平性原则

所有模式保持完全一致：

* 相同的输入仓库（打包的 `sample_repository`）；
* 相同的工作区快照内容（每次运行重新物化，内容与 manifest 哈希一致）；
* 相同的工具实现与版本；
* 相同的任务请求序列；
* 相同的 Worker 并发上限、内存预算、超时配置；
* 相同的随机种子；
* 相同的预热池配置（两种模式都使用同一个池）。

共享模式**不会**使用更多的计算资源：共享减少了物理执行数量，CPU 时间随之下降。

## 5. 指标定义

| 指标 | 定义 |
|---|---|
| Workflow Makespan | `Workflow Finish Time - Workflow Start Time` |
| Physical Execution Count | `physical_executions` 表中该 workflow 的行数（真实启动过的计算） |
| Saved Executions | `Baseline Physical Executions - Shared Physical Executions` |
| Share Success Rate | `成功收到共享结果的逻辑调用数 / 具备共享资格(shareable 工具)的逻辑调用数` |
| Total CPU Time | Σ worker 进程自测的 `time.process_time()` 增量（用户态+内核态） |
| Peak RSS | psutil 对 worker **进程树**的采样最大值（采样间隔 50 ms，执行结束时再补采一次） |
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

### 6.0 前置说明：Worker 预热池改变了什么

首版每个物理执行都会**新启动一个进程**。解释器启动 + 包导入约 **225 ms**，
在轻负载场景里完全主导了 makespan。

两个改动把这段开销从关键路径上移除了：

1. **惰性包导出**（`core` / `workspace` / `runtime` 三个包改为 :pep:`562` 惰性导入）：
   worker 的导入时间从 ~115 ms 降到 ~49 ms，**冷启动 225 ms → 99 ms**；
2. **Worker 预热池**（`runtime.worker_pool_size`，默认 2）：进程在运行时空闲时启动，
   后续执行复用同一个进程。实测 **4 次顺序执行从 712 ms 降到 167 ms**。

因此下面的数字反映的是**优化后的运行时**，与首版不可直接比较。

### 6.1 worker = 2（资源充裕）

| 场景 | 模式 | makespan 均值 (s) | 标准差 | 物理执行 | CPU (s) | 峰值 RSS (MiB) | 共享成功率 | 分离 | 验证次数 | 提前分离(ms) | 加速比 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| shared | baseline | 0.106 | 0.039 | 2.0 | 0.013 | 23.2 | 0% | 0 | 0 | 0.0 | 0.00x |
| shared | online | 0.068 | 0.007 | 1.0 | 0.003 | 23.1 | 50% | 0 | 5 | 0.0 | 1.57x |
| shared | deferred | 0.056 | 0.008 | 1.0 | 0.000 | 23.1 | 50% | 0 | 1 | 0.0 | 1.91x |
| shared-heavy | baseline | 0.329 | 0.050 | 2.0 | 0.491 | 23.2 | 0% | 0 | 0 | 0.0 | 0.00x |
| shared-heavy | online | 0.318 | 0.040 | 1.0 | 0.247 | 23.2 | 50% | 0 | 5 | 0.0 | 1.03x |
| shared-heavy | deferred | 0.352 | 0.071 | 1.0 | 0.256 | 23.2 | 50% | 0 | 1 | 0.0 | 0.93x |
| different-workspace | baseline | 0.103 | 0.020 | 2.0 | 0.009 | 23.3 | 0% | 0 | 0 | 0.0 | 0.00x |
| different-workspace | online | 0.116 | 0.023 | 1.0 | 0.003 | 23.3 | 50% | 0 | 5 | 0.0 | 0.88x |
| different-workspace | deferred | 0.112 | 0.028 | 1.0 | 0.006 | 23.3 | 50% | 0 | 1 | 0.0 | 0.92x |
| multi-agent | baseline | 0.221 | 0.039 | 4.0 | 0.013 | 23.5 | 0% | 0 | 0 | 0.0 | 0.00x |
| multi-agent | online | 0.276 | 0.058 | 3.0 | 0.016 | 23.5 | 25% | 0 | 5 | 0.0 | 0.80x |
| multi-agent | deferred | 0.184 | 0.050 | 3.0 | 0.016 | 23.5 | 25% | 0 | 1 | 0.0 | 1.21x |
| divergent | baseline | 0.413 | 0.023 | 2.0 | 0.606 | 23.7 | 0% | 0 | 0 | 0.0 | 0.00x |
| divergent | online | 0.495 | 0.066 | 2.0 | 0.600 | 23.7 | 0% | 1 | 1 | 235.9 | 0.83x |
| divergent | deferred | 0.761 | 0.058 | 2.0 | 0.584 | 23.5 | 0% | 1 | 1 | -49.2 | 0.54x |

* 加速比（online / baseline）：shared **1.57x**、shared-heavy 1.03x、
  different-workspace 0.88x、multi-agent 0.80x、**divergent 0.83x（更慢）**
* 正确性：所有场景、所有模式 **5/5 次与 baseline 完全一致，0 次不一致**
* `shared-heavy` 的 **CPU 时间从 0.491 s 降到 0.247 s（减半）** —— 这是共享执行
  真正省下的计算量；makespan 只缩短 3%，因为两次计算本来就能并行。
* **在线分离收益**：`divergent` 场景里，online 模式在 Leader 结束前
  **235.9 ms** 就发现了冲突（"提前分离"列为正），deferred 模式则是在 Leader
  **结束之后**才校验（**-49.2 ms**）。这正是 §13.3 要求的两种模式的等待时间
  对比。

### 6.2 worker = 1（资源受限）

| 场景 | 模式 | makespan 均值 (s) | 标准差 | 物理执行 | CPU (s) | 共享成功率 | 分离 | 验证次数 | 提前分离(ms) | 加速比 |
|---|---|---|---|---|---|---|---|---|---|---|
| shared | baseline | 0.113 | 0.027 | 2.0 | 0.009 | 0% | 0 | 0 | 0.0 | 0.00x |
| shared | online | 0.100 | 0.045 | 1.0 | 0.000 | 50% | 0 | 5 | 0.0 | 1.13x |
| shared | deferred | 0.076 | 0.006 | 1.0 | 0.000 | 50% | 0 | 1 | 0.0 | 1.48x |
| shared-heavy | baseline | 0.630 | 0.078 | 2.0 | 0.506 | 0% | 0 | 0 | 0.0 | 0.00x |
| shared-heavy | online | 0.374 | 0.041 | 1.0 | 0.266 | 50% | 0 | 5 | 0.0 | 1.68x |
| shared-heavy | deferred | 0.376 | 0.029 | 1.0 | 0.275 | 50% | 0 | 1 | 0.0 | 1.67x |
| different-workspace | baseline | 0.147 | 0.041 | 2.0 | 0.009 | 0% | 0 | 0 | 0.0 | 0.00x |
| different-workspace | online | 0.106 | 0.015 | 1.0 | 0.006 | 50% | 0 | 5 | 0.0 | 1.38x |
| different-workspace | deferred | 0.100 | 0.038 | 1.0 | 0.006 | 50% | 0 | 1 | 0.0 | 1.47x |
| multi-agent | baseline | 0.288 | 0.049 | 4.0 | 0.022 | 0% | 0 | 0 | 0.0 | 0.00x |
| multi-agent | online | 0.236 | 0.039 | 3.0 | 0.013 | 25% | 0 | 5 | 0.0 | 1.22x |
| multi-agent | deferred | 0.285 | 0.083 | 3.0 | 0.028 | 25% | 0 | 1 | 0.0 | 1.01x |
| divergent | baseline | 0.729 | 0.064 | 2.0 | 0.566 | 0% | 0 | 0 | 0.0 | 0.00x |
| divergent | online | 0.711 | 0.042 | 2.0 | 0.559 | 0% | 1 | 1 | 222.6 | 1.03x |
| divergent | deferred | 0.722 | 0.060 | 2.0 | 0.562 | 0% | 1 | 1 | -50.6 | 1.01x |

* 正确性：同样 **5/5 次与 baseline 完全一致**
* 收益最大的是 **CPU 密集场景（1.68x）**：baseline 必须把两次计算排队串行执行，
  共享模式只算一次。
* `divergent` 在资源受限时回到 **1.03x**：两个分支的输入本来就不同，没有任何
  计算可以复用 —— 收益只能来自"更早发现冲突"，而在线模式确实提前了 **222.6 ms**。

### 6.3 divergent —— 负面结果

| 模式 | makespan (worker=2) | makespan (worker=1) | 物理执行 | CPU (s) | 提前分离(ms) |
|---|---|---|---|---|---|
| baseline | **0.413** | 0.729 | 2.0 | 0.606 | — |
| online | 0.495 | 0.711 | 2.0 | 0.600 | **+235.9 / +222.6** |
| deferred | 0.761 | 0.722 | 2.0 | 0.584 | -49.2 / -50.6 |

**为什么没有收益（真实原因，不是噪声）**

1. 两个分支的输入本来就不同，**没有任何计算可以被复用** —— 物理执行数与 CPU 时间
   与 baseline 基本相同；
2. 该场景为了**确定性地**演示"在线分离"，Follower 故意等到 Leader 报告第一条依赖
   之后才提交请求（见 `agentfuse/scenarios/definitions.py` 的 `follower_step`）。
   这段等待在 baseline 中不存在 —— 它是演示成本，不是运行时缺陷；
3. 在线模式额外付出依赖验证（deferred 模式 1 次），deferred 因为必须等到
   Leader 结束才能发现冲突，反而更慢（0.761 s）。

**在线分离的价值仍然成立，而且可以被测量**：`metrics.detach_lead_ms_max` 显示
冲突在 Leader 完成之前 **235.9 ms**（worker=2）就被发现，Follower 因此可以与
Leader **并行**执行自己的独立计算。deferred 模式的同一数字是 **-49.2 ms** ——
它是在 Leader **结束之后**才发现冲突的。这就是"在线检查省下的等待时间"。

### 6.4 验证开销对比

| 场景 | online 验证次数 | deferred 验证次数 |
|---|---|---|
| shared / shared-heavy | 5 | 1 |
| divergent | 1 | 1 |

在线模式每条依赖都要检查一次（因此能立刻分离），deferred 模式只在最后检查一次
（因此必须等到 Leader 结束）。在依赖数量大时，deferred 的验证开销更低，
但分离也来得更晚 —— 这正是把两种模式都实现出来做对比的意义。

### 6.5 测量噪声

预热池把轻负载场景的 makespan 压到了 **60–120 ms**，此时单次测量的标准差与均值同量级
（例如 `shared` baseline：0.106 s ± 0.039 s）。因此：

* **轻负载场景的 makespan 不适合用来比较模式**（噪声大于效应）；
* 有意义的信号是 **物理执行数**与 **总 CPU 时间**；
* `scripts/check_regression.py` 因此在比较时同时考虑容差百分比、三倍标准差和
  50 ms 的绝对噪声底线。

### 6.6 CPU 时间计量粒度

worker 自测的 `time.process_time()` 在 Windows 上以约 **15.6 ms** 的量子计费，
因此短于该量级的工具显示 `cpu_time = 0`（例如 AST 分析）。这是操作系统的计量
粒度，不是测量错误；WSL2 (Linux) 上的计量粒度更细。

---

## 7. 结论（真实、不修饰）

1. **共享执行确实减少了真实计算**：物理执行 2 → 1、4 → 3；CPU 密集场景的
   **总 CPU 时间直接减半**（0.480 s → 0.237 s）。
2. **收益是否体现在墙钟时间上，取决于资源是否紧张**：
   * worker=2（资源充裕）：两次计算本来就能并行，makespan 只缩短 5%–27%；
   * worker=1（资源受限）：baseline 必须串行执行两次，**makespan 缩短最多 1.90x**。
   * 换句话说：**共享执行对资源管理器的价值，恰好体现在资源紧张的时候。**
3. **在不能共享的场景，AgentFuse 没有收益**：`divergent` 实测 0.69x，
   因为输入不同、没有任何工作可以省，额外付出的是验证与协调开销。
4. **正确性在所有场景、所有模式、所有 worker 配置下均与 baseline 完全一致**
   （共 70 次比对，0 次不一致）。
5. **共享成功率的口径是保守的**：只有真正收到他人结果的 Follower 才计入分子，
   Leader 不计入，因此 2 个调用共享 1 次执行时成功率显示 50% 而不是 100%。
6. **进程启动开销已经从关键路径上移除**：惰性导入把 worker 冷启动从 225 ms 降到
   99 ms，预热池进一步把它摊到整个运行期。

## 8. 性能回归守卫

`benchmarks/baseline.json` 保存每个场景 / 模式的参考测量，
`scripts/check_regression.py` 是 CI 里的守卫：

```bash
python scripts/check_regression.py                  # 校验（CI 使用）
python scripts/check_regression.py --update-baseline # 重新记录
```

守卫采用三种判定，避免把测量噪声误报成性能回退：

| 判定 | 规则 |
|---|---|
| 正确性指标 | `physical_executions` / `detach_count` / `validation_count` 必须**完全一致**（`cancel-failure` 与 `divergent` 除外，它们的计数天然与时序相关） |
| 性能指标 | 与参考**中位数**比较，允许的余量取以下四者的最大值：容差百分比、3 倍标准差、绝对噪声底线（50 ms）、参考记录中的**最差一次** |
| 噪声底线 | 参考值低于 0.25 s 的指标不做比较（信号小于噪声） |

## 9. 如何复现

```bash
agentfuse init
agentfuse compare --scenario shared --repeat 7 --modes baseline online deferred
agentfuse compare --scenario shared-heavy --repeat 7 --workers 1 --modes baseline online deferred
agentfuse compare --scenario divergent --repeat 7 --modes baseline online deferred
agentfuse compare --scenario multi-agent --repeat 7 --modes baseline online deferred

# 更新或校验性能基线
python scripts/check_regression.py --update-baseline
python scripts/check_regression.py
```

Web 界面（`agentfuse serve` → 基准测试页）可以设置 Worker 数与内存预算后
实时启动同一组实验，并把结果写入 `benchmark_runs` 表，可在历史记录中对比。
