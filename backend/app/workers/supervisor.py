"""RQ worker supervisor (D1 overload protection).

The worker container runs this instead of a single ``rq worker`` so the number of extraction worker
processes is owner-configurable via the ``app_config`` singleton. The count is read **once at
startup** (apply-on-restart semantics — no live polling); the admin UI notes that a worker-container
restart is required to apply a change.

Behaviour:
  * read the effective ``rq_worker_count`` from the DB (fall back to the built-in default and log if
    the DB/config table is unreachable at startup — a broken DB must never leave zero workers);
  * spawn that many ``rq worker ... paracord`` child processes;
  * restart a child that dies (so a crashed worker self-heals);
  * on SIGTERM/SIGINT, terminate the children cleanly and exit.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

from app.core.config import get_settings
from app.models.app_config import _DEFAULT_RQ_WORKER_COUNT
from app.workers.queue import QUEUE_NAME

logger = logging.getLogger(__name__)

# How long to wait for children to exit on shutdown before hard-killing them.
_SHUTDOWN_GRACE_SECONDS = 10.0
# Poll interval for the supervise loop (detecting dead children).
_POLL_SECONDS = 2.0


def resolve_worker_count() -> int:
    """Return the effective RQ worker count, falling back to the default if the DB is unreachable."""
    try:
        from app.db.session import SessionLocal
        from app.services import app_config

        with SessionLocal() as db:
            count = app_config.effective_rq_worker_count(db)
    except Exception as exc:  # noqa: BLE001 - a broken DB must not leave zero workers
        logger.warning(
            "Worker supervisor: could not read rq_worker_count (%s); using default %d",
            exc,
            _DEFAULT_RQ_WORKER_COUNT,
        )
        return _DEFAULT_RQ_WORKER_COUNT
    return max(1, count)


def _worker_command() -> list[str]:
    """The ``rq worker`` command each child runs, bound to the configured Redis URL + queue."""
    return ["rq", "worker", "--url", get_settings().redis_url, QUEUE_NAME]


def _spawn() -> subprocess.Popen:
    return subprocess.Popen(_worker_command())  # noqa: S603 - fixed command, no shell


class _Supervisor:
    def __init__(self, count: int) -> None:
        self.count = count
        self.children: list[subprocess.Popen] = []
        self._stopping = False

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum, _frame) -> None:
        logger.info("Worker supervisor: received signal %s; shutting down children", signum)
        self._stopping = True

    def start(self) -> None:
        logger.info(
            "Worker supervisor: launching %d RQ worker(s) on queue %r", self.count, QUEUE_NAME
        )
        self.children = [_spawn() for _ in range(self.count)]

    def supervise(self) -> None:
        """Block, restarting any child that dies, until a shutdown signal arrives."""
        while not self._stopping:
            for index, child in enumerate(self.children):
                if child.poll() is not None and not self._stopping:
                    logger.warning(
                        "Worker supervisor: child pid=%s exited (code=%s); restarting",
                        child.pid,
                        child.returncode,
                    )
                    self.children[index] = _spawn()
            time.sleep(_POLL_SECONDS)
        self.shutdown()

    def shutdown(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
        for child in self.children:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                logger.warning("Worker supervisor: child pid=%s did not exit; killing", child.pid)
                child.kill()


def main() -> int:
    """Entry point: read the count once, launch children, and supervise until terminated."""
    logging.basicConfig(
        level=os.environ.get("PARACORD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    supervisor = _Supervisor(resolve_worker_count())
    supervisor._install_signal_handlers()
    supervisor.start()
    supervisor.supervise()
    return 0


if __name__ == "__main__":
    sys.exit(main())
