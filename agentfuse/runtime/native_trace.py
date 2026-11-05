"""Native Linux command audit inside the hardened, immutable-image sandbox.

The audit includes descendant syscalls and the complete declared input tree.
Observed dependency coverage does not prove deterministic output: traced
commands remain ineligible for semantic result sharing.
"""
import base64
import json
import zlib

from .sandbox import run_isolated


RUNNER = r'''
import base64, json, os, pathlib, resource, subprocess, sys, tempfile, zlib
resource.setrlimit(resource.RLIMIT_FSIZE, (4194304, 4194304))
os.umask(0o077)
private=pathlib.Path(tempfile.mkdtemp(prefix='audit-', dir='/tmp'))
trace=private/'syscalls'
out=private/'stdout'
err=private/'stderr'
argv=json.loads(sys.argv[1])
with out.open('wb') as stdout, err.open('wb') as stderr:
    process=subprocess.run(['/usr/bin/strace','-u','nobody','-f','-qq','-yy','-s','4096',
        '-e','trace=all','-o',str(trace),'--',*argv],stdout=stdout,stderr=stderr,
        timeout=float(sys.argv[2]))
raw=trace.read_text(errors='replace') if trace.exists() else ''
stderr=err.read_text(errors='replace')
stdout=out.read_text(errors='replace')
complete=bool(raw) and trace.stat().st_size < 4194304 and 'strace:' not in stderr
print(json.dumps(dict(exit_code=process.returncode,stdout=stdout,stderr=stderr,
    syscall_trace_zlib=base64.b64encode(zlib.compress(raw.encode())).decode(),trace_complete=complete,
    trace_scope='Linux container process tree; all syscalls; strings up to 4096 bytes')))
'''


def run_traced(snapshot, command, *, image, timeout=60, memory_mb=256, cpus=1.0):
    if not isinstance(command, (list, tuple)) or not command or any(
            not isinstance(arg, str) or '\x00' in arg for arg in command):
        raise ValueError('a nonempty argv list is required')
    result = run_isolated(snapshot, ['python', '-c', RUNNER, json.dumps(list(command)), str(timeout)],
                          image=image, timeout=timeout, memory_mb=memory_mb, cpus=cpus,
                          _native_monitor=True)
    if result['exit_code']:
        raise RuntimeError('native audit runner failed: ' + result['stderr'][:500])
    audit = json.loads(result['stdout'])
    if not audit['trace_complete']:
        raise RuntimeError('native syscall audit was incomplete; no result accepted')
    inflater = zlib.decompressobj()
    raw = inflater.decompress(base64.b64decode(audit.pop('syscall_trace_zlib')), 4194305)
    if len(raw) > 4194304 or not inflater.eof:
        raise RuntimeError('native audit exceeded its decoded size limit')
    audit['syscall_trace'] = raw.decode('utf-8')
    # Keep syscall observations, immutable filesystem provenance and purity
    # separate: time, entropy and scheduling are not safe sharing inputs.
    return {**result, **audit, 'image_digest': image, 'command': list(command),
            'dependency_complete': False, 'shareable': False,
            'dependency_scope': 'declared input tree + immutable image + native syscall observations'}
