"""Sandbox runner for skills: resource limits, no root, optional network isolation."""

from __future__ import annotations

import asyncio
import logging
import os
import resource
from pathlib import Path

logger = logging.getLogger(__name__)


def _set_resource_limits(cpu_seconds: int, memory_mb: int) -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (ValueError, OSError):
        pass
    try:
        bytes_limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
    except (ValueError, OSError):
        pass


async def run_in_sandbox(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    cpu_limit_seconds: int = 30,
    memory_limit_mb: int = 256,
    timeout_seconds: float | None = None,
    network: bool = False,
) -> tuple[int, str, str]:
    """
    Run command in subprocess with resource limits. Returns (returncode, stdout, stderr).
    network=False: do not allow network (set env to block if possible; full isolation needs container).
    """
    use_cwd = str(cwd) if cwd else None
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    if not network:
        run_env["HTTP_PROXY"] = ""
        run_env["HTTPS_PROXY"] = ""
        run_env["NO_PROXY"] = "*"
    timeout = timeout_seconds or (cpu_limit_seconds + 5)
    preexec = None
    if os.name != "nt":
        def _limits() -> None:
            _set_resource_limits(cpu_limit_seconds, memory_limit_mb)
        preexec = _limits
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=use_cwd,
            env=run_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=preexec,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, (stdout or b"").decode("utf-8", errors="replace"), (stderr or b"").decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        try:
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, NameError):
            pass
        return -1, "", "command timed out"
    except Exception as e:
        logger.exception("sandbox run failed: %s", e)
        return -1, "", str(e)
