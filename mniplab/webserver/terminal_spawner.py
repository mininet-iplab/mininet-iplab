"""Spawns the OS process behind a Terminal.

Encapsulates the fork/exec logic so that TerminalService can focus on
bookkeeping.  The mechanism is pty(7) — the one place in this package where
that word names something real rather than the Terminal it delivers.

Attachment is ``nsenter`` against the Node's namespaces, built by
:func:`mniplab.namespace.build_nsenter_argv` — the same recipe
``util/connect_node.sh`` restates in shell, so the two Terminal entry points
cannot drift apart.
"""

import os
import pty
import shlex
import time
from typing import Optional, Tuple

from mniplab.labhandle import NodeAttachment
from mniplab.namespace import build_nsenter_argv

# How long spawn waits for nsenter's forked child to appear, and how long
# between attempts.  Entering a PID namespace forces nsenter to fork, so the
# Learner's shell is a grandchild of the forked child; these bound the wait
# for the grandchild to be born.  A second is generous — the fork follows the
# exec immediately — and the alternative to a short wait is returning the
# wrapper's pid, which signals and reaps the wrong process.
INNER_PID_TIMEOUT = 1.0
INNER_PID_POLL_INTERVAL = 0.01


class TerminalSpawner:
    """Spawns a Terminal's shell inside a Mininet node namespace or container."""

    def spawn(self, attachment: NodeAttachment) -> Tuple[int, int]:
        """Fork a pty(7) pair and exec into the target node.

        In the child process this method does not return — it replaces the
        process image via os.execvp().  In the parent it resolves and returns
        the pid of the process that actually runs inside the Node, which is
        not the forked child: entering a PID namespace makes ``nsenter``
        fork, so the shell is its child.  Signalling or reaping the wrapper
        would strand the shell, so the returned pid is the inner one.

        Args:
            attachment: The Node's name, Namespace Anchor pid, home directory
                and, for a Container Host, its container name.

        Returns:
            Tuple of (inner child pid, terminal_fd).  On a ContainerHost the
            returned pid is the forked child's own: ``docker exec`` does not
            fork, so there is no inner pid to resolve.

        Raises:
            OSError: The spawned nsenter died before it could fork — the
                Node has gone away or its namespaces are not enterable.
        """
        child_pid, terminal_fd = pty.fork()

        if child_pid == 0:
            node_name = attachment.node
            safe_home = shlex.quote(attachment.home_dir)
            ps1_val = f"{node_name}# "

            if attachment.container_name is not None:
                os.execvp(
                    "docker",
                    [
                        "docker",
                        "exec",
                        "-it",
                        attachment.container_name,
                        "env",
                        f"HOME={safe_home}",
                        f"PS1={ps1_val}",
                        "/bin/sh",
                        "-i",
                    ],
                )
            else:
                safe_ps1 = shlex.quote(ps1_val)
                command = [
                    "bash",
                    "--norc",
                    "-c",
                    f"export HOME={safe_home} PS1={safe_ps1}; "
                    f"cd {safe_home}; exec bash --norc",
                ]
                os.execvp("nsenter", build_nsenter_argv(attachment.pid, command))

        # docker exec never forks, so for a container the forked child *is*
        # the process to signal; resolving an inner pid must not go looking
        # for docker's children.
        inner_pid = child_pid
        if attachment.container_name is None:
            inner_pid = _resolve_inner_pid(child_pid, terminal_fd)
        return inner_pid, terminal_fd


def _resolve_inner_pid(child_pid: int, terminal_fd: int) -> int:
    """Return the pid of nsenter's forked child — the Terminal's real shell.

    Polls the forked child's procfs ``children`` entry until nsenter's fork
    lands.  A live nsenter with no child yet would appear on the next poll,
    so a timeout means it died at startup, and the raise is what a dead Node
    surfaces as.

    Args:
        child_pid: The forked child's pid (the nsenter wrapper).
        terminal_fd: The pty(7) master, closed here on failure so a failed
            spawn leaks no descriptor.

    Returns:
        The pid of the process running inside the Node's namespaces.

    Raises:
        OSError: The child never appeared — nsenter failed to exec or the
            target's namespaces were not enterable.
    """
    deadline = time.monotonic() + INNER_PID_TIMEOUT
    while time.monotonic() < deadline:
        pid = _sole_child_pid(child_pid)
        if pid is not None:
            return pid
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(INNER_PID_POLL_INTERVAL)
    os.close(terminal_fd)
    raise OSError(
        f"nsenter (pid {child_pid}) exited before entering the node's "
        f"namespaces — is the node still running?"
    )


def _sole_child_pid(pid: int) -> Optional[int]:
    """Read a process's first child from procfs, or None when it has none.

    Args:
        pid: The parent whose children entry is read.

    Returns:
        The first child pid, or None when the entry is absent or empty.
    """
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as children:
            entry = children.read().split()
    except (OSError, ValueError):
        return None
    if not entry:
        return None
    return int(entry[0])
