"""Run the test suite the way the CI runner does.

The GitHub Actions runners install the package into the *base* interpreter of
``actions/setup-python``.  That interpreter is not a virtual environment, so
there is **no launcher stub** - the process that is started is the worker
itself.  The tests must behave the same in both worlds.

This script reproduces that environment locally:

    python scripts/run_ci_like.py                 # the whole suite
    python scripts/run_ci_like.py --matrix        # pool 0/1/2 x workers 1/2
    python scripts/run_ci_like.py --python "C:\\Python311\\python.exe"

The interpreter is taken from ``--python`` (default: the base interpreter of the
current environment).  The test dependencies are loaded from
``.agentfuse/pylibs`` so that nothing is installed outside the project.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYLIBS = ROOT / ".agentfuse" / "pylibs"


def base_interpreter() -> str:
    """The base interpreter of the current environment (no virtual env)."""

    base = getattr(sys, "base_prefix", sys.prefix)
    candidate = Path(base) / ("python.exe" if os.name == "nt" else "bin/python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def environment(pool: int | None = None, workers: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    parts = [str(PYLIBS), str(ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # the user site packages of the machine can contain conflicting pytest
    # plugins - only the ones of the project may be loaded
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if pool is not None:
        env["AGENTFUSE_TEST_POOL_SIZE"] = str(pool)
    if workers is not None:
        env["AGENTFUSE_TEST_WORKERS"] = str(workers)
    return env


def run(python: str, pool: int | None, workers: int | None, extra: list[str]) -> int:
    command = [
        python,
        "-m",
        "pytest",
        "tests",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_asyncio.plugin",
        *extra,
    ]
    label = f"pool={pool if pool is not None else 'default'} workers={workers if workers is not None else 'default'}"
    print(f"  --- {label} ---", flush=True)
    return subprocess.run(command, cwd=str(ROOT), env=environment(pool, workers)).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=None, help="the interpreter to use")
    parser.add_argument("--matrix", action="store_true", help="run the pool matrix")
    args = parser.parse_args()

    python = args.python or base_interpreter()
    print(f"interpreter: {python}")
    print(f"may_have_launcher: ", end="")
    probe = subprocess.run(
        [python, "-c", "from agentfuse.runtime.pool import may_have_launcher; print(may_have_launcher())"],
        cwd=str(ROOT),
        env=environment(),
        capture_output=True,
        text=True,
    )
    print(probe.stdout.strip() or probe.stderr.strip()[-200:])

    failures = 0
    if args.matrix:
        for pool in (0, 1, 2):
            for workers in (1, 2):
                failures += 1 if run(python, pool, workers, []) else 0
    else:
        failures += 1 if run(python, None, None, []) else 0
    print()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
