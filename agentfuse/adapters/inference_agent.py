"""A local numerical-policy agent entering through the normal adapter boundary.

This is not a language model agent. Its causal-attention output chooses a
registered tool from caller-declared actions; KV reuse changes cost, not policy.
"""
import asyncio
import time


class InferenceAgent:
    def __init__(self, name, model, inference, actions):
        if not actions:
            raise ValueError("at least one action is required")
        self.name, self.model, self.inference = name, model, inference
        self.actions = tuple(actions)

    async def run(self, adapter, tokens, *, snapshot_id, use_cache=True):
        agent = await adapter.bind_agent(external_identity=self.name, registration_source="local-inference-policy")
        invocation = await adapter.begin_invocation(agent.agent_id)
        try:
            started = time.perf_counter()
            result = await asyncio.to_thread(self.inference.generate, self.model, tokens, 1, use_cache=use_cache)
            action = self.actions[result["tokens"][0] % len(self.actions)]
            await adapter.record_event("INFERENCE_COMPLETED", invocation_id=invocation.invocation_id,
                                       agent_id=agent.agent_id,
                                       metadata=dict(model_id=result["model_id"],
                                                     reused_tokens=result["reused_tokens"],
                                                     evaluated_tokens=result["evaluated_tokens"],
                                                     elapsed=time.perf_counter() - started,
                                                     selected_tool=action["tool"]))
            output = await adapter.submit_tool_call(invocation.invocation_id, action["tool"],
                                                    action["arguments"], snapshot_id=snapshot_id)
            await adapter.end_invocation(invocation.invocation_id)
            return output
        except BaseException:
            await adapter.end_invocation(invocation.invocation_id, status="FAILED")
            raise
