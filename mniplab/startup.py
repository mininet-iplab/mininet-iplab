"""Per-Node Startup commands run at declared stages of a Lab's start.

A Startup command is authored shell setup that belongs to one Node. It runs
at one of two stages of :meth:`mnIPLab.start`:

``before-daemons``
    Interfaces are up; FRR, ExaBGP, NAT and Services have not started, so
    what a command sets up (a loopback address, say) is there when a daemon
    reads its configuration.
``after-start``
    The Lab has started: IPv6 is reconciled, Relays run, Hosts are ready.

Within a stage every Node runs in parallel and each Node runs its own
commands in listed order, so a stage costs about its slowest Node. A command
has a timeout (5 s unless overridden, 1–60 s) and the whole stage has a cap.
A failure or timeout stops that Node's remaining commands and fails the
start, unless the command may fail, which turns it into a warning.
"""

from __future__ import annotations

import math
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

STAGES = ("before-daemons", "after-start")
DEFAULT_TIMEOUT = 5
MAX_TIMEOUT = 60
STAGE_CAP = 120.0
_STATUS_MARKER = "__mniplab_status="
_TIMED_OUT = 124
_KILLED = 137


def check_timeout(timeout: object) -> None:
    """Refuse a Startup command timeout outside whole seconds 1–60.

    Raises:
        ValueError: The timeout is not an integer in range.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"Startup command timeout must be 1–{MAX_TIMEOUT} seconds")


class StartupCommandError(Exception):
    """Raised when a Startup command that must succeed fails or times out."""


@dataclass
class _Command:
    node: Any
    command: str
    stage: str
    timeout: int
    may_fail: bool


class StartupCommands:
    """The Startup commands of one Lab, run stage by stage."""

    def __init__(self, stage_cap: float = STAGE_CAP) -> None:
        """Keep commands with a cap on each stage's total time.

        Args:
            stage_cap: Seconds a whole stage may take.
        """
        self.stage_cap = stage_cap
        self._commands: List[_Command] = []

    def add(self, node: Any, command: str, stage: str,
            timeout: int = DEFAULT_TIMEOUT, may_fail: bool = False) -> None:
        """Declare one Startup command for a Node.

        Args:
            node: The Node whose shell runs the command.
            command: A shell command line.
            stage: ``before-daemons`` or ``after-start``.
            timeout: Seconds before the command is killed (1–60).
            may_fail: Report a failure as a warning instead of failing start.

        Raises:
            ValueError: The stage, command or timeout is invalid.
        """
        if stage not in STAGES:
            raise ValueError(f"Unknown Startup command stage '{stage}'")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("A Startup command cannot be empty")
        check_timeout(timeout)
        self._commands.append(_Command(node, command, stage, timeout, bool(may_fail)))

    def run(self, stage: str) -> List[str]:
        """Run one stage's commands: Nodes in parallel, each in listed order.

        Args:
            stage: The stage to run.

        Returns:
            Warnings from commands that were allowed to fail.

        Raises:
            StartupCommandError: A command that must succeed failed or timed out.
        """
        by_node: Dict[int, List[_Command]] = {}
        for command in self._commands:
            if command.stage == stage:
                by_node.setdefault(id(command.node), []).append(command)
        if not by_node:
            return []
        deadline = time.monotonic() + self.stage_cap
        warnings: List[str] = []
        errors: List[str] = []
        lock = threading.Lock()

        def run_node(commands: List[_Command]) -> None:
            for command in commands:
                problem = self._run_one(command, deadline)
                if problem is None:
                    continue
                with lock:
                    (warnings if command.may_fail else errors).append(problem)
                if not command.may_fail:
                    return

        threads = [threading.Thread(target=run_node, args=(commands,), daemon=True)
                   for commands in by_node.values()]
        for thread in threads:
            thread.start()
        # A Node shell that hangs outside timeout(1) must not hold the start.
        for thread, commands in zip(threads, by_node.values()):
            thread.join(max(0.0, deadline - time.monotonic()) + 5)
            if thread.is_alive():
                with lock:
                    errors.append(f"Startup commands on {commands[0].node.name} did not finish "
                                  "within the stage's time limit")
        if errors:
            raise StartupCommandError("\n".join(errors))
        return warnings

    @staticmethod
    def _run_one(command: _Command, deadline: float) -> Optional[str]:
        """Run one command; describe its failure, or return None."""
        shown = command.command if len(command.command) <= 200 else command.command[:197] + "..."
        label = f"Startup command on {command.node.name} ({command.stage.replace('-', ' ')}) '{shown}'"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return f"{label} did not run: the stage exceeded its time limit"
        timeout = min(command.timeout, math.ceil(remaining))
        wrapped = (
            f"timeout -k 1 {timeout} sh -c {shlex.quote(command.command)} </dev/null 2>&1;"
            f" echo {_STATUS_MARKER}$?"
        )
        output = command.node.cmd(wrapped)
        body, _, status_text = output.rpartition(_STATUS_MARKER)
        try:
            status = int(status_text.split()[0])
        except (IndexError, ValueError):
            status = -1
        if status == 0:
            return None
        tail = body.replace("\r", "").strip()[-500:]
        reason = (f"timed out after {timeout} s" if status == _TIMED_OUT
                  else f"was killed (timeout or signal) after at most {timeout} s" if status == _KILLED
                  else f"failed with exit status {status}")
        return f"{label} {reason}" + (f": {tail}" if tail else "")
