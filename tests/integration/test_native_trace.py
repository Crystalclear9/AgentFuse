import os
import subprocess
import pytest

from agentfuse.runtime.native_trace import run_traced


@pytest.fixture
def image():
    value = os.environ.get('AGENTFUSE_TRACE_TEST_IMAGE')
    if not value:
        pytest.skip('set an immutable local image containing Python and strace')
    return value


def test_native_trace_observes_child_reads_and_preserves_inputs(tmp_path, image):
    (tmp_path / 'input.txt').write_text('native-input', encoding='utf-8')
    result = run_traced(tmp_path, ['python', '-c',
        "import subprocess; subprocess.run(['cat','input.txt'],check=True)"], image=image)
    assert result['exit_code'] == 0
    assert result['stdout'] == 'native-input'
    assert 'input.txt' in result['syscall_trace']
    assert 'execve(' in result['syscall_trace']
    assert 'read(' in result['syscall_trace']
    assert result['trace_complete']
    assert result['input_manifest']['input.txt']
    assert result['image_digest'] == image
    assert result['shareable'] is False
    assert (tmp_path / 'input.txt').read_text() == 'native-input'


def test_native_sandbox_rejects_writes_and_network(tmp_path, image):
    result = run_traced(tmp_path, ['python', '-c', '''
import pathlib,socket
for name in ['/workspace/forbidden','/etc/forbidden']:
    try: pathlib.Path(name).write_text('bad')
    except OSError: pass
    else: raise AssertionError('writable filesystem')
try: socket.create_connection(('1.1.1.1',80),timeout=1)
except OSError: pass
else: raise AssertionError('network escaped')
print('confined')
'''], image=image)
    assert result['exit_code'] == 0, result['stderr']
    assert result['stdout'].strip() == 'confined'
    assert not (tmp_path / 'forbidden').exists()


def test_native_sandbox_enforces_deadline(tmp_path, image):
    with pytest.raises(subprocess.TimeoutExpired):
        run_traced(tmp_path, ['python', '-c', 'import time; time.sleep(30)'],
                   image=image, timeout=1)


def test_tracee_cannot_tamper_with_monitor_or_gain_privilege(tmp_path, image):
    result = run_traced(tmp_path, ['python', '-c', '''
import os,pathlib
assert os.geteuid()==65534
status=pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='64'
assert int(pathlib.Path('/sys/fs/cgroup/memory.max').read_text())==268435456
for directory in pathlib.Path('/tmp').glob('audit-*'):
    try: (directory/'syscalls').write_text('forged')
    except PermissionError: pass
    else: raise AssertionError('audit tampering possible')
try: os.setuid(0)
except PermissionError: pass
else: raise AssertionError('privilege escalation possible')
print('unprivileged')
'''], image=image)
    assert result['exit_code'] == 0, result['stderr']
    assert result['stdout'].strip() == 'unprivileged'
    assert 'forged' in result['syscall_trace']  # rejected write is still observed
