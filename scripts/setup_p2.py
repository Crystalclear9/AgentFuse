"""Install optional P2 tools inside this checkout; downloads are hash pinned."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, destination, spec):
    if destination.exists() and digest(destination) == spec['sha256']:
        return
    curl = ROOT / '.tools/git/ucrt64/bin/curl.exe'
    executable = str(curl) if curl.exists() else 'curl'
    parts = ROOT / '.tools/downloads' / ('parts-' + destination.name)
    parts.mkdir(parents=True, exist_ok=True)
    def fetch(index):
        start = spec['bytes'] * index // 16
        end = spec['bytes'] * (index + 1) // 16 - 1
        path = parts / str(index)
        if not path.exists() or path.stat().st_size != end - start + 1:
            subprocess.run([executable, '-fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '240', '--range', f'{start}-{end}', '-o', str(path), url], check=True)
        if path.stat().st_size != end - start + 1:
            raise RuntimeError('download server did not honor byte ranges')
        return path
    with ThreadPoolExecutor(max_workers=16) as pool:
        files = list(pool.map(fetch, range(16)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as target:
        for path in files:
            with path.open('rb') as stream:
                shutil.copyfileobj(stream, target)
    if digest(destination) != spec['sha256']:
        raise RuntimeError('artifact hash verification failed')
    # Every path has been created and checked inside the project downloads dir.
    assert parts.resolve().is_relative_to((ROOT / '.tools/downloads').resolve())
    shutil.rmtree(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-mirror', default='https://huggingface.co')
    parser.add_argument('--install-frameworks', action='store_true')
    parser.add_argument('--build-sandbox', action='store_true')
    args = parser.parse_args()
    if not args.model_mirror.startswith('https://'):
        parser.error('model mirror must use HTTPS')
    pins = json.loads((ROOT / 'config/p2-artifacts.json').read_text())
    archive = ROOT / '.tools/downloads' / pins['llama']['filename']
    download(pins['llama']['url'], archive, pins['llama'])
    model = pins['model']
    url = f"{args.model_mirror.rstrip('/')}/{model['repository']}/resolve/{model['revision']}/{model['filename']}"
    download(url, ROOT / '.tools/models' / model['filename'], model)
    target = ROOT / '.tools/llama'
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        if any(not (target / name).resolve().is_relative_to(target.resolve()) for name in source.namelist()):
            raise RuntimeError('unsafe executable archive')
        source.extractall(target)
    if args.install_frameworks:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-compile', '--require-hashes',
                        '--index-url', 'https://pypi.org/simple', '-r', str(ROOT / 'requirements-p2.lock')], check=True)
    if args.build_sandbox:
        subprocess.run(['docker', 'tag', pins['sandbox_base'], 'agentfuse-base:local'], check=True)
        context = ROOT / '.tools/sandbox'
        context.mkdir(exist_ok=True)
        (context / 'Dockerfile').write_text('FROM agentfuse-base:local\nRUN apk add --no-cache strace='
                                          + pins['strace_version'] + '\n')
        subprocess.run(['docker', 'build', '--pull=false', '-t', 'agentfuse-trace:local', str(context)],
                       env=dict(os.environ, DOCKER_BUILDKIT='0'), check=True)
        image = subprocess.check_output(['docker', 'image', 'inspect', 'agentfuse-trace:local',
                                         '--format', '{{.Id}}'], text=True).strip()
        (ROOT / '.tools/images/native-trace.json').write_text(json.dumps({'image': image, **pins}, indent=2))
        subprocess.run(['docker', 'save', '-o', str(ROOT / '.tools/images/agentfuse-trace.tar'), image], check=True)
    print('Verified project-local P2 artifacts. Use --list-devices to select the GPU explicitly.')


if __name__ == '__main__':
    main()
