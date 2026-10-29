"""Worker protocol.

The parent process and the tool worker communicate over a one way
``multiprocessing.Pipe``.  Messages are plain dictionaries (pickled by the
pipe).  The most important property of this protocol is that a *dependency
observation* is sent **immediately** when the tool touches a file - never
batched at the end of the run.  Without that property the share coordinator
could not validate participants while the leader is still running.
"""

from __future__ import annotations

from typing import Any

MSG_DEPENDENCY = "dep"
MSG_RESULT = "result"
MSG_ERROR = "error"
MSG_LOG = "log"
MSG_EOF = "eof"

ERROR_TOOL = "tool_error"
ERROR_INTERNAL = "internal_error"
ERROR_CRASH = "worker_crash"
ERROR_TIMEOUT = "timeout"
ERROR_CANCELLED = "cancelled"


def dependency_message(
    dependency_type: str, relative_path: str, fingerprint: str, sequence: int
) -> dict[str, Any]:
    return {
        "t": MSG_DEPENDENCY,
        "dependency_type": dependency_type,
        "relative_path": relative_path,
        "fingerprint": fingerprint,
        "sequence": sequence,
    }


def result_message(
    payload: Any, result_hash: str, cpu_time: float, elapsed: float, rss_end: int
) -> dict[str, Any]:
    return {
        "t": MSG_RESULT,
        "payload": payload,
        "result_hash": result_hash,
        "cpu_time": cpu_time,
        "elapsed": elapsed,
        "rss_end": rss_end,
    }


def error_message(kind: str, message: str, traceback_text: str, cpu_time: float) -> dict[str, Any]:
    return {
        "t": MSG_ERROR,
        "kind": kind,
        "message": message,
        "traceback": traceback_text,
        "cpu_time": cpu_time,
    }
