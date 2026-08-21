"""Runtime directories, instance lock, and shutdown coordination."""

import atexit
import logging
import os
import signal
from pathlib import Path
from types import FrameType
from typing import Callable


def ensure_runtime_directories() -> None:
    for path in (
        Path("data/raw"), Path("data/products"), Path("data/snapshots"), Path("data/site_models"),
        Path("runtime/chrome-profile"), Path("runtime/screenshots"), Path("runtime/artifacts"),
        Path("logs"), Path("prompts"), Path("tests"),
    ):
        path.mkdir(parents=True, exist_ok=True)


class RuntimeLock:
    def __init__(self, path: Path = Path("runtime/agent.lock")) -> None:
        self.path = path
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Another main instance may be running ({self.path})") from exc
        with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
            lock_file.write(str(os.getpid()))
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def __enter__(self) -> "RuntimeLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class ShutdownCoordinator:
    def __init__(self) -> None:
        self._callbacks: list[Callable[[], None]] = []
        self._done = False

    def add(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def install(self) -> None:
        atexit.register(self.shutdown)
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(signum, self._handle_signal)
            except (AttributeError, ValueError):
                pass

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        logging.getLogger("runtime").info("Shutdown signal received: %s", signum)
        self.shutdown()
        raise SystemExit(128 + signum)

    def shutdown(self) -> None:
        if self._done:
            return
        self._done = True
        for callback in reversed(self._callbacks):
            try:
                callback()
            except Exception:
                logging.getLogger("runtime").exception("Shutdown callback failed")
        logging.getLogger("runtime").info("Shutdown complete")

