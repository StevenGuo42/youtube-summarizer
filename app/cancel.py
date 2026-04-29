import asyncio
import logging

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_procs: dict[str, set] = {}  # job_id -> set of asyncio.subprocess.Process

_cancelled: set[str] = set()


def is_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled."""
    return job_id in _cancelled


def mark_cancelled(job_id: str) -> None:
    """Mark a job as cancelled."""
    _cancelled.add(job_id)


def clear_cancelled(job_id: str) -> None:
    """Remove the cancelled flag for a job."""
    _cancelled.discard(job_id)


async def register_subprocess(job_id: str, proc) -> None:
    """Register a subprocess for a job so cancel() can kill it."""
    async with _lock:
        _procs.setdefault(job_id, set()).add(proc)


async def unregister_subprocess(job_id: str, proc) -> None:
    """Remove a subprocess from the registry (called in finally after proc ends)."""
    async with _lock:
        if job_id in _procs:
            _procs[job_id].discard(proc)
            if not _procs[job_id]:
                del _procs[job_id]


async def kill_subprocesses(job_id: str) -> None:
    """SIGTERM all registered subprocesses for job_id, then SIGKILL after 2s grace."""
    async with _lock:
        procs = list(_procs.get(job_id, set()))
    if not procs:
        return
    logger.info("[%s] Killing %d registered subprocess(es)", job_id, len(procs))
    for proc in procs:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    # Give processes 2 seconds to exit cleanly, then force-kill
    await asyncio.sleep(2)
    for proc in procs:
        try:
            if proc.returncode is None:
                proc.kill()
                logger.warning("[%s] SIGKILL sent to subprocess (did not exit after SIGTERM)", job_id)
        except ProcessLookupError:
            pass
