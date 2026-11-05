"""Work that outlives a request.

This is one person's album on one machine, so jobs live in memory and the database is the
queue: a photo with no annotation still has none after a restart, and the next run picks
it up. The stack's Redis is there for when this stops being true.
"""

import asyncio
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from typing import Any


class JobBusy(Exception):
    """That job is already running."""


@dataclass
class Job:
    """Progress of a named job, as the album shows it."""

    name: str
    running: bool = False
    done: int = 0
    failed: int = 0
    total: int = 0
    message: str = ""


class JobBoard:
    """Named jobs, one at a time each, plus a strictly serial render queue."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        # Renders are CPU work on a machine with four cores, and one matte can use them all.
        self._render_gate = asyncio.Semaphore(1)

    def snapshot(self) -> list[dict[str, Any]]:
        return [asdict(job) for job in self.jobs.values()]

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start(self, name: str, work: Coroutine[Any, Any, str], job: Job | None = None) -> Job:
        """Run `work`, whose return value becomes the job's closing message."""
        existing = self.jobs.get(name)
        if existing is not None and existing.running:
            work.close()
            raise JobBusy(name)
        current = job or Job(name=name)
        current.running = True
        self.jobs[name] = current

        async def run() -> None:
            try:
                current.message = await work
            except Exception as exc:
                current.message = f"failed: {exc}"
                # Raised on, so that asyncio reports the traceback in the server's log.
                raise
            finally:
                current.running = False

        self._spawn(run())
        return current

    def render(self, work: Coroutine[Any, Any, None]) -> None:
        """Queue a render behind any already running."""

        async def run() -> None:
            async with self._render_gate:
                await work

        self._spawn(run())

    async def wait(self) -> None:
        """Let everything queued finish. Tests use it; so could a clean shutdown."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def cancel_all(self) -> None:
        for task in self._tasks:
            task.cancel()
