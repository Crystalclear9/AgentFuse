"""Pretrained local language-model decisions through normal runtime boundaries."""
import asyncio
import json


class LanguageModelAgent:
    def __init__(self, name, backend, actions):
        self.name, self.backend = name, backend
        self.actions = {action['tool']: action for action in actions}
        if not self.actions or len(self.actions) != len(actions):
            raise ValueError('nonempty uniquely named tool actions required')

    async def run(self, adapter, task, *, snapshot_id, use_cache=True):
        agent = await adapter.bind_agent(external_identity=self.name,
            registration_source='local-pretrained-language-model')
        invocation = await adapter.begin_invocation(agent.agent_id)
        try:
            choices = [{'tool': name, 'description': action.get('description', name)}
                       for name, action in self.actions.items()]
            prompt = ('<|im_start|>system\nSelect one tool for the task. Return only JSON with a tool key.\n'
                      + json.dumps(choices) + '<|im_end|>\n<|im_start|>user\n' + task
                      + '<|im_end|>\n<|im_start|>assistant\n')
            schema = {'type': 'object', 'properties': {'tool': {'enum': list(self.actions)}},
                      'required': ['tool'], 'additionalProperties': False}
            decision = await asyncio.to_thread(self.backend.generate, prompt, 64,
                                               use_cache=use_cache, json_schema=schema)
            name = json.loads(decision['text'])['tool']
            action = self.actions[name]  # Model output never supplies arbitrary argv or code.
            await adapter.record_event('INFERENCE_COMPLETED', invocation_id=invocation.invocation_id,
                agent_id=agent.agent_id, metadata={'model_id': decision['model_id'],
                    'backend': decision['backend'], 'reused_tokens': decision['reused_tokens'],
                    'evaluated_tokens': decision['evaluated_tokens'], 'selected_tool': name})
            output = await adapter.submit_tool_call(invocation.invocation_id, name,
                action['arguments'], snapshot_id=snapshot_id)
            await adapter.end_invocation(invocation.invocation_id)
            return output
        except asyncio.CancelledError:
            await adapter.end_invocation(invocation.invocation_id, status='CANCELLED')
            raise
        except BaseException:
            await adapter.end_invocation(invocation.invocation_id, status='FAILED')
            raise
