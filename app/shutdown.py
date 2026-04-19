import atexit
import gc
import logging
import threading

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


def request_shutdown():
    """Signal all worker threads to stop."""
    _shutdown_event.set()
    logger.info("Shutdown requested")


def reset_shutdown():
    """Clear shutdown flag (for tests)."""
    _shutdown_event.clear()


def _atexit_gpu_cleanup():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("GPU cache cleared at exit")
    except Exception:
        pass


atexit.register(_atexit_gpu_cleanup)
