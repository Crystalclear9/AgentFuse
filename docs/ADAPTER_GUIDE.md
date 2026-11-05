# AgentFuse 框架接入指南

AgentFuse 在工具执行边界接收调用。框架继续管理 Agent、消息和编排。适配器将实际发生的激活、工具请求、结果交付和显式父子关系记录到同一个工作流中。

| 场景 | 接入方式 |
|---|---|
| Python 框架与 AgentFuse 同进程 | 使用 ExternalAdapter 的 catalogue、invocation、call_tool |
| LangGraph StateGraph | 使用 LangGraphAdapter 的 discover_nodes 或 tool_node |
| LangChain 工具调用 | 使用 LangChainAdapter 的 structured_tool |
| 独立进程、JavaScript 或其他语言 | 使用 UTF-8 JSON Lines v1 |
| 需要映射已有事件生命周期 | 实现 AgentAdapter，使用 bind_agent、begin_invocation、submit_tool_call、deliver_result、end_invocation |

## Python 框架

在框架注册工具时读取 catalogue()，用 name、version、description 和 input_schema 构造工具描述。schema 是独立副本，可以调整框架侧表述。框架实际收到工具请求时调用 call_tool，然后把 result.result 作为工具输出交回框架。

```python
from agentfuse.adapters.external_adapter import ExternalAdapter
from agentfuse.app import AgentFuseApp
from agentfuse.settings import load_settings

async def run_framework_tool(home, workspace):
    app = AgentFuseApp(settings=load_settings(home=home))
    snapshot = app.snapshots.create_workspace(source_dir=workspace)
    workflow = app.create_workflow("my-framework", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    adapter = ExternalAdapter(workflow)
    try:
        tool = next(item for item in adapter.catalogue() if item["name"] == "source_index")
        async with adapter.invocation(
            "planner:instance-1", registration_source="my-framework", identity_confirmed=True
        ) as invocation:
            result = await adapter.call_tool(
                invocation.invocation_id,
                tool["name"],
                {"root": "."},
                tool_version=tool["version"],
                timeout_seconds=30,
            )
        return result.result
    finally:
        adapter.close()
        workflow.finish()
        await app.close()
```

仅在框架能够确认稳定实例 ID 时，调用 bind_agent 或向 invocation 传入 identity_confirmed=True。已确认标签可解析到同一 Agent，每次激活仍创建独立 Invocation。只提供未知标签或不提供标签时，每次激活创建独立的未确认身份。保留内部 agent_id 可以显式引用此前观察到的实例，包括未确认实例。为多个框架共享同一适配器时，给实例 ID 加上框架命名空间。

仅当框架观察到真实父子调用时传 parent_invocation_id。父 Invocation 必须属于同一工作流。一个 Invocation 可以并发等待多次 call_tool，各自返回自己的逻辑调用 ID。退出上下文时自动记录完成、失败或取消。退出前应等待已创建的子任务收尾。

使用 AgentAdapter 自行接入时，submit_tool_call 返回 ToolCallResult，deliver_result 对应框架的接收边界。通过 call_tool 接入会自动记录这一步。框架侧接收事件为 FRAMEWORK_RESULT_RECEIVED，协调器负责 TOOL_RESULT_DELIVERED。

## LangGraph

安装项目的 frameworks 可选依赖后，用 LangGraphAdapter(workflow) 创建适配器。discover_nodes() 返回注册工具对应的节点。需要映射输入状态或输出键时，使用 tool_node。

```python
from agentfuse.adapters.langgraph_adapter import LangGraphAdapter

adapter = LangGraphAdapter(workflow)
graph.add_node("inspect", adapter.tool_node(
    "ast_analyzer",
    arguments=lambda state: {"files": state["files"]},
    result_key="analysis",
))
```

调用方负责构造并运行 StateGraph，图完成后调用 adapter.close()。节点身份来自实际执行元数据，也可以通过 node_name 显式指定。已观察到的父调用可通过 configurable.agentfuse_parent_invocation_id 传递。

## LangChain

