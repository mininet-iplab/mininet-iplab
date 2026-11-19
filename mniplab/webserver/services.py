"""Web UI backend services — extracted from SessionRegistry for testability."""

import asyncio
import codecs
import math
import os
import signal
import time
import uuid
from typing import Any, Awaitable, Callable, Optional, Dict, List, Set, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import status, WebSocket

from mniplab.labhandle import LabHandle, as_handle
from mniplab.labprocess import LabChild, LabStartError
from mniplab.logging import get_logger
from mniplab.persistent_config import discard_copy, read_copy_structure, record_copy_structure
from .broadcast import TopologyBroadcastService
from .config import settings
from .prompt import PromptService
from .pseudo_terminal import PendingInput
from .repositories import GuideRepository, resolve_lab_name
from .terminal_spawner import TerminalSpawner

logger = get_logger(__name__)

# Reason sent to a browser whose Terminal is closed because the Session that
# owned it was destroyed.
SESSION_ENDED_REASON = "Session ended"

# Reason sent to a browser whose Terminal is closed because the shell behind
# it exited — the Node it was attached to has stopped.  Distinct from
# SESSION_ENDED_REASON so a Learner is told *which* end went away.
NODE_EXITED_REASON = "Node exited"

# Reason sent to a browser whose Terminal is closed because the Lab's own
# process ended — every Node with it — before anyone asked it to stop.
LAB_STOPPED_REASON = "Lab stopped"

# Seconds a Terminal's shell is given to exit on SIGTERM before it is killed.
# The wait is paid for the shell, not the attach: an interactive bash ignores
# SIGTERM, and although the nsenter wrapper dies immediately the Learner's
# shell inside the Node does not.
REAP_GRACE_PERIOD = 0.5

# Reason sent to a page reattaching a Terminal that ended because no page came
# back for it within the grace window.
DETACHED_EXPIRED_REASON = "Terminal ended after the page was away too long"

# Reason sent to a page reattaching a Terminal the server knows nothing of — or
# one that is not that page's to have.
UNKNOWN_TERMINAL_REASON = "Terminal ended"

# Reason sent to the connection a Terminal is taken from when another tab of
# the same Session reattaches it — a duplicated browser tab copies the list
# of Terminals, so it names the same ones.
MOVED_REASON = "This Terminal moved to another tab"

# Close code for that connection.  Neither 1008 (a refusal, retried on a click)
# nor 1000 (an ending), nor a plain drop (reattached at once): the page must
# not reconnect by itself, or two tabs would take the Terminal from each other
# for ever.  terminal-tabs.js names the same code.
MOVED_CLOSE_CODE = 4001

# Reason recorded for a Terminal its page closed on purpose.
CLOSED_REASON = "Terminal closed"

# Reason sent to a page reattaching a detached Terminal that its Session, at
# its cap, ended to make room for a new one.
EVICTED_REASON = "Terminal ended to make room for a newer one"

# Bytes of recent output a Terminal keeps to replay on reattach.
REPLAY_BUFFER_SIZE = 64 * 1024

# How many ended Terminals are remembered, so a page reattaching one is told
# why it ended rather than only that it did.
ENDED_HISTORY = 256


@dataclass
class Terminal:
    """One live interactive shell attached to a node.

    A Terminal outlives its browser connection: one dropped without a close
    on purpose leaves it detached, its shell running and its output kept in
    ``recent_output``, until a page reattaches it or ``grace_task`` ends it.

    ``client_id`` is the owning Session's token, so a Terminal always knows
    which Session must release it; ``lab_key`` names the Lab its Node is
    in, so stopping that Lab closes exactly its Terminals.  ``websocket`` is the browser connection
    delivering the Terminal, held so that a Terminal closed from outside its
    own handler — when its Session is destroyed — can tell the browser why.
    ``input`` holds typing and pastes the shell has not read yet, so a busy
    program never blocks the server; it too outlives the connection.
    """
    id: str
    node: str
    client_id: str
    username: str
    lab_key: str
    terminal_fd: int
    child_pid: int
    created_at: float
    last_activity: float
    read_task: Optional[asyncio.Task] = None
    ping_task: Optional[asyncio.Task] = None
    timeout_task: Optional[asyncio.Task] = None
    websocket: Optional[WebSocket] = None
    detached_at: Optional[float] = None
    grace_task: Optional[asyncio.Task] = None
    recent_output: bytearray = field(default_factory=bytearray)
    recent_output_trimmed: bool = False
    decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="ignore")
    )
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    input: PendingInput = field(init=False)

    def __post_init__(self) -> None:
        """Start with no input pending."""
        self.input = PendingInput(self.terminal_fd)

    def keep_output(self, data: bytes) -> None:
        """Add output to the recent-output buffer, dropping its oldest bytes.

        Args:
            data: Bytes the shell just wrote.
        """
        self.recent_output += data
        excess = len(self.recent_output) - REPLAY_BUFFER_SIZE
        if excess > 0:
            del self.recent_output[:excess]
            self.recent_output_trimmed = True

    def replay(self) -> str:
        """The recent output as text, for a page that reattaches.

        A buffer that has dropped its oldest bytes is cut to start after its
        first newline, so the replay never begins inside an escape sequence or
        a multi-byte character.  A character still incomplete at the end is
        left to the live output, which completes it.

        Returns:
            The recent output, decoded.
        """
        data = bytes(self.recent_output)
        if self.recent_output_trimmed:
            newline = data.find(b"\n")
            data = data[newline + 1:] if newline >= 0 else b""
        return codecs.getincrementaldecoder("utf-8")(errors="ignore").decode(data)


