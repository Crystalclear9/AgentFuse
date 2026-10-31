# Adapter Guide

本文档说明如何把现有多智能体中间层（或任意 Agent 框架）接入 AgentFuse。

---

## 1. 为什么需要适配器

AgentFuse 位于框架与执行环境之间。它**不关心**框架内部如何调度 Agent，
只关心在**真实的调用边界**上观察到了什么：

* 哪个 Agent 被激活了；
* 它发起了哪个工具请求；
* 结果交付给了谁；
* 谁显式地调用了谁。

这些事实由适配器翻译成统一的 `RuntimeEvent`。适配器是唯一需要框架相关代码的地方。

## 2. 最小接口

```python
from agentfuse.adapters.base import AgentAdapter


class MyFrameworkAdapter(AgentAdapter):
    async def bind_agent(
        self,
        external_identity: str | None = None,
        agent_description: str | None = None,
        registration_source: str | None = None,
        metadata: dict | None = None,
    ) -> AgentHandle:
        ...

    async def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict | None = None,
    ) -> InvocationHandle:
        ...

    async def submit_tool_call(
        self,
        invocation_id: str,
        tool_name: str,
        arguments: dict,
        snapshot_id: str | None = None,
    ) -> ToolCallResult:
        ...

    async def deliver_result(self, invocation_id: str, logical_call_id: str, result) -> None:
        ...

    async def end_invocation(self, invocation_id: str, status: str = "COMPLETED") -> None:
        ...

    async def record_event(self, event_type, **fields) -> RuntimeEvent:
        ...
```

`AgentAdapter` 的构造函数只需要一个 `workflow` 对象（`core/workflow.py` 的
`WorkflowRuntime`），其余能力（事件总线、协调器、仓储）都能从它拿到。

## 2.1 身份无法确认时（需求 5.2）

外部框架可能无法证明两个事件属于同一个 Agent。此时不得直接合并：

```python
await self.bind_agent(external_identity=None, identity_confirmed=False)
```

这样的 Agent 会被记录为 `identity_status = UNDETERMINED`（存在 `agents` 表中），
执行图中会出现一个 `UNDETERMINED` 状态的节点，而不是把两个事件合并成一个 Agent。
这就是需求 5.2 所说的“保留身份未确定状态”。


## 3. 对接步骤

### 步骤 1：创建工作流

```python
from agentfuse.app import AgentFuseApp

app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
workflow = app.create_workflow("my-framework", mode=RuntimeMode.ONLINE)
workflow.start()
```

### 步骤 2：实现适配器

```python
class MyFrameworkAdapter(AgentAdapter):
    async def bind_agent(self, external_identity=None, agent_description=None,
                         registration_source=None, metadata=None):
        return self.workflow.bind_agent(
            external_identity=external_identity,
            agent_description=agent_description,
            registration_source=registration_source,
            metadata=metadata,
        )

    async def begin_invocation(self, agent_id, parent_invocation_id=None, metadata=None):
        return self.workflow.begin_invocation(
            agent_id, parent_invocation_id=parent_invocation_id, metadata=metadata
        )

    async def submit_tool_call(self, invocation_id, tool_name, arguments, snapshot_id=None):
        invocation = self.workflow.invocations[invocation_id]
        return await self.coordinator.submit_tool_call(
            workflow_id=self.workflow.workflow_id,
            invocation_id=invocation_id,
            agent_id=invocation.agent_id,
            tool_name=tool_name,
            arguments=arguments,
            snapshot_id=snapshot_id or self.workflow.default_snapshot_id,
        )

    async def deliver_result(self, invocation_id, logical_call_id, result):
        # The runtime publishes the authoritative TOOL_RESULT_DELIVERED event
        # when the result is bound to the logical call.  An adapter must not
        # publish it again - it reports the framework side with its own
        # event type (FRAMEWORK_RESULT_RECEIVED).
        await self.record_event(
            "FRAMEWORK_RESULT_RECEIVED",
            invocation_id=invocation_id,
            logical_call_id=logical_call_id,
        )

    async def end_invocation(self, invocation_id, status="COMPLETED"):
        self.workflow.end_invocation(invocation_id, status=status)

    async def record_event(self, event_type, **fields):
        fields.setdefault("workflow_id", self.workflow.workflow_id)
        return self.workflow.event_bus.publish(event_type, **fields)
```

