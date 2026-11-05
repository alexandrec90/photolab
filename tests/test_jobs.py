import asyncio

import pytest

from photolab.jobs import Job, JobBoard, JobBusy


async def succeed() -> str:
    return "finished"


async def explode() -> str:
    raise RuntimeError("boom")


async def test_a_job_records_its_closing_message():
    board = JobBoard()

    job = board.start("scan", succeed())
    assert job.running
    await board.wait()

    assert job.running is False
    assert job.message == "finished"


async def test_a_failing_job_says_so_and_stops_running():
    board = JobBoard()

    job = board.start("scan", explode())
    await board.wait()

    assert job.running is False
    assert job.message == "failed: boom"


async def test_the_same_job_cannot_run_twice():
    board = JobBoard()
    release = asyncio.Event()

    async def hold() -> str:
        await release.wait()
        return "done"

    board.start("scan", hold())
    with pytest.raises(JobBusy):
        board.start("scan", succeed())
    release.set()
    await board.wait()


async def test_a_finished_job_can_run_again_and_a_caller_can_supply_the_job():
    board = JobBoard()
    board.start("scan", succeed())
    await board.wait()
    mine = Job(name="scan", total=3)

    assert board.start("scan", succeed(), mine) is mine
    await board.wait()


async def test_snapshot_is_plain_data():
    board = JobBoard()
    board.start("scan", succeed())
    await board.wait()

    assert board.snapshot()[0]["name"] == "scan"


async def test_renders_run_one_at_a_time_in_order():
    board = JobBoard()
    log: list[str] = []

    async def render(name: str) -> None:
        log.append(f"{name} start")
        await asyncio.sleep(0)
        log.append(f"{name} end")

    board.render(render("a"))
    board.render(render("b"))
    await board.wait()

    assert log == ["a start", "a end", "b start", "b end"]


async def test_cancel_all_stops_running_work():
    board = JobBoard()
    job = board.start("scan", asyncio.sleep(60))

    await asyncio.sleep(0)
    board.cancel_all()
    await board.wait()

    assert job.running is False