@dataclass
class RunningLab:
    """One Lab alive under the manager, with everything the server keeps for it.

    Attributes:
        key: The Lab key, under which every route and feed reaches it.
        example: The Lab Example id it runs.
        group: The Group that owns it — a login — or None for the Lab
            everyone shares in Single-Lab Mode when nobody in particular
            started it (a Lab Example run with ``--enable-web``).
        lab: What was attached: a network object, a handle, or a double.
        handle: What every service calls.
        started_at: When it started, as ``time.time()``.
        child: Its process, when this server launched it.
        watcher: The task waiting for that process to end.
        prompt: Its Lab Prompt, multiplexed to every viewer.
        feed: Its Topology and Service feed, for the browsers on it.
        dns_locks: Per-Node locks serialising Knot zone transactions.
        resolver_locks: Per-Node locks serialising Access Control rewrites.
        rpki_locks: Per-Node locks serialising an RPKI CA's ROA and ASPA changes.
        preview: Whether this Lab is a private Instructor Preview.
        preview_config_copy: The isolated configuration directory removed
            when this Preview stops.
        builder_file: The immutable builder selected when this Lab started.
    """
    key: str
    example: str
    group: Optional[str]
    lab: Any
    handle: LabHandle
    started_at: float
    child: Optional[LabChild] = None
    watcher: Optional[asyncio.Task] = None
    prompt: PromptService = field(default_factory=PromptService)
    feed: TopologyBroadcastService = field(default_factory=TopologyBroadcastService)
    dns_locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    resolver_locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    rpki_locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    preview: bool = False
    preview_config_copy: Optional[str] = None
    builder_file: Optional[str] = None

    def visible_to(self, viewer: Optional[str], role: Optional[str]) -> bool:
        """Whether a login may see this Lab.

        Everyone sees every Lab in Single-Lab Mode; in Multi-Lab Mode a
        Group sees its own and the Instructor sees all.  A viewer of None
        is the server itself.

        Args:
            viewer: The login asking, or None.
            role: Its role.
        """
        if self.preview and role == "learner":
            return False
        if settings.lab_mode != "multi" or viewer is None or role == "instructor":
            return True
        return self.group == viewer


# How many Labs that ended on their own are remembered, so a stale page can
# still be told why its Lab is gone.
EXIT_HISTORY = 16