### 步骤 3：把框架的调用边界映射过去

```python
async def on_agent_started(framework_call, adapter):
    agent = await adapter.bind_agent(
        external_identity=framework_call.agent_label,   # 可以是 None
        registration_source="my-framework",
    )
    invocation = await adapter.begin_invocation(
        agent.agent_id,
        parent_invocation_id=framework_call.parent_invocation_id,
    )
    return invocation


async def on_tool_request(adapter, invocation, tool_name, arguments, snapshot_id):
    result = await adapter.submit_tool_call(
        invocation.invocation_id, tool_name, arguments, snapshot_id=snapshot_id
    )
    return result.result          # 共享执行返回的就是这份结果


async def on_agent_finished(adapter, invocation, status="COMPLETED"):
    await adapter.end_invocation(invocation.invocation_id, status=status)
```

## 4. 外部框架的硬性要求

适配器**不要求**外部框架提供：

* 固定的 `agent_id`（外部身份可以为 `None`，内部标识由运行时维护）；
* 固定格式的请求载荷；
* 固定的"下一个 Agent 唤醒"格式；
* 任何特定框架的 Scheduler 或内部状态对象；
* 在 Agent Query 中插入标识。

适配器**要求**外部框架提供：

* 在真实的调用边界上调用 `bind_agent` / `begin_invocation` /
  `submit_tool_call` / `end_invocation`；
* 如果观察到"某个 Invocation 通过明确的调用边界触发了另一个 Invocation"，
  通过 `parent_invocation_id` 传递它（否则图中不会出现
  `EXPLICIT_INVOCATION_DEPENDENCY` 边——这是有意的：没观察到就不画）。

## 5. 工作区快照

工具执行需要一个快照 id。典型做法：

* 每个 Agent 绑定一个工作区快照（`workflow.default_snapshot_id` 或
  `agent` 级别的快照）；
* 需要修改工作区的 Agent 先取得一个**可写副本**，修改后生成新快照；
* 通过 `snapshot_id` 参数把它传给 `submit_tool_call`。

```python
base = app.snapshots.create_workspace(source_dir=repo_path)
branch = app.snapshots.fork_snapshot(base.snapshot_id)   # 独立分支
```

## 6. Scripted Agent 参考实现

`agentfuse/adapters/scripted_adapter.py` 中的 `ScriptedAdapter` +
`ScriptedAgent` 就是按照上面这套接口实现的，可以直接作为模板：

```python
from agentfuse.adapters.scripted_adapter import ScriptedAdapter, ScriptedAgent, ToolStep

adapter = ScriptedAdapter(workflow)
agent = ScriptedAgent("my-agent", [ToolStep("ast_analyzer", {"files": [...]})])
await agent.run(workflow, adapter)
```

**Scripted Agent 不是 LLM Agent**，它只是按照真实结果动态选择下一步的本地程序。
它的价值在于：演示与测试走的是**与真实框架完全相同的接入路径**，
Runtime 中不存在任何"Demo 专用旁路"。

## 7. 事件协议

适配器也可以直接上报自定义事实：

```python
await adapter.record_event(
    "AGENT_INVOKED",
    agent_id=agent.agent_id,
    invocation_id=invocation.invocation_id,
    metadata={"parent_invocation_id": parent_id},
)
```

事件类型见 `agentfuse/core/state.py` 的 `EventType`。
`metadata` 用于承载事件特有的细节（依赖路径、指纹、分离原因等）。
