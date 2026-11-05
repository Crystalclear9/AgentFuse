# P2 实现与验收

2026-11-05 用户确认：框架优先实接 LangGraph；第三方工具追踪和安全隔离采用 Linux 容器命令范围，记录不完整时禁止共享，不要求覆盖任意操作系统或保证宿主内核无漏洞。原始需求文件保留不变。

## 新增的实际能力

| P2 条款 | 实现与验证 |
|---|---|
| 持久结果缓存 | SQLite 缓存跨重启复用，命中前重新验证调用方依赖；持久缓存集成测试 |
| GPU 调度 | 加速器槽位准入；真实模型选择 NVIDIA Vulkan 设备并执行 GPU 推理 |
| KV 管理 | llama.cpp 原生模型 KV，固定上下文与单槽位预算，模型进程隔离、前缀复用、清空、退出释放 |
| 真实 Agent 推理优化 | SmolLM2 预训练模型生成受约束工具决策，经 Invocation 执行真实工具；冷热 token、决策与工具结果一致，前缀计算量下降 |
| 分布式 Worker | 真实多服务端、TLS、认证、依赖流与远端资源记录；同机多节点实测，多台物理机未实测 |
| 抢占调度 | 高优先级抢占、低优先级重试；结果一致性回归 |
| 路径预测 | 显式父子 Invocation 学习 Agent 路径，工具预测独立统计，不推断身份或建立未观察因果边 |
| 自动适配 | 通用 JSON 进程协议与 LangGraph 专用接入；从工具注册表自动生成节点和 schema，读取实际节点元数据，自动绑定身份、记录 Invocation |
| 第三方依赖追踪 | Linux 原生 strace 跟踪命令及子进程的全部系统调用；输入树与镜像指纹；不同 UID 的监视器防止工具篡改审计；不完整记录拒收 |
| 安全沙箱 | 无外网、只读根和输入、非特权工具 UID、无工具 capabilities、禁止提权、cgroup CPU/内存/PID 与超时上限；越权及审计篡改实测拒绝 |

P2 专用验收命令覆盖以上十个领域，38 项全部通过且零跳过。实测同一提示词首次计算 5 个 token、复用时计算 1 个并重用 4 个；清空后重新计算 5 个，生成 token 一致。显式 CPU 后端亦验证了复用。

本地完整 359 项：358 通过，Windows 符号链接能力跳过 1 项。原计划 78 个编号小节均核对通过证据，原始附件逐字节不变。需求审计 325 项、交付验收 44/44、六组并发矩阵 420 项、58 项核心变异均通过；十分钟运行 1215 轮零错误、零 Worker/RSS 增长。原基线和原容差的五次性能守卫通过。安装后的 wheel 核对 104 个运行时/资源文件，实际 LangGraph、GPU KV 及物理执行 2→1 的共享检查均通过。

完整证据见 `verification/p2-completion-2026-11-05`。最终模型与原生审计改动另由最终源码的 P2 专项覆盖；远端跨平台 CI 最终状态见本地 `.agentfuse/dist/delivery.json`。

## 项目内安装与运行

在项目根目录运行：

```powershell
. ./scripts/enter-local.ps1
python scripts/setup_p2.py --model-mirror https://hf-mirror.com --install-frameworks
# 首次构建本地审计镜像；已有离线镜像时直接 docker load。
python scripts/setup_p2.py --model-mirror https://hf-mirror.com --build-sandbox
.tools/llama/llama-server.exe --list-devices
python -m agentfuse.extensions llm-demo --config config/p2-local.toml
python scripts/verify_p2.py
```

`.tools/llama`、`.tools/models`、`.tools/images` 及 `.venv-local` 保存实际工具、模型、镜像和依赖。下载固定版本并校验 SHA-256；框架依赖固定于 requirements-p2.lock。安装后推理不访问外部服务，不需要模型厂商密钥。WSL/Docker Engine、GPU 驱动仍是系统前提。

本机实测 NVIDIA RTX 4060 Laptop GPU 对应 `Vulkan1`；其他机器须按 `--list-devices` 输出修改 config/p2-local.toml。模型原始来源为 [SmolLM2 GGUF](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF)，修订及哈希见 config/p2-artifacts.json。小模型质量有限；当前实现也可显式配置兼容、可放入本机内存/显存的 GGUF 模型。

纯 CPU 模式设置 `device = "none"` 与 `gpu_layers = 0`。GPU 专用验收会要求实际 NVIDIA Vulkan 设备，不把 CPU 运行记为 GPU 通过。

`agentfuse --config config/p2-local.toml serve` 启动后可使用 `POST /api/extensions/llm`，请求包含 prompt、max_new_tokens、use_cache；`DELETE /api/extensions/llm/cache` 清空原生 KV。后端进程仅监听回环地址，采用每实例生成的私有认证值，应用关闭时终止进程并释放资源；Windows 使用 kill-on-close Job Object，在主进程异常退出后由操作系统回收模型进程树。原生缓存语义以 [llama.cpp server API](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) 为准；不承诺不同 GPU/批次下浮点逐位相同，本机验收比较生成 token 和真实工具结果。

## LangGraph 接入

```python
from agentfuse.adapters.langgraph_adapter import LangGraphAdapter
adapter = LangGraphAdapter(workflow)
# catalogue() 返回版本、schema 和说明；无需为每个工具编写调用包装。
nodes = adapter.discover_nodes()
graph.add_node('analysis', adapter.tool_node('ast_analyzer',
    arguments=lambda state: {'files': state['files']}, result_key='analysis'))
# graph.compile().ainvoke(...)；图结束后 adapter.close()。
```

显式父 Invocation 可通过 configurable.agentfuse_parent_invocation_id 提供。适配器不会把并行执行顺序猜成父子因果关系。用户节点仍遵循 [LangGraph StateGraph 公共 API](https://reference.langchain.com/python/langgraph/graph/state/StateGraph)。

## 容器原生审计边界

```powershell
$image = docker image inspect agentfuse-trace:local --format '{{.Id}}'
python -m agentfuse.extensions sandbox --trace --snapshot agentfuse/resources/sample_repository --image $image -- python -c "print('native')"
```

镜像必须固定为 digest，命令采用 argv，不经过宿主 shell。工具可使用镜像中的 Linux 二进制或输入树中的脚本。审计监视器仅保留跨 UID 跟踪所需的 SETUID/SETGID/SYS_PTRACE，实际工具以 nobody、零 capabilities 运行；私有审计目录仅监视器可访问。

系统调用字符串上限 4096 字节、解码审计上限 4 MiB、输出与容器资源均有上限。超过限制、缺少 strace、监视器失败或超时会拒收/报错并清理容器。记录含被拒绝的访问。`trace_complete` 表示有界监视记录成功，不等于任意程序的纯函数或确定性证明；`dependency_complete=False`、`shareable=False` 保留，时间、熵、调度等输入不用于自动共享。

原生监视器还执行容器内独立超时，调用方被强制终止时也能到期退出并清理。缺少监视器、记录超过限额、调用方退出均已实测，见同目录故障验收记录。

支持范围外的任意操作系统、恶意宿主、内核漏洞以及未验证框架不作保证。历史验收文件是当时结果，不作为当前能力缺口；旧的“未接入真实模型/GPU”当前说明已更新。
