"""Console helpers.

The AgentFuse CLI and the example scripts print Chinese messages.  On Windows
the console code page is often 936 (GBK) which mangles UTF-8 output when it is
piped or redirected, therefore the output stream is switched to UTF-8.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


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


def configure_logging(logs_dir: Path) -> None:
    """Write the log of the runtime into the home directory.

    The dashboard shows the state of the runtime; the log file explains what
    happened.  Without it the directory that ``agentfuse init`` prints would
    stay empty.

    The handler is attached **once** - the runtime is created and closed
    again in the tests, a second handler would write every line twice.
    """

    directory = Path(logs_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - a read only home
        return
    target = directory / "agentfuse.log"
    logger = logging.getLogger("agentfuse")
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(
            getattr(handler, "baseFilename", "")
        ) == target:
            return
    try:
        handler = logging.FileHandler(target, encoding="utf-8")
    except OSError:  # pragma: no cover - a read only home
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