frameworks 可选依赖也提供 LangChain Core。LangChainAdapter 将注册表的工具描述和 JSON Schema 导出为原生 StructuredTool，供框架注册和异步调用。导出的工具绑定本次 Invocation，框架应在上下文结束前等待工具调用完成。[StructuredTool 公共接口](https://reference.langchain.com/python/langchain-core/tools/structured/StructuredTool/from_function)

```python
from agentfuse.adapters.langchain_adapter import LangChainAdapter

adapter = LangChainAdapter(workflow)
async with adapter.invocation("analyst", registration_source="langchain") as invocation:
    tool = adapter.structured_tool(invocation.invocation_id, "ast_analyzer")
    output = await tool.ainvoke({"files": ["src/alpha.py"]})
```

## 进程协议 v1

从项目根目录启动服务端，工作目录由调用方选择并在启动时生成快照。将 --home 放在 --workspace 之外，避免把数据库和快照写入输入树。省略 --workspace 时使用示例仓库。只提供同步回调的框架也可以用这个入口，在同一连接上串行完成请求和回复。

```powershell
python -m agentfuse.adapters.external_adapter --home .agentfuse/framework-demo --workspace agentfuse/resources/sample_repository
```

每行发送一个 UTF-8 JSON 对象，每条请求对应一行回复。protocol_version 缺省为 1，request_id 可传字符串或整数，回复会原样带回。使用标准输出传递协议消息，日志从标准错误读取。

同一连接最多并发处理 32 条带 request_id 的 call。先完成各 Agent 的 bind 和 invoke，再连续发送调用，按 request_id 匹配可能乱序到达的回复。请求 ID 在等待回复期间必须唯一。重复的在途 ID 返回不带 ID 的 duplicate_request_id 错误，原请求继续执行。缺少 ID 的调用保持串行。bind、invoke、end 等请求会等待先前提交的调用完成，保持生命周期顺序。

| 请求 type | 必要字段 | 回复 |
|---|---|---|
| discover | 无 | catalogue，包含 protocol_version 和 tools。每个条目包含名称、版本、描述、输入和输出 schema |
| snapshot | 无 | snapshot，包含默认 snapshot_id |
| bind | 无。可传 external_identity、description、source、metadata、identity_confirmed | bound，包含 agent_id |
| invoke | agent_id。可传 parent_invocation_id、metadata | invoked，包含 invocation_id 和内部 agent_id。未知外部标签每次建立独立的未确认身份 |
| call | invocation_id、tool。可传 arguments、tool_version、snapshot_id、timeout_seconds | result，包含 logical_call_id、result 元数据和 output 工具输出 |
| end | invocation_id。可传 status，缺省 COMPLETED，也可用 FAILED 或 CANCELLED | ended，包含 invocation_id |

旧客户端可继续读取 result 元数据字段。新客户端用 output 获取工具输出。错误回复包含 type=error、code、message。工具执行失败额外带 kind 和 execution_id。请求错误和工具失败后，客户端仍可在同一连接发起下一次请求。客户端完成工作后关闭 stdin，服务端会记录尚未结束的 Invocation 为 CANCELLED 并关闭应用。

```powershell
node examples/external_framework/client.mjs python .agentfuse/framework-demo agentfuse/resources/sample_repository
```

这份 Node.js 示例实际启动桥接进程，让两个 Agent 通过同一连接并发调用选定版本的工具，并打印两条逻辑调用及物理执行 ID。Python 外部客户端示例位于 examples/external_framework/agent_fwk.py。

## 维护与验收

框架相关映射放在适配层。Python 框架可以调用进程内接口，跨语言框架可以复用 JSON Lines。使用具体框架 SDK 的接入需验证其工具注册、调用和取消行为。当前专用集成为 LangGraph 和 LangChain，通用协议已用 Python 和 Node.js 客户端验证。

工具仍由 AgentFuse 注册表和快照约束。把外部函数接成可共享工具时，需单独声明工具实现和依赖追踪能力。进程桥接面向受信任的本地客户端，其权限由启动者选择的 home 和工作区确定。

```powershell
python -m pytest tests/integration/test_external_adapter.py tests/integration/test_framework_bridge.py -q
# 安装 frameworks 可选依赖后运行
python -m pytest tests/integration/test_langgraph_adapter.py tests/integration/test_langchain_adapter.py -q
```
