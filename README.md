# AgentFuse

**面向多智能体工作流的共享执行与资源管理 Runtime**

AgentFuse 位于多智能体框架与实际工具执行环境之间。它通过受控接入的调用边界观察
Agent 的真实执行过程，在线重建执行关系，识别不同 Agent 之间潜在的重复工具执行，
并在不同分支和工作区状态下**动态判断计算结果是否能够安全共享**。

核心能力链路：

```
Observe → Reconstruct → Validate → Share → Schedule → Measure
```

> 本项目是一个**真正执行计算并管理计算资源的 Runtime**，不是 Trace Viewer、
> 工作流可视化工具，也不是模拟数据展示程序。

---

## 1. 它解决什么问题

在多智能体工作流中，不同 Agent 经常会对同一批文件执行同一类只读计算
（AST 分析、依赖抽取、内容摘要……）。朴素的做法是每个 Agent 各跑一次：

```
Agent A ──► 物理执行 #1  ┐
                       ├──► 同一份计算做了两遍
Agent B ──► 物理执行 #2  ┘
```

AgentFuse 在这些执行之间建立了一个**受控的共享层**：

* 物理执行只发生一次，多个逻辑调用共享它的结果；
* 共享**不是**基于参数相等猜测，而是基于工具**实际读取过的依赖指纹**逐条验证；
* 一旦发现某个分支的工作区状态不同，参与者会被**立即分离**并使用自己的快照独立执行；
* 任何无法完成验证的调用都**不会**拿到共享结果。

---

## 2. 主要功能

| 能力 | 说明 |
|---|---|
| 统一事件协议 | 22 种运行时事件，workflow 内具备稳定序号，全局唯一 event_id，事件携带因果链（parent_event_id） |
| 在线执行图重建 | Workflow / Agent / Invocation / LogicalToolCall / PhysicalExecution / ToolResult 六类节点，七类边，全部来自真实事件 |
| 受控工具接入 | Tool Registry（6 个真实工具 + 1 个故障注入夹具）+ 受控 `WorkspaceReader`，读取即上报依赖，依赖完整性由运行时强制 |
| 工作区快照 | 不可变快照 + 分支 fork + SHA-256 manifest |
| 共享执行 | CandidateKey → ShareGroup → In-flight Sharing → 增量在线验证 → 冲突分离 → 最终提交门禁 → 快照完整性校验 |
| 资源管理 | FIFO / 最短作业优先调度、Worker 并发上限、内存准入、实测 RSS、CPU 时间、超时、取消、故障回收、**Worker 预热池** |
| 持久化 | SQLite（15 张表，schema v6 + 迁移机制 + 外键约束 + 崩溃回收，后台写入线程） |
| 结果复用 | 会话内已完成结果复用（默认关闭，需完整依赖校验才命中） |
| 本地 Web 界面 | Dashboard / 执行图 / 时间线 / 依赖检查器 / 基准测试，127.0.0.1:8765，无 CDN |
| 命令行 | `init / serve / demo / compare / trace / inspect / tools` |
| 离线演示 | 5 个真实多智能体场景，完全不需要外部模型 |
| 基准测试 | Baseline / Online Sharing / Deferred Validation 三种模式对比 + 正确性逐调用校验 |

---

## 3. 技术栈

| 部分 | 技术 |
|---|---|
| Runtime 核心 | Python 3.11+ / asyncio |
| 计算进程 | subprocess（`python -m agentfuse.runtime.worker_child`），stdin/stdout JSON 行协议 |
| 资源监控 | psutil（进程树 RSS 采样，50ms） |
| 数据存储 | SQLite（WAL，独立写入线程） |
| 本地 API | FastAPI + Uvicorn |
| 前端 | 原生 HTML / CSS / JavaScript（无框架、无构建链、无 CDN） |
| 自动化测试 | pytest + pytest-asyncio + httpx（136 个用例） |
| 配置 | TOML（`config/default.toml`） |
| 命令行 | argparse |

**完全本地运行**：不调用任何模型 API、不需要 API Key、不依赖外部 SaaS、
不需要网络。安装依赖后可以完全离线运行。

---

## 4. 安装

需要 Python 3.11 或更高版本。

### Windows（首要目标平台）

```powershell
# 在项目目录内创建本地虚拟环境（所有文件都留在项目目录中）
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
```

### WSL2 / Linux（已实测验证）

Ubuntu-22.04 自带的 Python 是 3.10，AgentFuse 需要 3.11+。推荐用 `uv` 在
**项目目录内**创建虚拟环境：

```bash
cd /mnt/c/Users/<you>/Desktop/AgentFuse     # 或你的项目路径
uv venv --python 3.11 .agentfuse/wsl/venv
uv pip install --python .agentfuse/wsl/venv/bin/python -e ".[dev]"

# 运行测试
.agentfuse/wsl/venv/bin/python -m pytest tests -q
.agentfuse/wsl/venv/bin/python scripts/acceptance_check.py
```

实测结果见 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) §3（Windows 136 passed /
WSL2 137 passed，两平台验收自检均 18/18）。

> 跨平台实测发现并修复了一个 Linux 专属缺陷：父进程未 `wait()` 回收 worker
> 导致僵尸进程堆积。详见 TEST_REPORT §4。

---

## 5. 启动

```bash
agentfuse init          # 初始化本地数据目录（默认 <repo>/.agentfuse）
agentfuse serve         # 启动本地 Web 管理界面 http://127.0.0.1:8765
```

