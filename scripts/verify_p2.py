"""Run all ten P2 acceptance areas with real local backends; skips fail the gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    'tests/integration/test_persistent_cache.py', 'tests/unit/test_gpu_scheduling.py',
    'tests/integration/test_pretrained_llm.py', 'tests/integration/test_extensions.py',
    'tests/integration/test_preemption.py', 'tests/unit/test_prediction_namespaces.py',
    'tests/integration/test_langgraph_adapter.py', 'tests/integration/test_native_trace.py',
    'tests/unit/test_framework_boundary_audit.py',
    'tests/unit/test_windows_job.py',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, default=ROOT / '.agentfuse/p2-review')
    parser.add_argument('--device', help='explicit NVIDIA Vulkan device; otherwise detect by name')
    args = parser.parse_args()
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    pins = json.loads((ROOT / 'config/p2-artifacts.json').read_text())
    executable = ROOT / '.tools/llama/llama-server.exe'
    model = ROOT / '.tools/models' / pins['model']['filename']
    with model.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != pins['model']['sha256']:
            raise RuntimeError('pretrained model hash mismatch')
    devices = subprocess.check_output([str(executable), '--list-devices'], text=True, stderr=subprocess.STDOUT)
    nvidia = re.findall(r'^\s*(\w+):.*NVIDIA[^\n]*', devices, re.M)
    device = args.device or (nvidia[0] if nvidia else None)
    if device not in nvidia:
        raise RuntimeError('an actual NVIDIA Vulkan device is required for the GPU acceptance gate')
    trace_image = subprocess.check_output(['docker', 'image', 'inspect', 'agentfuse-trace:local',
                                          '--format', '{{.Id}}'], text=True).strip()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        AGENTFUSE_LLAMA_SERVER=str(executable), AGENTFUSE_LLM_MODEL=str(model),
        AGENTFUSE_LLM_DEVICE=device, AGENTFUSE_TRACE_TEST_IMAGE=trace_image,
        AGENTFUSE_SANDBOX_TEST_IMAGE=pins['sandbox_base'])
    junit = reports / 'p2-acceptance.xml'
    with (reports / 'p2-acceptance.log').open('w', encoding='utf-8') as output:
        result = subprocess.run([sys.executable, '-B', '-m', 'pytest', *TESTS, '-q', '-s',
            '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin', '--junitxml=' + str(junit)], cwd=ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT)
    root = ET.parse(junit).getroot()
    cases = root.findall('.//testcase')
    valid = bool(cases) and not result.returncode and not any(
        root.findall('.//' + tag) for tag in ('failure', 'error', 'skipped'))
    summary = dict(full_p2_completed=valid,
        scope={'framework': 'LangGraph + generic external protocol',
               'third_party_tools': 'Linux container native commands',
               'sharing': 'incomplete or nondeterministic dependencies never auto-shared'},
        total=len(cases), passed=sum(c.find('failure') is None and c.find('error') is None
                                     and c.find('skipped') is None for c in cases),
        device=device, devices=devices, model_sha256=pins['model']['sha256'],
        immutable_trace_image=trace_image,
        source_sha256=runpy.run_path(str(ROOT / 'scripts/report_review.py'))['source_digest'](),
        acceptance_areas=['persistent cache', 'GPU scheduling', 'native model KV management',
            'pretrained agent inference', 'distributed workers', 'preemptive scheduling',
            'path prediction', 'LangGraph automatic adaptation',
            'native descendant syscall audit', 'hardened Linux command sandbox'])
    (reports / 'p2-acceptance.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
