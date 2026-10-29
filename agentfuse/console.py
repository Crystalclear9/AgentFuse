"""Console helpers.

The AgentFuse CLI and the example scripts print Chinese messages.  On Windows
the console code page is often 936 (GBK) which mangles UTF-8 output when it is
piped or redirected, therefore the output stream is switched to UTF-8.
"""

from __future__ import annotations

import sys


def configure_console() -> None:
    """Switch stdout/stderr to UTF-8 (best effort)."""

    try:  # pragma: no cover - Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
