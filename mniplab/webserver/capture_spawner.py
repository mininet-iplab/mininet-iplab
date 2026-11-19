"""Starts the process behind a Packet Capture.

One tshark per captured Interface serves both halves of the feature: PDML
on stdout for the live pane and a pcap written with ``-w`` for the
download.  It runs in the web server process and enters the Node by its
Namespace Anchor pid through :func:`mniplab.namespace.build_nsenter_argv`
— the recipe a Terminal uses — so a Capture sees exactly the Interfaces
the Node has and nothing of the host's.  A Switch has no namespace to
enter: its ports are in the root namespace, and the caller names the one
keyed interface of the Lab's own Switch to run tshark on.

:func:`build_capture_command` is pure and holds every decision about what
tshark is asked to do; :class:`CaptureSpawner` is the only code here that
starts a process.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

from mniplab.labhandle import NodeAttachment
from mniplab.namespace import build_nsenter_argv

# The pcap is a ring of RING_FILES files of RING_FILE_KB kB each: a Capture
# left running keeps capturing while the oldest packets are discarded, so it
# never fills the host and never stops without the Learner noticing.
RING_FILE_KB = 10000
RING_FILES = 2

# Seconds tshark is given to flush its pcap and exit on SIGTERM before the
# group is killed outright.
STOP_GRACE_PERIOD = 2.0

# A Node or Interface name becomes a path component under the Lab
# directory, so only names that cannot climb out of it are accepted.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

# A capture filter is BPF handed to tshark as one argument: printable text
# of a length no expression a Learner types comes near.
_SAFE_FILTER = re.compile(r"^[\x20-\x7e]{0,256}$")

PCAP_SUFFIX = ".pcapng"

# What a filtered Capture adds to its Interface's name in the pcap's name.
_FILTER_TAG = re.compile(r"-f[0-9a-f]{10}$")


@dataclass(frozen=True)
class CaptureCommand:
    """What to run for one Packet Capture, and where its files go.

    Attributes:
        argv: The full command, starting with ``nsenter`` (``tshark`` for
            a Switch's port, which is in the root namespace already).
        pcap_path: The ``-w`` base name; tshark's ring buffer numbers the
            files it writes after it.
        log_path: Where tshark's diagnostics go, read back when it fails.
    """

    argv: List[str]
    pcap_path: str
    log_path: str


def build_capture_command(
    attachment: NodeAttachment,
    interface: str,
    lab_dir: str,
    capture_filter: str = "",
    root_interface: Optional[str] = None,
) -> CaptureCommand:
    """Build the command that captures one Interface of one Node.

    Args:
        attachment: The Node's name and Namespace Anchor pid.
        interface: The Interface to capture, as the Node names it.
        lab_dir: The Lab directory the pcap is written under, which is what
            keeps two Labs' identically named Interfaces apart.
        capture_filter: A BPF expression the kernel applies as packets are
            captured; empty captures everything.  Each filter writes its
            own pcap, so a classmate's Capture of the same Interface under
            another filter never shares a file.
        root_interface: For a Switch's port, the keyed interface that exists
            in the host's root namespace.  tshark then runs there directly,
            entering no Node, and ``attachment`` only names the Node whose
            directory the files go in (its pid is unused).  The caller must
            have taken the name from the Lab's own Switch, never from a
            viewer.

    Returns:
        The command and the paths of the files it writes.

    Raises:
        ValueError: The Node is a Container Host, whose capture path is not
            yet proven, a name could not be a single path component, or
            the filter is not printable text of a sane length.
    """
    if attachment.container_name is not None:
        raise ValueError(
            f"Packet Capture on a Container Host ({attachment.node}) is not supported yet"
        )
    names = [("Node", attachment.node), ("Interface", interface)]
    if root_interface is not None:
        names.append(("Interface", root_interface))
    for kind, name in names:
        if not _SAFE_NAME.match(name):
            raise ValueError(f"Not a valid {kind} name for a Packet Capture: {name!r}")
    capture_filter = capture_filter.strip()
    if not _SAFE_FILTER.match(capture_filter):
        raise ValueError("A capture filter must be one line of plain text, at most 256 characters")

    directory = os.path.join(lab_dir, "captures", attachment.node)
    stem = interface
    if capture_filter:
        stem += "-f" + hashlib.sha256(capture_filter.encode()).hexdigest()[:10]
    pcap_path = os.path.join(directory, stem + PCAP_SUFFIX)
    tshark = [
        "tshark",
        "-i", interface if root_interface is None else root_interface,
        "-l",
        "-n",
        "-T", "pdml",
        "-w", pcap_path,
        "-b", f"filesize:{RING_FILE_KB}",
        "-b", f"files:{RING_FILES}",
    ]
    if capture_filter:
        tshark += ["-f", capture_filter]
    return CaptureCommand(
        argv=tshark if root_interface is not None else build_nsenter_argv(attachment.pid, tshark),
        pcap_path=pcap_path,
        log_path=os.path.join(directory, stem + ".log"),
    )


def ring_files(pcap_path: str) -> List[str]:
    """The files tshark's ring buffer has written for one ``-w`` name, oldest first.

    tshark numbers each file after the name it was given —
    ``h1-eth0_00007_20260923014330.pcapng`` for ``h1-eth0.pcapng`` — so the
    sequence number orders them.  Each is a complete pcapng section, and
    pcapng allows sections to follow one another, so the files read in this
    order are one valid capture.

    Args:
        pcap_path: A :class:`CaptureCommand`'s ``pcap_path``.

    Returns:
        The ring's files, possibly none.
    """
    stem = pcap_path[: -len(PCAP_SUFFIX)]
    pattern = glob.escape(stem) + "_" + "[0-9]" * 5 + "_*" + PCAP_SUFFIX
    return sorted(glob.glob(pattern))


def interface_rings(pcap_path: str) -> List[str]:
    """Every ring on disk for the Interface a ``-w`` name belongs to, under any filter.

    Args:
        pcap_path: A :class:`CaptureCommand`'s ``pcap_path``.

    Returns:
        The files of every filter's ring on that Interface.
    """
    directory, name = os.path.split(pcap_path)
    interface = _FILTER_TAG.sub("", name[: -len(PCAP_SUFFIX)])
    ring = re.compile(
        re.escape(interface) + r"(?:-f[0-9a-f]{10})?_[0-9]{5}_[0-9]+" + re.escape(PCAP_SUFFIX) + "$"
    )
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return []
    return sorted(os.path.join(directory, n) for n in names if ring.match(n))


class CaptureSpawner:
    """Starts a Packet Capture's tshark."""

    def spawn(self, command: CaptureCommand, keep: Iterable[str] = ()) -> "subprocess.Popen[bytes]":
        """Start the command with its PDML on a pipe.

        The process leads its own group: ``nsenter`` forks into the Node's
        PID namespace and tshark runs ``dumpcap`` beneath it, so stopping a
        Capture signals the group, never one pid.

        Every ring an ended Capture left on the same Interface is removed
        first, whatever its filter, so trying filter after filter never
        piles rings up: the Interface holds at most one ring per Capture
        running on it.

        Args:
            command: What :func:`build_capture_command` built.
            keep: The ``pcap_path`` of every Capture still running on the
                Interface, whose rings are left alone.

        Returns:
            The running process; its stdout carries the PDML stream.
        """
        os.makedirs(os.path.dirname(command.pcap_path), exist_ok=True)
        running = {path for pcap in keep for path in ring_files(pcap)}
        for stale in interface_rings(command.pcap_path):
            if stale not in running:
                os.remove(stale)
        with open(command.log_path, "wb") as log:
            return subprocess.Popen(
                command.argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                start_new_session=True,
            )

    def stop(self, process: "subprocess.Popen[bytes]") -> None:
        """Stop a Capture's process group, politely first and then not.

        SIGTERM lets tshark close the pcap cleanly; a group that has not
        exited within :data:`STOP_GRACE_PERIOD` is killed.  It returns only
        once the whole group has gone, not merely its leader: ``nsenter``
        dies on SIGTERM at once while tshark is still flushing the pcap, so
        waiting on the leader alone would report a Capture stopped while
        its tshark still ran.  Blocks, so the service calls it from an
        executor.

        Args:
            process: What :meth:`spawn` returned.
        """
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            if self._group_gone(process, STOP_GRACE_PERIOD):
                break

    @staticmethod
    def _group_gone(process: "subprocess.Popen[bytes]", timeout: float) -> bool:
        """Wait up to ``timeout`` for the leader to be reaped and every member to exit.

        Args:
            process: The group's leader.
            timeout: Seconds to wait in all.

        Returns:
            True once no process is left in the group.
        """
        deadline = time.monotonic() + timeout
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
