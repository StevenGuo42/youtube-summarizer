from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path("logs/tests")
_active_handlers: dict[str, logging.FileHandler] = {}


def pytest_configure(config):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def pytest_collection_modifyitems(config, items):
    """Create one log file per test module at collection time."""
    seen = set()
    for item in items:
        module_name = Path(item.module.__file__).stem
        if module_name in seen:
            continue
        seen.add(module_name)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = LOGS_DIR / f"{module_name}_{timestamp}.log"

        handler = logging.FileHandler(log_file, mode="w")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

        logging.getLogger().addHandler(handler)
        _active_handlers[module_name] = handler


def pytest_unconfigure(config):
    """Close all handlers and prune old logs at session end."""
    for module_name, handler in _active_handlers.items():
        handler.close()
        logging.getLogger().removeHandler(handler)
        _prune_logs(module_name, keep=3)
    _active_handlers.clear()


def _prune_logs(module_name: str, keep: int):
    logs = sorted(
        LOGS_DIR.glob(f"{module_name}_*.log"),
        key=lambda p: p.stat().st_mtime,
    )
    for old in logs[:-keep]:
        old.unlink()