class LabLifecycleService:
    """Holds every running Lab behind its handle, and starts and stops them.

    The manager: in Single-Lab Mode a cap of one, in Multi-Lab Mode a Lab
    per Group under the configured cap.  A Lab is attached — a network
    object served in-process (a Lab Example run with ``--enable-web``, or a
    test double) — or launched in a child process of this server through
    ``launcher``.  ``handle`` is what every service calls; a network object
    is wrapped on attachment, so nothing here reaches into a Node.

    The single-Lab accessors (``lab``, ``handle``, ``lab_key`` …) name the
    one Lab of Single-Lab Mode and are how a test attaches its double.
    """

    def __init__(self, launcher: Callable[..., Awaitable[Any]] = LabChild.launch) -> None:
        """Start with no Lab.

        Args:
            launcher: Starts a Lab Example in a process and answers with its
                ``LabChild``; tests hand in one that forks nothing.
        """
        self.launcher = launcher
        self._labs: Dict[str, RunningLab] = {}
        # Groups whose Lab is being launched: they hold a slot under the
        # cap, and their own one-Lab rule, before the process reports ready.
        self._starting: List[Optional[str]] = []
        # The same launches as Group and Lab Example, which removal waits for.
        self._starting_labs: List[Tuple[Optional[str], str]] = []
        self.lab_lock = asyncio.Lock()
        # Why Labs ended on their own, by key, newest last.
        self.exits: Dict[str, Dict[str, Any]] = {}
        # Awaited with (key, reason) when a Lab's process ends on its own.
        self.on_lab_exited: Optional[Callable[[str, str], Awaitable[None]]] = None
        # Handle calls run here rather than on the default executor, whose
        # threads Terminal readers hold in os.read: a Lab call must never
        # queue behind a quiet shell, and a wedged Lab must never stall the
        # event loop.
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lab-handle")

    @property
    def cap(self) -> int:
        """The most Labs alive at once.

        Single-Lab Mode is the manager with a cap of one; Multi-Lab Mode
        takes the setting.
        """
        return settings.lab_cap if settings.lab_mode == "multi" else 1

    # -- The table ----------------------------------------------------------

    def labs(self) -> List[RunningLab]:
        """Every Lab alive, oldest first."""
        return list(self._labs.values())

    def get(self, key: str) -> Optional[RunningLab]:
        """The Lab under ``key``, or None."""
        return self._labs.get(key)

    def lab_of(self, group: Optional[str]) -> Optional[RunningLab]:
        """The Lab a login sees as its own, or None.

        In Single-Lab Mode that is the one Lab whoever asks; in Multi-Lab
        Mode the Group's own.  The server itself — a group of None — sees
        the oldest Lab alive.
        """
        if settings.lab_mode != "multi" or group is None:
            labs = self.labs()
            return labs[0] if labs else None
        for lab in self._labs.values():
            if lab.group == group:
                return lab
        return None

    def visible(self, viewer: Optional[str], role: Optional[str]) -> List[RunningLab]:
        """The Labs a login may see; see :meth:`RunningLab.visible_to`."""
        return [lab for lab in self._labs.values() if lab.visible_to(viewer, role)]

    def last_exit_of(self, group: Optional[str]) -> Optional[Dict[str, Any]]:
        """Why a login's last Lab ended on its own, or None.

        In Single-Lab Mode the most recent exit whoever asks; in Multi-Lab
        Mode the Group's own.
        """
        for record in reversed(list(self.exits.values())):
            if settings.lab_mode != "multi" or record.get("group") == group:
                return dict(record)
        return None

    def forget_all(self) -> None:
        """Drop every record without stopping anything — for tests."""
        self._labs.clear()
        self._starting.clear()
        self._starting_labs.clear()
        self.exits.clear()

    # -- Single-Lab accessors -----------------------------------------------

    def _sole(self) -> Optional[RunningLab]:
        """The one Lab, or None; several alive is a Multi-Lab Mode caller's bug."""
        labs = self.labs()
        if len(labs) > 1:
            raise RuntimeError("several Labs are alive; address one by its key")
        return labs[0] if labs else None

    @property
    def lab(self) -> Any:
        """The one Lab as it was handed in, or None."""
        sole = self._sole()
        return sole.lab if sole is not None else None

    @lab.setter
    def lab(self, value: Any) -> None:
        """Attach ``value`` as the one Lab, or drop every record with None."""
        self.forget_all()
        if value is not None:
            self._attach(value, group=None)

    @property
    def handle(self) -> Optional[LabHandle]:
        """The one Lab's handle, or None when no Lab is running."""
        sole = self._sole()
        return sole.handle if sole is not None else None

    @property
    def lab_key(self) -> Optional[str]:
        """The one Lab's key, or None."""
        sole = self._sole()
        return sole.key if sole is not None else None

    @property
    def lab_example(self) -> Optional[str]:
        """The one Lab's example id, or None."""
        sole = self._sole()
        return sole.example if sole is not None else None

    @lab_example.setter
    def lab_example(self, value: Optional[str]) -> None:
        sole = self._sole()
        if sole is not None and value is not None:
            sole.example = value
            sole.handle.example = value

    @property
    def lab_started_at(self) -> Optional[float]:
        """When the one Lab started, or None."""
        sole = self._sole()
        return sole.started_at if sole is not None else None

    @property
    def last_exit(self) -> Optional[Dict[str, Any]]:
        """Why the most recent Lab to end on its own did, or None."""
        return self.last_exit_of(None)

    @last_exit.setter
    def last_exit(self, value: Optional[Dict[str, Any]]) -> None:
        self.exits.clear()
        if value is not None:
            self.exits[value["key"]] = dict(value)

    @property
    def runs_a_process(self) -> bool:
        """Whether any running Lab is a child process of this server."""
        return any(lab.child is not None for lab in self._labs.values())

    def refuse_key(
        self, key: str, viewer: Optional[str] = None, role: Optional[str] = None
    ) -> Optional[str]:
        """Say why ``key`` names no Lab the viewer may reach, or None if it may.

        A Lab another Group owns is refused exactly as a Lab that does not
        exist.  With no Lab visible and no record of this key, nothing is
        refused here: the caller answers "No lab running" as it always has.

        Args:
            key: A Lab key from a URL.
            viewer: The login asking, or None for the server.
            role: Its role.

        Returns:
            The refusal, naming the Lab where one is known, or None.
        """
        lab = self._labs.get(key)
        if lab is not None:
            return None if lab.visible_to(viewer, role) else f"No Lab with key '{key}' is running"
        exit_record = self.exits.get(key)
        if exit_record is not None and (
            settings.lab_mode != "multi" or role == "instructor" or exit_record.get("group") == viewer
        ):
            return (
                f"Lab '{exit_record.get('example')}' ({key}) is not running: "
                f"its process {exit_record.get('reason')}"
            )
        if self.visible(viewer, role):
            return f"No Lab with key '{key}' is running"
        return None

    # -- Starting -----------------------------------------------------------

    def _refuse_start(self, group: Optional[str]) -> Optional[Dict[str, Any]]:
        """Say why a start for ``group`` is refused now, or None if it may go.

        A Group's own Lab is named before the cap: that is the refusal the
        Group can act on.  The cap refuses Group and Instructor alike.
        """
        # A group of None is a Lab attached in-process, which belongs to no
        # Group and so has no "own Lab" to be refused for.
        if settings.lab_mode == "multi" and group is not None:
            own = self.lab_of(group)
            if own is not None:
                return {
                    "error": f"Group '{group}' already runs Lab '{own.example}' ({own.key}); "
                             "stop it first",
                    "running": True, "key": own.key, "example": own.example,
                }
            if group in self._starting:
                return {
                    "error": f"Group '{group}' is already starting a Lab",
                    "running": True, "example": None,
                }
        alive = len(self._labs) + len(self._starting)
        if alive >= self.cap:
            if settings.lab_mode != "multi":
                sole = self._sole()
                return {
                    "error": "Lab already running", "running": True,
                    "example": sole.example if sole is not None else None,
                }
            return {
                "error": f"Lab cap reached: {alive} of {self.cap} Labs running",
                "running": False, "cap": self.cap,
            }
        return None

    def _attach(
        self, lab_instance: Any, group: Optional[str], example: Optional[str] = None,
        child: Optional[LabChild] = None, preview: bool = False,
        preview_config_copy: Optional[str] = None,
    ) -> RunningLab:
        """Record a Lab as running and return its record."""
        handle = as_handle(lab_instance)
        if example is not None:
            handle.example = example
        lab = RunningLab(
            key=handle.key, example=handle.example, group=group,
            lab=lab_instance, handle=handle, started_at=time.time(), child=child,
            preview=preview, preview_config_copy=preview_config_copy,
        )
        self._labs[handle.key] = lab
        # The Group's older exits are history now: it has a Lab again.
        for key, record in list(self.exits.items()):
            if record.get("group") == group:
                del self.exits[key]
        return lab

    @staticmethod
    def _row(lab: RunningLab, status: str, nodes: int) -> Dict[str, Any]:
        return {
            "status": status, "key": lab.key, "group": lab.group,
            "example": lab.example, "started_at": lab.started_at, "nodes": nodes,
        }

    async def start_lab(
        self, lab_instance: Any, example_name: str, group: Optional[str] = None
    ) -> dict:
        """Attach a Lab that is already running in this process.

        Args:
            lab_instance: The started network.
            example_name: The Lab Example it was built from.
            group: The Group it belongs to, or None for everyone's.

        Returns:
            The Lab's status row, or an error when it may not start.
        """
        async with self.lab_lock:
            refusal = self._refuse_start(group)
            if refusal is not None:
                return refusal
            lab = self._attach(lab_instance, group, example=example_name)
            return self._row(lab, "running", lab.handle.status()["nodes"])

    async def start_lab_from_builder(
        self, example_id: str, lab_file: str, group: Optional[str] = None,
        config_copy: Optional[str] = None, clean: bool = False,
        timeout: Optional[float] = None, preview: bool = False,
        preview_config_copy: Optional[str] = None,
        structure: Optional[Dict[str, Any]] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Start a Lab Example in a child process of its own.

        The process builds the Lab and serves its handle; this service holds
        the RPC client and watches the process, so a Lab that dies is marked
        stopped, swept by its key and the reason recorded.

        Args:
            example_id: The catalog id of the Lab Example.
            lab_file: Its script's path.
            group: The Group the Lab is for, or None for everyone's.
            config_copy: The Group's copy directory for Persistent Config,
                or None to use each seed directly.
            clean: Discard the copy first, so the Lab starts from the seed.
                Done only once the start is not refused: a copy is never
                pulled from under the Group's running Lab.
            timeout: Optional startup timeout in seconds for builder execution.
            preview: Mark this as an Instructor Preview, hidden from Learners
                and cleaned up with its isolated configuration.
            preview_config_copy: The isolated configuration directory to
                remove when the Preview stops.
            structure: The published Router structure required to resume a
                Group's Config Copy; None for Labs without this contract.
            cwd: The process's working directory, for a Python-based Lab
                Example run from its package folder; None keeps the server's.

        Returns:
            The Lab's status row, or an error.
        """
        async with self.lab_lock:
            refusal = self._refuse_start(group)
            if refusal is not None:
                return refusal
            if (not clean and structure is not None and config_copy is not None
                    and os.path.isdir(config_copy)
                    and read_copy_structure(config_copy) != structure):
                return {"error": "This Group's Config Copy is structurally incompatible. "
                        "Use Start clean or ask an Instructor for Config Reset.",
                        "incompatible": True}
            if clean and config_copy is not None:
                discard_copy(config_copy)
            # A copy the build seeds now, for a Lab that never becomes one,
            # is not something the Group's next start should resume.
            fresh_copy = config_copy is not None and not os.path.isdir(config_copy)
            self._starting.append(group)
            self._starting_labs.append((group, example_id))

        if timeout is None:
            timeout = settings.lab_builder_timeout

        kwargs: Dict[str, Any] = {"config_copy": config_copy}
        if cwd is not None:
            kwargs["cwd"] = cwd
        try:
            import inspect
            sig = inspect.signature(self.launcher)
            if "timeout" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["timeout"] = timeout
        except (ValueError, TypeError):
            pass

        try:
            child = await self.launcher(example_id, lab_file, **kwargs)
        except (LabStartError, OSError) as e:
            logger.error(f"Failed to start lab '{example_id}': {e}")
            if fresh_copy and config_copy is not None:
                discard_copy(config_copy)
            return {"error": str(e), "running": False}
        finally:
            async with self.lab_lock:
                self._starting.remove(group)
                self._starting_labs.remove((group, example_id))

        async with self.lab_lock:
            # Opaque Python or Canvas code records its structure only once
            # its Lab makes a copy: the code may add persistent Routers.
            if (structure is not None and config_copy is not None
                    and (structure.get("routers")
                         or (("python" in structure or "code_section" in structure)
                             and os.path.isdir(config_copy)))):
                try:
                    record_copy_structure(config_copy, structure)
                except OSError as exc:
                    await child.stop()
                    if fresh_copy:
                        discard_copy(config_copy)
                    logger.error(f"Failed to record Config Copy structure for '{example_id}': {exc}")
                    return {"error": f"Could not save Config Copy structure: {exc}",
                            "running": False}
            lab = self._attach(
                child.handle, group, example=example_id, child=child, preview=preview,
                preview_config_copy=preview_config_copy,
            )
            lab.builder_file = lab_file
            lab.watcher = asyncio.create_task(self._watch(lab))
            row = self._row(lab, "started", child.handle.status()["nodes"])
            warnings = getattr(child, "startup_warnings", [])
            if warnings:
                row["warnings"] = list(warnings)
            return row

    async def _watch(self, lab: RunningLab) -> None:
        """Mark a Lab stopped when its process ends on its own.

        A stop this service asked for takes the Lab off the table first, so
        only an exit nobody asked for is handled here: the Lab is swept by
        its key, the reason recorded, and whoever registered
        ``on_lab_exited`` — the registry — told, so Terminals close and the
        browsers hear.

        Args:
            lab: The Lab whose process is watched.
        """
        child = lab.child
        assert child is not None
        reason = await child.wait()
        async with self.lab_lock:
            if self._labs.get(lab.key) is not lab:
                return
            logger.warning(f"Lab '{lab.example}' ({lab.key}) {reason}; marking it stopped")
            await child.sweep()
            if lab.preview_config_copy is not None:
                discard_copy(lab.preview_config_copy)
            self.exits[lab.key] = {
                "key": lab.key, "example": lab.example, "group": lab.group,
                "reason": reason, "at": time.time(),
            }
            while len(self.exits) > EXIT_HISTORY:
                del self.exits[next(iter(self.exits))]
            del self._labs[lab.key]
        await self._close_viewers(lab, f"{LAB_STOPPED_REASON} ({reason})")
        if self.on_lab_exited is not None:
            await self.on_lab_exited(lab.key, reason)

    @staticmethod
    async def _close_viewers(lab: RunningLab, reason: str) -> None:
        """Tell a stopped Lab's prompt viewers and feed subscribers, and close them."""
        await lab.prompt.close_all(reason)
        await lab.feed.close_all(reason)

    # -- Stopping -----------------------------------------------------------

    async def stop_lab(self, key: str, terminal_service: "TerminalService") -> dict:
        """Stop one Lab, closing every Terminal it was serving.

        Terminals are closed first: each one holds a shell inside a node
        namespace, so tearing the Lab down under them would leak the child
        processes the registry is the only record of.

        Args:
            key: The Lab's key.
            terminal_service: The service owning the Terminals to close.

        Returns:
            The stopped Lab's example name, or an error when none was running.
        """
        async with self.lab_lock:
            # Taken off the table before the stop so the watcher, which wakes
            # when the process ends, sees a stop that was asked for.
            lab = self._labs.pop(key, None)
            if lab is None:
                return {"error": "No lab running", "running": False}

            await terminal_service.close_lab_terminals(key)

            logger.info(f"Stopping lab '{lab.example}' ({key})")
            if lab.child is not None:
                await lab.child.stop()
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lab.handle.stop)
            if lab.preview_config_copy is not None:
                discard_copy(lab.preview_config_copy)

            logger.info(f"All Terminals of {key} cleared")
        await self._close_viewers(lab, LAB_STOPPED_REASON)
        return {"status": "stopped", "key": key, "group": lab.group, "example": lab.example}

    # -- Reading ------------------------------------------------------------

    async def status_of(self, lab: RunningLab) -> Dict[str, Any]:
        """One Lab's status row, its node count read over the handle."""
        loop = asyncio.get_running_loop()
        lab_status = await loop.run_in_executor(self.executor, lab.handle.status)
        status = {
            "running": True,
            "key": lab.key,
            "group": lab.group,
            "example": lab.example,
            "started_at": lab.started_at,
            "uptime": int(time.time() - lab.started_at),
            "example_name": resolve_lab_name(lab.example),
            "has_guide": GuideRepository.available(lab.example, builder_file=lab.builder_file),
            "nodes": lab_status["nodes"],
            "active_terminals": 0,  # populated by SessionRegistry
            "link_condition_drift": lab_status["link_condition_drift"],
        }
        if lab.preview:
            status["preview"] = True
        return status

    async def get_status(self, group: Optional[str] = None, role: Optional[str] = None) -> dict:
        """The status of a login's own Lab, or that none is running and why.

        Args:
            group: The login asking; None reads the one Lab of Single-Lab Mode.
            role: The login's role, used to hide Instructor Previews from
                Learners.
        """
        async with self.lab_lock:
            lab = self.lab_of(group)
            if lab is not None and lab.preview and role == "learner":
                lab = None
            if lab is None:
                status: Dict[str, Any] = {"running": False}
                last_exit = self.last_exit_of(group)
                if last_exit is not None:
                    status["last_exit"] = last_exit
                return status
            return await self.status_of(lab)

    def refuse_reset(self, group: str, example: str) -> Optional[str]:
        """Say why a Group's copy of a Lab Example may not be reset now, or None.

        A reset never pulls files from under a running Router: the Group's
        Lab on that Lab Example, alive or still starting, refuses it naming
        the Lab.  A Lab on another Lab Example does not use the copy.

        Args:
            group: The Group whose copy would be reset.
            example: The Lab Example the copy is of.

        Returns:
            The refusal, or None when the reset may go ahead.
        """
        own = self.lab_of(group)
        if own is not None and own.example == example:
            return f"Group '{group}' runs Lab '{example}' ({own.key}); stop it first"
        if group in self._starting:
            return f"Group '{group}' is starting a Lab; wait for it"
        return None

    def refuse_removal(self, example: str) -> Optional[str]:
        """Say why a Lab Example may not leave the Catalog now, or None.

        Any Lab of it, whoever runs or is starting it, holds its builder and
        Config Copy.

        Args:
            example: The Lab Example to remove.

        Returns:
            The refusal, or None when the removal may go ahead.
        """
        for lab in self._labs.values():
            if lab.example == example:
                owner = f"Group '{lab.group}'" if lab.group else "The server"
                return f"{owner} runs a Lab of it ({lab.key}); stop it first"
        for group, starting in self._starting_labs:
            if starting == example:
                owner = f"Group '{group}'" if group else "The server"
                return f"{owner} is starting a Lab of it; wait for it"
        return None