如果还没有安装为命令，可以直接使用模块入口：

```bash
python -m agentfuse init
python -m agentfuse serve
```

---

## 6. CLI 示例

```bash
# 完全共享场景：两个 Agent 共享一次真实物理执行
agentfuse demo --scenario shared --mode online

# 动态依赖冲突：Follower 在 Leader 运行期间被分离
agentfuse demo --scenario divergent --mode online

# 多 Agent 并行与汇合
agentfuse demo --scenario multi-agent --mode online

# 取消与失败语义
agentfuse demo --scenario cancel-failure --mode online

# 对比实验（Baseline vs Online Sharing vs Deferred Validation）
agentfuse compare --scenario shared --repeat 5 --modes baseline online deferred

# 资源受限场景（worker=1）：共享执行真正节省墙钟时间的地方
agentfuse compare --scenario shared-heavy --repeat 5 --workers 1 --modes baseline online deferred

# 查看一次真实运行的全部事件与运行关系
agentfuse trace <workflow_id>

# 查看一次物理执行的状态、依赖账本与资源采样
agentfuse inspect <execution_id>

# 查看已注册的可控工具
agentfuse tools
```

所有命令支持 `--json`，便于脚本化处理。

---

## 7. Web UI 使用方法

浏览器打开 `http://127.0.0.1:8765`，界面为中文，包含 5 个页签：

1. **总览** — 运行中的 Workflow、各类节点计数、Worker 与 CPU/内存占用、
   最近事件与最近共享/分离事件。
2. **工作流详情** — 由真实事件重建的执行关系图（可点击节点查看详情）、
   逻辑调用 → 物理执行对应表、执行时间线泳道图。
3. **依赖检查器** — 选择物理执行后查看依赖账本：路径、类型、Leader 指纹、
   Follower 验证结果（MATCH / MISMATCH）与失败原因，以及共享组参与者状态。
4. **基准测试** — 选择场景与模式、设置 Worker 数与内存预算，真实启动运行并展示
   柱状图、指标表与正确性校验结论。
5. **工具注册表** — 已注册可控工具的确定性、只读、可共享属性与实现位置。

界面只读取后端真实状态，不包含任何假任务或随机曲线。

---

## 8. 最小演示流程

```bash
agentfuse init
agentfuse demo --scenario shared --mode online
```

预期输出：

```
场景: shared   模式: online   workflow: wf-...
指标       | 数值
---------+---------
工作流耗时    | 0.230 s
逻辑调用数    | 2
物理执行数    | 1        <- 两次逻辑调用只做了一次真实计算
节省执行数    | 1
共享成功率    | 50.0%
分离次数    | 0
验证次数    | 5
```

然后：

```bash
agentfuse serve
# 浏览器打开 http://127.0.0.1:8765/#workflow 查看执行图与时间线
```

资源受限时收益最直观：

```bash
agentfuse compare --scenario shared-heavy --repeat 5 --workers 1 --modes baseline online
# baseline 0.818s / 2 次物理执行  →  online 0.436s / 1 次物理执行  (1.88x)
```

---

## 9. 测试方法

```bash
# 全量测试
pytest -q

# 只跑单元测试
pytest tests/unit -q

# 只跑集成测试（会真实启动 worker 进程）
pytest tests/integration -q

# 压力测试
pytest tests/stress -q

# 交付验收自检（检查目录结构、共享行为、CLI、API、离线依赖）
python scripts/acceptance_check.py

# 性能回归守卫（对比 benchmarks/baseline.json）
python scripts/check_regression.py
```

测试覆盖：图重建、共享正确性、动态验证、并发竞态、资源控制、快照隔离、
完整集成流程（8 条真实端到端流程）以及本地 HTTP API。

实际测试结果见 [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)。

---

## 10. 文档索引

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块图、职责划分、事件流、执行图模型、持久化 |
| [EXECUTION_MODEL.md](docs/EXECUTION_MODEL.md) | Workflow / Invocation / LogicalToolCall / PhysicalExecution / 调度 / 取消 / 故障恢复 |
| [SHARING_PROTOCOL.md](docs/SHARING_PROTOCOL.md) | CandidateKey、ShareGroup、依赖账本、增量验证、Late Join、分离、最终提交门禁 |
| [ADAPTER_GUIDE.md](docs/ADAPTER_GUIDE.md) | 如何把现有多智能体中间层接入 AgentFuse |
| [BENCHMARK.md](docs/BENCHMARK.md) | 场景、方法、指标、公平性原则、**真实测量结果** |
| [TEST_REPORT.md](docs/TEST_REPORT.md) | 测试命令、数量、结果、环境、尚未验证的功能 |
| [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | 首版边界与不保证的事情 |
| [DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md) | 分阶段开发记录 |

---

## 11. 可选特性（默认关闭）

| 配置项 | 默认 | 说明 |
|---|---|---|
| `runtime.worker_pool_size` | 2 | 预热的 worker 进程数；设为 0 则每次执行新建进程 |
| `runtime.result_cache_entries` | 0 | 会话内已完成结果复用；只有工具、参数、快照**内容**完全一致且依赖复验通过才命中 |
| `runtime.scheduling_strategy` | `fifo` | 准入策略；`sjf` = 最短作业优先（吞吐更高、公平性更低） |
| `runtime.max_rss_soft_limit_mb` | 0 | 实测 RSS 软限制；超出后阻止新准入并告警 |

## 12. 许可

MIT，见 [LICENSE](LICENSE)。
