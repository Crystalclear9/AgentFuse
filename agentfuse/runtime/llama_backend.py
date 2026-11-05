"""Owned, offline llama.cpp process with bounded native CPU/GPU KV storage."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import secrets
import subprocess
from threading import RLock
import time
import urllib.error
import urllib.request


class LlamaBackend:
    def __init__(self, executable, model, *, context_size=2048, gpu_layers=0,
                 log_path, startup_timeout=90, device=None):
        self.executable = Path(executable).resolve(strict=True)
        self.model = Path(model).resolve(strict=True)
        if not 256 <= context_size <= 32768 or not -1 <= gpu_layers <= 1000:
            raise ValueError('invalid context or GPU layer budget')
        self.context_size = context_size
        self.gpu_layers = gpu_layers
        self.device = device
        with self.model.open('rb') as stream:
            self.model_id = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.lock = RLock()
        self.process = None
        self._job = None
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = None
        self.startup_timeout = startup_timeout
        self._token = secrets.token_urlsafe(32)
        # Never route private prompts through a machine-wide HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path, payload=None, timeout=120):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={'Content-Type': 'application/json',
                                                  'Authorization': 'Bearer ' + self._token})
        with self.http.open(request, timeout=timeout) as response:
            return json.load(response)

    def start(self):
        with self.lock:
            if self.process is not None:
                if self.process.poll() is not None:
                    raise RuntimeError('local inference process exited')
                return self
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            self.url = f'http://127.0.0.1:{port}'
            self._log = self.log_path.open('wb')
            slot_path = self.log_path.parent / 'kv-slots'
            slot_path.mkdir(exist_ok=True)
            command = [str(self.executable), '-m', str(self.model), '--host', '127.0.0.1',
                       '--port', str(port), '-c', str(self.context_size), '-np', '1',
                       '-ngl', str(self.gpu_layers), '--slots', '--no-webui',
                       '--cache-ram', '0', '--no-context-shift', '--api-key', self._token,
                       '--slot-save-path', str(slot_path)]
            if self.device:
                command.extend(['--device', self.device])
            try:
                self.process = subprocess.Popen(command, stdout=self._log, stderr=self._log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if os.name == 'nt':
                    from .windows_job import KillOnCloseJob
                    self._job = KillOnCloseJob(self.process)
                deadline = time.monotonic() + self.startup_timeout
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError(f'llama.cpp startup failed; see {self.log_path}')
                    try:
                        if self._request('/health', timeout=1).get('status') == 'ok':
                            return self
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.1)
                raise TimeoutError('local model startup timed out')
            except BaseException:
                self.close()
                raise

    def generate(self, prompt, max_new_tokens=32, *, use_cache=True, json_schema=None):
        if not isinstance(prompt, str) or not prompt or len(prompt.encode()) > 1024 * 1024:
            raise ValueError('nonempty prompt up to 1 MiB required')
        if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 512:
            raise ValueError('generation must be 1..512 tokens')
        with self.lock:
            self.start()
            tokens = self._request('/tokenize', {'content': prompt})['tokens']
            if len(tokens) + max_new_tokens > self.context_size:
                raise ValueError('prompt and generation exceed the KV context budget')
            payload = dict(prompt=prompt, n_predict=max_new_tokens, temperature=0,
                           seed=42, cache_prompt=bool(use_cache), id_slot=0,
                           return_tokens=True, stream=False)
            if json_schema is not None:
                payload['json_schema'] = json_schema
            result = self._request('/completion', payload)
            timings = result['timings']
            return dict(text=result['content'], tokens=result['tokens'], model_id=self.model_id,
                        reused_tokens=timings.get('cache_n', 0),
                        evaluated_tokens=timings['prompt_n'], timings=timings,
                        context_budget=self.context_size, backend='llama.cpp',
                        gpu_layers_requested=self.gpu_layers, device=self.device)

    def clear(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self._request('/slots/0?action=erase', {})
            return {'n_erased': 0}

    def close(self):
        with self.lock:
            if self._job is not None:
                self._job.close()
                self._job = None
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=10)
                self.process = None
            if self._log is not None:
                self._log.close()
                self._log = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
