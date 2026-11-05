// Run from the project root with: node examples/external_framework/client.mjs python <home> <workspace>
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const [python, home, workspace] = process.argv.slice(2);
if (!python || !home || !workspace) {
  throw new Error('expected Python executable, AgentFuse home and workspace directory');
}

const child = spawn(python, ['-m', 'agentfuse.adapters.external_adapter',
  '--home', home, '--workspace', workspace], { stdio: ['pipe', 'pipe', 'inherit'] });
let startupError;
child.on('error', (error) => { startupError = error; });
child.stdin.on('error', (error) => { startupError ??= error; });
const closed = new Promise((resolve) => child.once('close', resolve));
const lines = createInterface({ input: child.stdout });
const waiting = new Map();
let nextId = 0;

const responses = (async () => {
  try {
    for await (const line of lines) {
      const reply = JSON.parse(line);
      const pending = waiting.get(reply.request_id);
      if (!pending) throw new Error(reply.message ?? 'unexpected response ID');
      waiting.delete(reply.request_id);
      if (reply.type === 'error') {
        pending.reject(new Error(`${reply.code}: ${reply.message}`));
      } else {
        pending.resolve(reply);
      }
    }
  } catch (error) {
    startupError ??= error;
  } finally {
    startupError ??= new Error('AgentFuse closed the protocol stream');
    for (const pending of waiting.values()) pending.reject(startupError);
    waiting.clear();
  }
})();

function request(message) {
  if (startupError) return Promise.reject(startupError);
  const requestId = ++nextId;
  return new Promise((resolve, reject) => {
    waiting.set(requestId, { resolve, reject });
    child.stdin.write(`${JSON.stringify({ ...message, protocol_version: 1, request_id: requestId })}\n`);
  });
}

try {
  const catalogue = await request({ type: 'discover' });
  const tool = catalogue.tools.find((item) => item.name === 'source_index');
  const invocations = await Promise.all(['planner', 'reviewer'].map(async (label) => {
    const agent = await request({ type: 'bind', external_identity: label });
    return request({ type: 'invoke', agent_id: agent.agent_id });
  }));
  const [first, second] = await Promise.all(invocations.map(({ invocation_id }) =>
    request({ type: 'call', invocation_id, tool: tool.name,
      tool_version: tool.version, arguments: { root: '.' } })));
  await Promise.all(invocations.map(({ invocation_id }) => request({ type: 'end', invocation_id })));
  console.log(JSON.stringify({ tool: tool.name, output: first.output,
    call: first.result, second_call: second.result }));
} finally {
  child.stdin.end();
  const code = await closed;
  await responses;
  if (code !== 0) process.exitCode = 1;
}
