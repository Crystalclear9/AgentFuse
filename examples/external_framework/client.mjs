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
const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
let nextId = 0;

async function request(message) {
  const requestId = ++nextId;
  child.stdin.write(`${JSON.stringify({ ...message, protocol_version: 1, request_id: requestId })}\n`);
  const { value, done } = await lines.next();
  if (done) throw startupError ?? new Error('AgentFuse closed the protocol stream');
  const reply = JSON.parse(value);
  if (reply.request_id !== requestId) throw new Error('unexpected response ID');
  if (reply.type === 'error') throw new Error(`${reply.code}: ${reply.message}`);
  return reply;
}

try {
  const catalogue = await request({ type: 'discover' });
  const tool = catalogue.tools.find((item) => item.name === 'source_index');
  const { invocation_id } = await request({ type: 'invoke', agent_id: 'node-source-inspector' });
  const result = await request({ type: 'call', invocation_id, tool: tool.name,
    tool_version: tool.version, arguments: { root: '.' } });
  await request({ type: 'end', invocation_id });
  console.log(JSON.stringify({ tool: tool.name, output: result.output, call: result.result }));
} finally {
  child.stdin.end();
  const code = await closed;
  if (code !== 0) process.exitCode = 1;
}
