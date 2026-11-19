"""Packet Captures: one tshark per Interface, shared by everyone watching it.

The shape mirrors :class:`~mniplab.webserver.broadcast.TopologyBroadcastService`
— a set of subscribers fed from one source — with three differences a live
packet feed needs:

* **Each viewer has a bounded queue and a slow one drops rather than
  blocks,** so one stalled browser cannot stall the feed for everyone else.
  Unlike the peer this was modelled on, the drop is counted and the viewer
  told, because a gap a Learner cannot see reads as an absence.
* **A Capture outlives its last viewer by a grace window** — the Terminal
  idle timeout — so an accidental reload re-attaches to the running Capture
  and replays what it missed, while a Capture nobody returns to still ends.
* **A rate guard** stops sending field trees above 200 packets a second
  sustained over one second, and resumes below 100 so it cannot flap.  The
  pane is told either way.  No exchange a Guide teaches comes near it; an
  ``iperf3`` or ``ping -f`` on the captured Interface does.

A Capture is one Interface under one capture filter.  The filter is BPF,
applied in the kernel as packets are captured, so another filter is another
Capture — a Learner who changes filter starts a new one rather than
restarting a classmate's under them.

A Capture belongs to a Lab key and ends with the Lab: the registry stops a
Lab's Captures before the Lab itself.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from mniplab.labhandle import NodeAttachment
from mniplab.logging import get_logger
from mniplab.utils import host_network_refusal
from .capture_spawner import CaptureCommand, CaptureSpawner, build_capture_command
from .pdml import PacketSplitter, summarize_packet

logger = get_logger(__name__)

# Messages one viewer may have waiting before it starts dropping packets.
QUEUE_SIZE = 256

# Packets kept for a viewer who arrives late or reloads: enough to hold an
# exchange a Guide teaches (a DHCP or BGP session is a handful) with room.
HISTORY_SIZE = 100

# The rate guard: engage at this many packets within WINDOW seconds,
# release below RELEASE_RATE.  Eight times the loudest exchange the Labs
# produce, below where a slow browser starts to fall behind.
ENGAGE_RATE = 200
RELEASE_RATE = 100
WINDOW = 1.0

# Why a Capture ended, as the pane is told.
NO_VIEWERS_REASON = "No one is watching"
PANE_CLOSED_REASON = "Capture closed"

# Captures one viewer may watch at once: exactly the two a before-and-after
# comparison across a Router needs.
MAX_CAPTURES_PER_VIEWER = 2

# The node types whose Interfaces a Capture may attach to.  A Container
# Host's capture path is unproven; a NAT Gateway is hidden from the
# Topology and never reachable.  A Switch's ports live in the host's root
# namespace, which every Lab and the host share, so they are safe only
# under an exact-membership rule: the client names one plain port of a
# Switch in this Lab's Topology, the Lab's own Switch lists its ports
# (never the bridge's internal port or anything else in that namespace),
# and the name that tshark opens is the keyed one the Switch supplies.
CAPTURABLE_TYPES = frozenset({"router", "host", "bgp-speaker", "switch"})

# The node type whose Interfaces are in the root namespace, not a Node's own.
SWITCH_TYPE = "switch"

Key = Tuple[str, str, str, str]

# A line in which tshark, dumpcap or nsenter says what went wrong, e.g.
# 'tshark: There is no device named "eth9".'
_COMPLAINT = re.compile(r"^(?:tshark|dumpcap|nsenter): (.*)$")

# tshark refusing a capture filter: the expression on one line, libpcap's
# reason on a later one.
_INVALID_FILTER = re.compile(r'^Invalid capture filter "(.*)" for interface')
_FILTER_REASON = re.compile(r"^That string isn't a valid capture filter \((.*)\)\.?$")


class CaptureLimitError(ValueError):
    """A viewer asked for more Captures than one viewer may watch at once."""


def capture_node_error(topology: Dict[str, Any], node: str) -> Optional[str]:
    """Say why a Node's Interfaces may not be captured, or None when they may.

    The invariant is that a Packet Capture shows only its own Lab's
    traffic.  A Router, Host or Speaker is a namespace of its own, so every
    Interface it has satisfies it — including one whose Link is not drawn,
    such as a Router's end of the Link to an Internet Gateway.  A Switch
    satisfies it only through the ports its Lab lists for it (see
    :func:`capture_target_error`).  An Internet Gateway is refused here with
    the Internet reason, and before this check by the Lab handle's lever
    refusal: its uplink carries every Lab's traffic and the host's.  A Node
    absent from the Topology is refused here.

    Args:
        topology: The Lab's Topology payload.
        node: The Node asked for.

    Returns:
        A reason to show the Learner, or None.
    """
    kinds = {entry.get("id"): entry.get("type") for entry in topology.get("nodes", [])}
    kind = kinds.get(node)
    if kind is None:
        return f"No Node named {node} in this Lab"
    if kind == "internet":
        return host_network_refusal(node, is_switch=False)
    if kind == "container":
        return f"Packet Capture on a Container Host ({node}) is not supported yet"
    if kind not in CAPTURABLE_TYPES:
        return f"{node} is a {kind}; capture one of its neighbours' Interfaces instead"
    return None


def is_switch(topology: Dict[str, Any], node: str) -> bool:
    """Whether a Node of the Topology is a Switch, whose ports are in the root namespace."""
    return any(
        entry.get("id") == node and entry.get("type") == SWITCH_TYPE
        for entry in topology.get("nodes", [])
    )


def capture_target_error(
    topology: Dict[str, Any], node: str, interface: str, interfaces: List[str]
) -> Optional[str]:
    """Say why an Interface may not be captured, or None when it may.

    Args:
        topology: The Lab's Topology payload.
        node: The Node asked for.
        interface: The Interface asked for.
        interfaces: The names of every Interface the Node has.

    Returns:
        A reason to show the Learner, or None.
    """
    refusal = capture_node_error(topology, node)
    if refusal is not None:
        return refusal
    if interface not in interfaces:
        return f"{node} has no Interface named {interface}"
    return None


class RateGuard:
    """Decide whether the feed is too fast to carry field trees.

    Attributes:
        active: Whether trees are currently being withheld.
    """

    def __init__(self) -> None:
        self.active = False
        self._arrivals: Deque[float] = deque()

    def observe(self, now: float) -> Optional[bool]:
        """Count one packet and say whether the guard changed state.

        Args:
            now: The packet's arrival, in seconds on a monotonic clock.

        Returns:
            True when the guard just engaged, False when it just released,
            None when nothing changed.
        """
        self._arrivals.append(now)
        while self._arrivals and self._arrivals[0] <= now - WINDOW:
            self._arrivals.popleft()
        rate = len(self._arrivals)
        if not self.active and rate >= ENGAGE_RATE:
            self.active = True
            return True
        if self.active and rate < RELEASE_RATE:
            self.active = False
            return False
        return None


class CaptureSubscriber:
    """One viewer's bounded share of a Capture's feed.

    Attributes:
        capture: The Capture this viewer is watching.
        viewer: Who is watching — one signed-in browser session — for the
            limit on how many Captures one viewer holds; None is unlimited.
    """

    def __init__(self, capture: "Capture", size: int, viewer: Optional[str] = None) -> None:
        self.capture = capture
        self.viewer = viewer
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=size)
        self._dropped = 0

    def offer(self, message: Dict[str, Any], control: bool = False) -> None:
        """Queue a message without ever waiting for the viewer.

        A full queue drops the packet and counts it.  A control message —
        the guard changing, the Capture ending — is never the one dropped:
        the oldest packet makes room for it instead.

        Args:
            message: What to send.
            control: Whether the message must not be lost.
        """
        if self._queue.full():
            if not control:
                self._dropped += 1
                return
            self._queue.get_nowait()
            self._dropped += 1
        self._queue.put_nowait(message)

    async def get(self) -> List[Dict[str, Any]]:
        """Wait for the next message, preceded by a drop notice if any were lost.

        Returns:
            The messages to send, in order.
        """
        message = await self._queue.get()
        if not self._dropped:
            return [message]
        dropped, self._dropped = self._dropped, 0
        return [{"type": "dropped", "count": dropped}, message]


@dataclass(eq=False)
class Capture:
    """One running tshark on one Interface, and who is watching it."""

    key: Key
    command: CaptureCommand
    process: Any
    started_at: float
    subscribers: Set[CaptureSubscriber] = field(default_factory=set)
    history: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))
    guard: RateGuard = field(default_factory=RateGuard)
    splitter: PacketSplitter = field(default_factory=PacketSplitter)
    grace_task: Optional["asyncio.Task[None]"] = None
    ended: bool = False

    @property
    def lab_key(self) -> str:
        """The key of the Lab this Capture belongs to."""
        return self.key[0]

    @property
    def capture_filter(self) -> str:
        """The BPF expression the kernel applies; empty for everything."""
        return self.key[3]

    def describe(self) -> Dict[str, Any]:
        """The first message a viewer receives: what it is watching."""
        return {
            "type": "capture",
            "node": self.key[1],
            "interface": self.key[2],
            "filter": self.capture_filter,
            "started_at": self.started_at,
            "rate_guard": self.guard.active,
        }


class CaptureService:
    """Every running Packet Capture, keyed by Lab, Node and Interface."""

    def __init__(
        self,
        spawner: Optional[Any] = None,
        grace_seconds: Optional[float] = None,
        queue_size: int = QUEUE_SIZE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with no Captures running.

        Args:
            spawner: What starts and stops tshark; tests hand in a fake.
            grace_seconds: How long a Capture outlives its last viewer; the
                Terminal idle timeout when omitted.
            queue_size: Messages one viewer may have waiting.
            clock: The monotonic clock the rate guard reads.
        """
        if grace_seconds is None:
            from .config import settings
            grace_seconds = float(settings.terminal_idle_timeout)
        self._spawner: Any = spawner if spawner is not None else CaptureSpawner()
        self._grace = grace_seconds
        self._queue_size = queue_size
        self.clock = clock
        self._captures: Dict[Key, Capture] = {}
        self._ending: Set["asyncio.Task[None]"] = set()
        self._lock = asyncio.Lock()

    def captures_on(self, lab_key: str) -> int:
        """How many Captures are running on one Lab."""
        return sum(1 for capture in self._captures.values() if capture.lab_key == lab_key)

    async def subscribe(
        self,
        lab_key: str,
        lab_dir: str,
        attachment: NodeAttachment,
        interface: str,
        capture_filter: str = "",
        viewer: Optional[str] = None,
        root_interface: Optional[str] = None,
    ) -> CaptureSubscriber:
        """Watch an Interface, starting its Capture if nobody else is.

        The new viewer's queue is primed with what it is watching and the
        packets the Capture still holds, so a reload loses nothing recent.

        Args:
            lab_key: The Lab the Node is in.
            lab_dir: That Lab's directory, where the pcap is written.
            attachment: The Node's Namespace Anchor, as a Terminal gets it.
            interface: The Interface to watch.
            capture_filter: The BPF expression to capture through; empty
                for everything.
            viewer: Who is watching, held to
                :data:`MAX_CAPTURES_PER_VIEWER`; None is not limited.
            root_interface: For a Switch's port, the keyed interface in the
                root namespace, supplied by the Lab and never a viewer.

        Returns:
            The new viewer's subscription.

        Raises:
            CaptureLimitError: The viewer already watches as many Captures
                as one viewer may.
            ValueError: The Capture could not be started.
        """
        key = (lab_key, attachment.node, interface, capture_filter.strip())
        async with self._lock:
            if viewer is not None:
                held = {
                    other for other, capture in self._captures.items()
                    if any(s.viewer == viewer for s in capture.subscribers)
                }
                if key not in held and len(held) >= MAX_CAPTURES_PER_VIEWER:
                    raise CaptureLimitError(
                        "You already have two Captures open, the most one viewer "
                        "may run; stop one to open another"
                    )
            capture = self._captures.get(key)
            if capture is None:
                capture = self._start(key, lab_dir, attachment, interface, root_interface)
            if capture.grace_task is not None:
                capture.grace_task.cancel()
                capture.grace_task = None
            subscriber = CaptureSubscriber(capture, self._queue_size, viewer)
            subscriber.offer(capture.describe(), control=True)
            for message in capture.history:
                subscriber.offer(message)
            capture.subscribers.add(subscriber)
        logger.info(f"Capture {key} watched by {len(capture.subscribers)}")
        return subscriber

    async def unsubscribe(self, subscriber: CaptureSubscriber, linger: bool = True) -> None:
        """Stop watching.  The last viewer out starts the grace window.

        Args:
            subscriber: The subscription to end.
            linger: False when the viewer closed the pane on purpose: the
                grace window is for a reload, not for a Learner who is done.
        """
        capture = subscriber.capture
        async with self._lock:
            capture.subscribers.discard(subscriber)
            if capture.subscribers or capture.ended:
                return
            if linger:
                capture.grace_task = asyncio.create_task(self._expire(capture))
                return
        await self._stop(capture, PANE_CLOSED_REASON, only_if_unwatched=True)

    async def stop_lab_captures(self, lab_key: str, reason: str) -> None:
        """Stop every Capture of one Lab, telling each viewer why.

        Args:
            lab_key: The Lab being stopped.
            reason: What the panes are told.
        """
        for capture in [c for c in self._captures.values() if c.lab_key == lab_key]:
            await self._stop(capture, reason)

    async def stop_all(self, reason: str) -> None:
        """Stop every Capture, as the server's shutdown does."""
        for capture in list(self._captures.values()):
            await self._stop(capture, reason)

    # -- Internals ---------------------------------------------------------

    def _start(
        self,
        key: Key,
        lab_dir: str,
        attachment: NodeAttachment,
        interface: str,
        root_interface: Optional[str] = None,
    ) -> Capture:
        """Spawn tshark for an Interface and start reading it."""
        command = build_capture_command(
            attachment, interface, lab_dir, key[3], root_interface=root_interface
        )
        running = [c.command.pcap_path for k, c in self._captures.items() if k[:3] == key[:3]]
        try:
            process = self._spawner.spawn(command, keep=running)
        except OSError as e:
            raise ValueError(f"Cannot capture {interface} on {attachment.node}: {e}") from e
        capture = Capture(key=key, command=command, process=process, started_at=time.time())
        self._captures[key] = capture
        fd = process.stdout.fileno()
        os.set_blocking(fd, False)
        asyncio.get_running_loop().add_reader(fd, self._on_readable, capture)
        logger.info(f"Capture started on {key}: {command.pcap_path}")
        return capture

    def _on_readable(self, capture: Capture) -> None:
        """Read what tshark has written and fan out every packet it completed."""
        fd = capture.process.stdout.fileno()
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(fd)
            # Held until done: the loop keeps only a weak reference to a task.
            reason, filter_error = self._exit_reason(capture)
            task = asyncio.create_task(self._stop(capture, reason, filter_error=filter_error))
            self._ending.add(task)
            task.add_done_callback(self._ending.discard)
            return
        for packet in capture.splitter.feed(data):
            self._publish(capture, packet)

    def _publish(self, capture: Capture, packet: str) -> None:
        """Send one packet to every viewer, with or without its tree."""
        change = capture.guard.observe(self.clock())
        if change is not None:
            notice = {"type": "rate_guard", "active": change}
            for subscriber in capture.subscribers:
                subscriber.offer(notice, control=True)
        summary = summarize_packet(packet)
        if summary["time"] is not None:
            summary["relative_time"] = max(0.0, summary["time"] - capture.started_at)
        message: Dict[str, Any] = {"type": "packet", "summary": summary}
        if not capture.guard.active:
            message["pdml"] = packet
        capture.history.append(message)
        for subscriber in capture.subscribers:
            subscriber.offer(message)

    async def _expire(self, capture: Capture) -> None:
        """End a Capture nobody came back to within the grace window."""
        await asyncio.sleep(self._grace)
        capture.grace_task = None
        await self._stop(capture, NO_VIEWERS_REASON, only_if_unwatched=True)

    async def _stop(
        self,
        capture: Capture,
        reason: str,
        only_if_unwatched: bool = False,
        filter_error: bool = False,
    ) -> None:
        """Take a Capture off the table, stop its tshark, and tell its viewers.

        Args:
            capture: The Capture to stop.
            reason: What its viewers are told.
            only_if_unwatched: Stop only if nobody is watching once the lock
                is held — a viewer who attached while the last one was
                leaving keeps the Capture rather than being handed its end.
            filter_error: The Capture ended because tshark refused its
                filter, which the pane offers to correct.
        """
        async with self._lock:
            if self._captures.get(capture.key) is not capture:
                return
            if only_if_unwatched and capture.subscribers:
                return
            del self._captures[capture.key]
            capture.ended = True
            if capture.grace_task is not None and capture.grace_task is not asyncio.current_task():
                capture.grace_task.cancel()
            subscribers = list(capture.subscribers)
        try:
            asyncio.get_running_loop().remove_reader(capture.process.stdout.fileno())
        except (OSError, ValueError):
            pass
        await asyncio.get_running_loop().run_in_executor(None, self._spawner.stop, capture.process)
        try:
            capture.process.stdout.close()
        except OSError:
            pass
        notice: Dict[str, Any] = {"type": "ended", "reason": reason}
        if filter_error:
            notice["filter_error"] = True
        for subscriber in subscribers:
            subscriber.offer(notice, control=True)
        logger.info(f"Capture stopped on {capture.key}: {reason}")

    @staticmethod
    def _exit_reason(capture: Capture) -> Tuple[str, bool]:
        """Why tshark ended on its own, and whether its filter was the cause.

        A refused filter is told in the Learner's terms — which expression,
        and libpcap's reason — rather than as tshark's two-part complaint;
        anything else is the last thing tshark or nsenter complained of.
        """
        try:
            with open(capture.command.log_path, "r", encoding="utf-8", errors="replace") as log:
                lines = log.read().splitlines()
        except OSError:
            lines = []
        complaints = [m.group(1).strip() for m in map(_COMPLAINT.match, lines) if m and m.group(1).strip()]
        for complaint in complaints:
            invalid = _INVALID_FILTER.match(complaint)
            if invalid:
                why = next((m.group(1) for m in map(_FILTER_REASON.match, lines) if m), None)
                reason = f'The filter "{invalid.group(1)}" is not a valid capture filter'
                return (f"{reason}: {why}" if why else reason), True
        return (complaints[-1] if complaints else "The capture ended"), False
