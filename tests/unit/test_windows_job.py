import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from agentfuse.runtime.pool import build_environment
from agentfuse.runtime.windows_job import KillOnCloseJob

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object semantics')


def test_job_reclaims_process_after_owner_crash():
    code = """
import subprocess,sys,json,os,psutil
from agentfuse.runtime.windows_job import KillOnCloseJob
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
job=KillOnCloseJob(child)
print(json.dumps({'pid':child.pid,'created':psutil.Process(child.pid).create_time()}),flush=True)
os._exit(0)
"""
    result = subprocess.run([sys._base_executable, '-B', '-c', code],
        env=build_environment(), text=True, capture_output=True, timeout=15, check=True)
    identity = json.loads(result.stdout)
    pid = identity['pid']
    def same_process():
        try:
            return psutil.Process(pid).create_time() == identity['created']
        except psutil.NoSuchProcess:
            return False
    deadline = time.monotonic() + 5
    while same_process() and time.monotonic() < deadline:
        time.sleep(.05)
    alive = same_process()
    if alive:
        psutil.Process(pid).kill()
    assert not alive, 'an owner crash must not leave native model processes behind'


def test_job_close_is_idempotent_and_reclaims_process():
    child = subprocess.Popen([sys._base_executable, '-c', 'import time; time.sleep(30)'])
    try:
        job = KillOnCloseJob(child)
        job.close()
        job.close()
        assert child.wait(timeout=5) is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