class TerminalEnded(Exception):
    """A page named a Terminal it cannot have back; the message says why."""


class TerminalService:
    """Manages the live Terminals and the budgets that bound them."""

    def __init__(self, spawner: Optional[TerminalSpawner] = None) -> None:
        """Initialize an empty registry.

        Args:
            spawner: What attaches a shell to a Node.  Defaults to the pty(7)
                spawner; tests hand in one that never touches a namespace.
        """
        self.terminals: Dict[str, Terminal] = {}
        self.session_terminals: Dict[str, Set[str]] = defaultdict(set)
        self.terminals_lock = asyncio.Lock()
        self._spawner = spawner if spawner is not None else TerminalSpawner()
        # Ended Terminals by id: the owning Session and why it ended.  Bounded,
        # oldest first out; only a reattach reads it.
        self._ended: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()

    async def spawn_terminal(
        self, lab: Optional[LabHandle], client_id: str, node_name: str, username: str
    ) -> Terminal:
        """Open one Terminal on a node, if both budgets allow it.

        The per-Session cap is checked before the host-wide ceiling, so a
        Learner already at their own limit is told that rather than that the
        host is full; see the comment on the second check.

        Args:
            lab: The handle of the Lab the Node is in.
            client_id: The owning Session's token.
            node_name: The node to attach the shell to.
            username: The Learner the Terminal is opened for.

        Returns:
            The Terminal, already registered against its Session.

        Raises:
            ValueError: If either budget is spent, no Lab is running, or the
                node does not exist or has no PID.
        """
        # A Session at its cap gives up its oldest detached Terminal for the
        # new one: a tab closed for good cannot get it back, and a new tab
        # should not wait out the grace window.  Attached Terminals are never
        # given up, and nothing is given up unless the new Terminal opens.
        evicted: Optional[Terminal] = None
        async with self.terminals_lock:
            owned = [self.terminals[i] for i in self.session_terminals.get(client_id, ())]
            session_terminal_count = len(owned)
            victim: Optional[Terminal] = None
            if session_terminal_count >= settings.terminal_per_session_cap:
                detached = [t for t in owned if t.detached_at is not None]
                if not detached:
                    raise ValueError(
                        f"Terminal cap exceeded: {session_terminal_count}"
                        f"/{settings.terminal_per_session_cap}"
                    )
                victim = min(detached, key=lambda t: t.detached_at or 0.0)

            # Checked second, after the per-Session cap: a Learner already at
            # their own cap gets the more actionable message even when the
            # host also happens to be full.  Someone still under their own
            # cap gets this one instead, since nothing about their own usage
            # explains the refusal.  A Terminal given up above frees its place.
            host_terminal_count = len(self.terminals) - (victim is not None)
            if host_terminal_count >= settings.terminal_global_cap:
                raise ValueError(
                    f"host terminal limit reached: {host_terminal_count}"
                    f"/{settings.terminal_global_cap}"
                )

            if lab is None:
                raise ValueError("No lab running")

            attachment = lab.node_attachment(node_name)

            try:
                child_pid, terminal_fd = self._spawner.spawn(attachment)
            except OSError as e:
                # The spawner could not reach the node's namespaces — the
                # Node is gone or going.  Surface it like any other spawn
                # refusal so the browser shows the reason rather than a dead
                # socket.
                raise ValueError(f"Cannot open a Terminal on {node_name}: {e}")

            terminal_id = str(uuid.uuid4())
            now = time.time()
            terminal = Terminal(
                id=terminal_id,
                node=node_name,
                client_id=client_id,
                username=username,
                lab_key=lab.key,
                terminal_fd=terminal_fd,
                child_pid=child_pid,
                created_at=now,
                last_activity=now
            )

            if victim is not None:
                evicted = self._take_off_books(victim.id, EVICTED_REASON)
            self.terminals[terminal_id] = terminal
            self.session_terminals[client_id].add(terminal_id)
            logger.debug(
                f"Terminal spawned: {terminal_id} node={node_name} client={client_id}"
            )

        # Reaped outside the lock, as close_terminal does.
        if evicted is not None:
            await self._end_shell(evicted)
        return terminal

    async def close_terminal(self, terminal_id: str, reason: str = CLOSED_REASON) -> None:
        """Close one Terminal, releasing its budget before reaping its shell.

        The books are settled under the lock and the shell is reaped outside
        it.  Reaping costs :data:`REAP_GRACE_PERIOD` seconds — an interactive
        bash ignores SIGTERM, so the wait before SIGKILL is always paid in
        full — and doing that under the lock serialised every close behind
        every other one while blocking every spawn on the same lock.  A Learner
        who reloaded a full set of Terminals therefore waited seconds for
        Terminals the server had already given up on.

        Args:
            terminal_id: The Terminal to close.
            reason: Why it ended, told to a page that later tries to reattach it.
        """
        async with self.terminals_lock:
            terminal = self._take_off_books(terminal_id, reason)
        if terminal is not None:
            await self._end_shell(terminal)

    def _take_off_books(self, terminal_id: str, reason: str) -> Optional[Terminal]:
        """Release a Terminal's place in both budgets; call under ``terminals_lock``.

        Popped rather than read: two closes can race for one Terminal — the
        grace window running out and its Session being destroyed — and only
        the one that takes it off the books may reap it.

        Args:
            terminal_id: The Terminal to release.
            reason: Why it ended, told to a page that later tries to reattach it.

        Returns:
            The Terminal, for the caller to end with :meth:`_end_shell`, or None
            when another close already took it.
        """
        terminal = self.terminals.pop(terminal_id, None)
        if terminal is None:
            return None
        self._ended[terminal_id] = (terminal.client_id, reason)
        while len(self._ended) > ENDED_HISTORY:
            self._ended.popitem(last=False)

        owned = self.session_terminals.get(terminal.client_id)
        if owned is not None:
            owned.discard(terminal_id)
            if not owned:
                del self.session_terminals[terminal.client_id]
        return terminal

    async def _end_shell(self, terminal: Terminal) -> None:
        """Stop a Terminal's tasks and reap its shell; call outside the lock.

        Args:
            terminal: A Terminal already taken off the books.
        """
        await self._cancel_terminal_tasks(terminal)
        await self._reap_child(terminal.child_pid)

        terminal.input.close()
        try:
            os.close(terminal.terminal_fd)
        except OSError:
            pass

        logger.debug(f"Terminal closed: {terminal.id} node={terminal.node}")

    @staticmethod
    async def _cancel_terminal_tasks(terminal: Terminal) -> None:
        """Stop the background tasks serving one Terminal.

        Args:
            terminal: The Terminal whose tasks are being stopped.
        """
        current_task = asyncio.current_task()
        for task in (
            terminal.read_task, terminal.ping_task, terminal.timeout_task, terminal.grace_task
        ):
            # A Node-exit reader closes its own Terminal after the terminal fd reaches
            # EOF.  It is already on the way out, and awaiting the current
            # task would raise "Task cannot await on itself" while cancelling
            # it leaves a pending cancellation on the completed reader.
            if task is current_task:
                continue
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, RuntimeError):
                    pass

    @staticmethod
    async def _reap_child(child_pid: int) -> None:
        """End a Terminal's shell, politely first and then not.

        Its own method so that the wait it contains is visibly outside the
        registry lock, and so a test can observe what the books look like while
        it runs.

        Args:
            child_pid: PID of the shell to end.
        """
        try:
            os.kill(child_pid, signal.SIGTERM)
            await asyncio.sleep(REAP_GRACE_PERIOD)
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def close_session_terminals(self, session_token: str) -> int:
        """Close every Terminal owned by one Session.

        Called when a Session is destroyed.  ``session_terminals`` is keyed on
        the Session token, so without this a destroyed Session's Terminals stay
        counted against a Session nobody can present and reachable by nobody —
        a leaked shell per Terminal.

        The browser connection is closed first, with a reason, so the Learner's
        Terminal says why it ended instead of going silent on a dead socket.

        Args:
            session_token: The Session whose Terminals are being closed.

        Returns:
            The number of Terminals successfully closed, which is fewer than
            the Session owned if any refused to close.
        """
        async with self.terminals_lock:
            # .get() rather than indexing: session_terminals is a defaultdict,
            # and a Session being destroyed must not gain an entry here.
            terminal_ids = list(self.session_terminals.get(session_token, ()))

        closed = 0
        for terminal_id in terminal_ids:
            # Guarded per Terminal: one that fails to close must not strand the
            # rest, which would leak exactly the shells this method exists to
            # reclaim.
            try:
                await self._close_browser_connection(terminal_id)
                await self.close_terminal(terminal_id)
                closed += 1
            except (OSError, RuntimeError, ValueError) as e:
                logger.error(f"Failed to close Terminal {terminal_id}: {e}")

        return closed

    async def close_lab_terminals(self, key: str, reason: Optional[str] = None) -> None:
        """Close every Terminal on one Lab, telling each browser why if asked.

        Shells share one grace period: awaiting each reap in sequence makes
        the Instructor's Stop take half a second per connected Terminal.

        Args:
            key: The Lab's key.
            reason: The close reason for the browsers, or None to close the
                Terminals without a word — the Lab is being stopped on
                purpose and the page already knows.
        """
        async with self.terminals_lock:
            terminal_ids = [t.id for t in self.terminals.values() if t.lab_key == key]
        if reason is not None:
            await asyncio.gather(
                *(self._close_browser_connection(terminal_id, reason)
                  for terminal_id in terminal_ids)
            )
        await asyncio.gather(
            *(self.close_terminal(terminal_id, reason or LAB_STOPPED_REASON)
              for terminal_id in terminal_ids)
        )

    def terminals_on(self, key: str) -> int:
        """How many Terminals are open on one Lab.

        Args:
            key: The Lab's key.
        """
        return sum(1 for t in self.terminals.values() if t.lab_key == key)

    async def _close_browser_connection(
        self, terminal_id: str, reason: str = SESSION_ENDED_REASON
    ) -> None:
        """Close a Terminal's WebSocket, telling the browser why.

        Mirrors the idle-timeout close in the WebSocket handler: a normal
        closure carrying a reason string.  A connection the browser has already
        dropped raises here, which is not a failure worth propagating.

        Args:
            terminal_id: The Terminal whose connection is being closed.
            reason: Why the Terminal ended — "Session ended" when the Session
                was destroyed, "Node exited" when the shell behind the
                Terminal died.  The browser shows it so a Learner can tell
                which end went away.
        """
        terminal = self.terminals.get(terminal_id)
        websocket = terminal.websocket if terminal else None
        if websocket is None:
            return

        try:
            await websocket.close(
                code=status.WS_1000_NORMAL_CLOSURE,
                reason=reason,
            )
        except (OSError, RuntimeError) as e:
            logger.debug(f"Terminal {terminal_id} was already disconnected: {e}")

    async def close_terminal_with_reason(self, terminal_id: str, reason: str) -> None:
        """Close a Terminal whose shell has exited, telling the browser why.

        Reads as the reverse of a Session destroying its Terminals: here the
        Node end went away rather than the browser end.  Both the browser
        close and the Terminal close are idempotent, so racing the WebSocket
        handler's own cleanup is safe.

        Args:
            terminal_id: The Terminal whose shell exited.
            reason: The reason string sent to the browser.
        """
        await self._close_browser_connection(terminal_id, reason)
        await self.close_terminal(terminal_id, reason)

    async def update_activity(self, terminal_id: str) -> None:
        """Mark a Terminal as used just now, deferring its idle timeout.

        Silent when the Terminal is already gone: activity can arrive from a
        read that raced the close that removed it.

        Args:
            terminal_id: The Terminal reporting activity.
        """
        terminal = self.terminals.get(terminal_id)
        if terminal:
            terminal.last_activity = time.time()

    def terminal_count(self, client_id: str) -> int:
        """Return how many Terminals one Session currently holds.

        Read-only, unlike :meth:`close_session_terminals`: the Session Cap
        uses this to judge whether a Session is in use, not to reclaim it.

        Args:
            client_id: The Session token.

        Returns:
            The number of live Terminals owned by that Session.
        """
        return len(self.session_terminals.get(client_id, ()))

    async def detach(
        self,
        terminal_id: str,
        websocket: Any,
        on_expired: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Leave a Terminal running without its connection, for the grace window.

        Called when a Terminal's connection closes without a close on purpose:
        a reload, a closed tab, a dropped network.  The shell keeps running
        and its output keeps being kept; a page that reattaches within the
        Terminal idle timeout gets it back, and otherwise it ends.  A
        connection that no longer holds the Terminal — it ended, or another
        took it — detaches nothing.

        Args:
            terminal_id: The Terminal whose connection closed.
            websocket: The connection that closed.
            on_expired: Awaited once the grace window has ended the Terminal,
                so that whoever shows Terminals can refresh.
        """
        async with self.terminals_lock:
            terminal = self.terminals.get(terminal_id)
            if terminal is None or terminal.websocket is not websocket:
                return
            terminal.websocket = None
            terminal.detached_at = time.time()
            connection_tasks = (terminal.ping_task, terminal.timeout_task)
            terminal.ping_task = terminal.timeout_task = None
            terminal.grace_task = asyncio.create_task(
                self._end_unless_reattached(terminal_id, on_expired)
            )
        for task in connection_tasks:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, RuntimeError):
                    pass
        logger.debug(f"Terminal detached: {terminal_id} node={terminal.node}")

    async def _end_unless_reattached(
        self,
        terminal_id: str,
        on_expired: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """End a detached Terminal once its grace window has passed.

        Args:
            terminal_id: The detached Terminal.
            on_expired: Awaited after the Terminal has ended.
        """
        await asyncio.sleep(settings.terminal_idle_timeout)
        await self.close_terminal_with_reason(terminal_id, DETACHED_EXPIRED_REASON)
        if on_expired is not None:
            await on_expired()

    async def reattach(
        self,
        terminal_id: str,
        client_id: str,
        key: str,
        node_name: str,
        websocket: Any,
        greet: Callable[[str], Awaitable[None]],
    ) -> Terminal:
        """Give a detached Terminal back to the page that had it.

        Only the Session that opened the Terminal, asking for it in the same
        Lab and on the same Node, gets it; anyone else is told it ended, so a
        Terminal id reveals nothing about another Session's shells.  One still
        attached to another connection is taken over: that connection is told
        the Terminal moved to another tab and is closed.  The
        recent output is handed to ``greet`` before any live output can reach
        the connection, so the page sees the replay first and nothing twice.

        Args:
            terminal_id: The Terminal the page names.
            client_id: The asking Session's token.
            key: The Lab key the page connected under.
            node_name: The Node the page asks for.
            websocket: The page's new connection.
            greet: Sends the page the replay; awaited before live output flows.

        Returns:
            The reattached Terminal.

        Raises:
            TerminalEnded: If the Terminal cannot be reattached, with the reason
                when it is known.
        """
        async with self.terminals_lock:
            terminal = self.terminals.get(terminal_id)
            if (
                terminal is None
                or terminal.client_id != client_id
                or terminal.lab_key != key
                or terminal.node != node_name
            ):
                owner, reason = self._ended.get(terminal_id, ("", UNKNOWN_TERMINAL_REASON))
                if terminal is not None or owner != client_id:
                    reason = UNKNOWN_TERMINAL_REASON
                raise TerminalEnded(reason)
            # Taken under the registry lock so no read lands between the claim
            # and the replay; a detached reader never awaits while holding it.
            await terminal.send_lock.acquire()
            # Still attached elsewhere: a duplicated tab, or the later of two
            # racing for it.  The lock has decided; the newer connection takes
            # it and the older one, with its keepalive, is let go.
            previous = terminal.websocket
            connection_tasks = (terminal.ping_task, terminal.timeout_task)
            terminal.ping_task = terminal.timeout_task = None
            if terminal.grace_task is not None:
                terminal.grace_task.cancel()
                terminal.grace_task = None
            terminal.detached_at = None
            terminal.websocket = websocket
        try:
            await greet(terminal.replay())
        finally:
            terminal.send_lock.release()
        for task in connection_tasks:
            if task is not None and not task.done():
                task.cancel()
        if previous is not None:
            await self._tell_moved(previous)
        logger.debug(f"Terminal reattached: {terminal_id} node={terminal.node}")
        return terminal

    @staticmethod
    async def _tell_moved(websocket: Any) -> None:
        """Tell a connection its Terminal moved to another tab, and close it.

        Args:
            websocket: The connection the Terminal was taken from.
        """
        try:
            await websocket.send_json({"type": "moved", "reason": MOVED_REASON})
            await websocket.close(code=MOVED_CLOSE_CODE, reason=MOVED_REASON)
        except (OSError, RuntimeError) as e:
            logger.debug(f"A taken-over Terminal connection was already gone: {e}")

    def get_terminals(self) -> List[dict]:
        """Describe every live Terminal, for the Instructor's Terminal list.

        Uptime and idle are reported as whole seconds elapsed rather than as
        timestamps, so the caller needs no clock of its own to render them.

        Returns:
            One row per Terminal: id, Lab key, node, owning Session,
            username, uptime, idle time, whether a page is attached, and for a
            detached Terminal the whole seconds left in its grace window
            (None while attached).
        """
        now = time.time()
        terminals = []
        for terminal in self.terminals.values():
            detached_at = terminal.detached_at
            grace_left = None
            if detached_at is not None:
                remaining = settings.terminal_idle_timeout - (now - detached_at)
                grace_left = max(0, math.ceil(remaining))
            terminals.append({
                "id": terminal.id,
                "lab": terminal.lab_key,
                "node": terminal.node,
                "client": terminal.client_id,
                "username": terminal.username,
                "uptime": int(now - terminal.created_at),
                "idle": int(now - terminal.last_activity),
                "attached": detached_at is None,
                "grace_left": grace_left,
            })
        return terminals
