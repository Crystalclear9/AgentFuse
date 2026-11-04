# P2 扩展：配置、执行与验收边界

2026-11-04 用户授权扩大首版范围。所有扩展保持可选，原 CLI 与本地共享语义继续可用。

## 1. 远程 Worker

在每个 Worker 节点安装同一版本 AgentFuse。设置至少 32 字符的随机令牌，通过环境变量传递，勿提交到仓库。

```powershell
# 同机验证可使用回环地址；每个服务端各自限制并发数。
python -m agentfuse.runtime.remote --host 127.0.0.1 --port 8876 --workers 2

# 跨机器必须配置 TLS 证书与私钥。
python -m agentfuse.runtime.remote --host 0.0.0.0 --port 8876 --cert worker.crt --key worker.key
```

服务端读取 `AGENTFUSE_WORKER_TOKEN`。协调端通过配置指定令牌环境变量名，不在配置中保存令牌。
非回环 Worker 必须启用 TLS，证书必须与 host 匹配；私有 CA 可通过 ca_file 指定。

```toml
[[distributed.workers]]
host = "127.0.0.1"
port = 8876
token_env = "AGENTFUSE_WORKER_TOKEN"
timeout = 60

# 可继续添加其他节点；跨机器时使用下列配置。
# [[distributed.workers]]
# host = "worker.example.internal"
# port = 8876
# tls = true
# ca_file = "C:/certificates/ca.pem"
# token_env = "AGENTFUSE_WORKER_TOKEN"
```

随后照常运行 `agentfuse --config <配置路径> demo ...` 或 `serve`。
协调器轮询分配真实物理执行，Worker 本地使用独立子进程执行注册工具，逐条回传依赖；
共享、分离、最终提交和落库继续由原协调器负责。没有远程地址时使用原本地 Worker。

安全与资源约束：

- 先认证，再传输快照；请求体限制 96 MiB，快照限制 5000 文件 / 64 MiB，拒绝路径逃逸和符号链接。
- Python 次版本、平台、工具与 WorkspaceReader 源码指纹必须一致，避免不同实现共享结果。
- 服务端限制并发；执行超时或客户端断开时终止该请求的进程树并清理临时目录。
- `TOOL_EXECUTION_STARTED.metadata.worker_node` 记录目标节点；本地 pid 表示桥接进程，不能当作远端工具 pid。
- 工具 CPU 时间来自远端 Worker；远端进程树 RSS 每 50ms 实测并流式回传，最终采样补齐短任务，直接写入 PhysicalExecution 与资源采样。桥接进程 RSS 不混入远端工具指标。
- 目前只验证同一机器上的独立服务端；多台物理机、TLS 证书校验已在本机独立服务端实测；多台物理机与网络分区尚未实测。
- 这是可信节点之间的注册工具执行协议，不能向不可信节点发送源码；没有自动发现、节点故障迁移或集群一致性协议。

## 2. 真实 K/V 前缀缓存与数值推理

```powershell
python -m agentfuse.extensions inference-demo
python -m agentfuse.extensions inference-demo --weights C:/models/attention.json
```

`AttentionModel` 执行带位置编码的单层因果注意力和贪心自回归解码；缓存保存实际 key/value 张量的
Python 数值表示以及末端 logits。以不可变权重的 SHA-256 和完整 token 前缀作为键，查找最长已缓存前缀。
预算按 Python 对象大小保守计费、LRU 淘汰，模型不同不能复用。缓存前后 tokens / logits 必须逐值一致。

默认权重为确定性、**未训练**的测试夹具，无语言理解或推理质量保证；自定义权重是否经过训练由提供者决定。
这实现了真实数值计算复用，**尚未实现生产 LLM 或 GPU 推理优化**。
`agentfuse.adapters.inference_agent.InferenceAgent` 用模型输出选择已声明的工具动作，
经普通适配器创建 Invocation、记录 INFERENCE_COMPLETED 并发起真实工具调用；缓存前后的动作与工具结果保持一致。
支持 JSON 权重字段：embeddings、query、key、value、output；词表 2..256，隐藏维度 1..64，最多 1024 token。
模型结构刻意有限，不兼容 Hugging Face / GGUF checkpoint，也不宣称是完整 Transformer 服务。

本地 API：

- `POST /api/extensions/inference`：`{"tokens":[1,2,3],"max_new_tokens":8,"use_cache":true}`。
- `DELETE /api/extensions/inference/cache`：释放缓存。
- `GET /api/extensions/capabilities`：查看当前配置。

可配置 `[inference] weights_path = "..."` 与 `max_cache_bytes = 8388608`。
缓存属于各自 AgentFuseApp，不跨 home 或用户共享。

## 3. 路径预测

事件订阅器从真实 `TOOL_CALL_REQUESTED` 学习同一 Invocation 内的工具转移频次，
`GET /api/extensions/predictions?tool=source_index` 返回下一工具概率和观察次数。
统计容器有上限，Invocation 结束后移除活动游标。
概率属于建议，不创建执行图边、不合并身份、不据此共享结果或提前执行任务。
同时从显式 parent_invocation_id 学习 Agent 标签之间的转移，
`GET /api/extensions/agent-paths?label=Supervisor&depth=3` 返回多步未来路径的概率预测。
统计桶使用适配器给出的标签，不推断或合并实体身份；不同工作流的父调用引用不会串线。
概率没有确定性保证，冷启动或未观察过的路径没有证据，不自动调度。

## 4. 第三方命令隔离

要求已运行的 Docker Linux Engine 和已在本地准备好的镜像。运行时 `--pull=never`，不依赖在线拉取。
镜像必须用不可变 SHA-256 digest 指定，不接受 latest 等可变标签。

```powershell
python -m agentfuse.extensions sandbox --snapshot C:/source/snapshot --image python@sha256:<64位摘要> -- python -c "print('hello')"
```

先将输入复制到私有临时目录，记录整个输入树的 SHA-256 manifest，再以只读挂载交给非 root 容器。
容器禁网、根文件系统只读、drop ALL capabilities、no-new-privileges，设置 CPU / 内存 / PID 限额，
仅提供有限 tmpfs。命令退出或超时后强制删除容器。引擎不可用时抛出 SandboxUnavailable，绝不降级到宿主执行。

该后端提供 Docker Linux 的实际隔离机制，边界依赖 Docker 与宿主内核；不承诺抵御所有内核漏洞，
不支持任意 Windows 原生程序。第三方程序的时钟、随机数、C 扩展等隐含输入不能被完整证明，
因此返回 `dependency_complete=False` 和 `shareable=False`。完整输入 manifest 是保守输入记录，
不等同于任意程序的完备动态依赖追踪。隔离执行结果不进入原共享协调器。

Docker 参数依据：[Docker run 官方说明](https://docs.docker.com/reference/cli/docker/container/run/)。

## 5. 验证

```powershell
python -m pytest tests/integration/test_extensions.py tests/unit/test_audit_evidence.py -q

# 使用已准备好的不可变镜像启用真实隔离测试。
$env:AGENTFUSE_SANDBOX_TEST_IMAGE = 'python@sha256:<本地镜像摘要>'
python -m pytest tests/integration/test_extensions.py -q -rs
```

所有“部分实现”均保留在 KNOWN_LIMITATIONS 的 P2 表中。入口存在只证明结构，
行为结果必须以对应测试或实际命令执行记录为准；不把未训练夹具称为真实 LLM Agent。
