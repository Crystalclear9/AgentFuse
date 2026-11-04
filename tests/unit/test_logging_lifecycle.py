"""Log files must not accumulate across repeated Runtime lifetimes."""
import logging
from agentfuse.console import configure_logging, release_logging


def test_shared_log_stays_open_until_the_last_owner_releases_it(tmp_path):
    first = configure_logging(tmp_path)
    second = configure_logging(tmp_path)
    assert first is second
    release_logging(first)
    assert second.stream is not None
    logging.getLogger("agentfuse").warning("still owned")
    release_logging(second)
    assert second.stream is None
    assert second not in logging.getLogger("agentfuse").handlers
    assert "still owned" in (tmp_path / "agentfuse.log").read_text()


async def test_runtime_shutdown_releases_its_file_handler(settings):
    from agentfuse.app import AgentFuseApp
    app = AgentFuseApp(settings)
    handler = app._log_handler
    await app.close()
    await app.close()
    assert handler.stream is None
    assert handler not in logging.getLogger("agentfuse").handlers
    # This fails on Windows if a FileHandler still owns the file.
    (settings.logs_dir / "agentfuse.log").unlink()
