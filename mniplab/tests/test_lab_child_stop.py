"""Lab shutdown still cleans up when the process exits before a kill."""

from __future__ import annotations

import asyncio
from asyncio.subprocess import Process
from pathlib import Path
from typing import cast

import pytest

from mniplab.errors import LabUnavailableError
from mniplab.labprocess import LabChild
from mniplab.labrpc import RemoteLabHandle

pytestmark = pytest.mark.unit


class _DisappearingProcess:
    """Process boundary whose clean exit races with a shutdown deadline."""

    def __init__(self, wait_times_out: bool) -> None:
        self.returncode: int | None = None
        self.wait_times_out = wait_times_out

    async def wait(self) -> int:
        """Expose a deadline before the transport observes the clean exit."""
        if self.wait_times_out:
            self.wait_times_out = False
            raise asyncio.TimeoutError
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        """Report that the operating system no longer has this process."""
        self.returncode = 0
        raise ProcessLookupError("Lab process already exited")


class _StopHandle:
    """RPC boundary that can lose the reply as the Lab shuts down."""

    def __init__(
        self, process: _DisappearingProcess, socket_path: Path, lose_reply: bool
    ) -> None:
        self.process = process
        self.socket_path = str(socket_path)
        self.lose_reply = lose_reply

    def stop(self) -> None:
        """Let the Lab exit normally before reporting the lost RPC reply."""
        if self.lose_reply:
            self.process.returncode = 0
            raise LabUnavailableError("Lab stopped while answering stop")


@pytest.mark.parametrize("race", ["lost-stop-reply", "wait-timeout"])
def test_stop_sweeps_a_lab_that_exits_before_kill(
    race: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean process exit during shutdown preserves the full keyed cleanup."""
    key = "stop-race"
    socket_path = tmp_path / "lab.sock"
    socket_path.touch()
    swept: list[str] = []
    released: list[str] = []

    async def sweep_key(lab_key: str) -> None:
        swept.append(lab_key)

    monkeypatch.setattr("mniplab.labprocess.sweep_key", sweep_key)
    monkeypatch.setattr("mniplab.labprocess.DEFAULT_ALLOCATOR.release", released.append)
    process = _DisappearingProcess(wait_times_out=race == "wait-timeout")
    handle = _StopHandle(process, socket_path, lose_reply=race == "lost-stop-reply")
    child = LabChild(
        key,
        "tiny-lab",
        cast(Process, process),
        cast(RemoteLabHandle, handle),
    )

    asyncio.run(child.stop())

    assert not child.alive
    assert child.exit_reason == "stopped"
    assert swept == [key]
    assert released == [key]
    assert not socket_path.exists()
