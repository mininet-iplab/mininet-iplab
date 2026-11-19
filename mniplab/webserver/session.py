"""Session registry for managing the Labs' lifecycle and Terminals.

SessionRegistry is a thin facade that composes focused services:
- LabLifecycleService, the manager's table of Labs
- TerminalService
- per Lab: a PromptService and a TopologyBroadcastService
- the Groups and their Assignments
- the CaptureService, whose Packet Captures end with their Lab
"""

import asyncio
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from fastapi import WebSocket, WebSocketDisconnect

from .services import (
    LAB_STOPPED_REASON,
    LabLifecycleService,
    RunningLab,
    Terminal,
    TerminalService,
)
from .broadcast import TopologyBroadcastService
from .capture import (
    CaptureService,
    CaptureSubscriber,
    capture_node_error,
    capture_target_error,
    is_switch,
)
from .capture_spawner import build_capture_command, ring_files
from .network_tool import build_bandwidth_argv, build_tool_argv
from .config import settings
from .groups import Groups
from .server_settings import SettingsStore
from .prompt import PromptService
from .nat_table import NATTableReader, parse_nat_rules, parse_nat_table
from mniplab.dns.control_client import KnotControlClient
from mniplab.lab_path import lab_dir_for
from mniplab.labhandle import LabHandle, NodeAttachment
from mniplab.persistent_config import ConfigCopies
from mniplab.resolver.models import AccessRule
from mniplab.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class SessionRegistry:
    """Manages every running Lab and every live Terminal.

    This class is the public facade used by the FastAPI layer.  It delegates
    all behaviour to focused services so that each concern can be tested
    independently.  Every Lab is addressed by its key: the URL is the
    binding, and nothing here has a "current Lab".
    """

    def __init__(self):
        self.lab_service = LabLifecycleService()
        self.lab_service.on_lab_exited = self._on_lab_exited
        # The Groups that can log in and what each may start.
        self.groups = Groups()
        # The Limits changed in Server settings; opened only under `serve`,
        # so a Lab script's server has none and offers no Server settings.
        self.settings_store: Optional[SettingsStore] = None
        # Every Group's copies of Persistent Config, by Group and Lab Example.
        self.config_copies = ConfigCopies()
        self.terminal_service = TerminalService()
        # Every Packet Capture, shared by whoever watches its Interface.
        self.capture_service = CaptureService()
        # Reads a translating Router's connection tracking table.
        self.nat_table_reader = NATTableReader()
        # The manager's feed: which Lab is alive, for every signed-in
        # browser, each told about the Lab it may see.
        self._status_subscribers: Dict[WebSocket, Tuple[Optional[str], Optional[str]]] = {}
        # Made on first use, per event loop: a Lock binds to the loop that
        # first waits on it, and one registry outlives a server's loop.
        self._status_broadcast_lock: Optional[asyncio.Lock] = None
        self._status_broadcast_lock_loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def handle(self) -> Optional[LabHandle]:
        """The one Lab's handle, or None."""
        return self.lab_service.handle

    @property
    def lab_key(self) -> Optional[str]:
        """The one Lab's key, or None."""
        return self.lab_service.lab_key

    def refuse_key(
        self, key: str, viewer: Optional[str] = None, role: Optional[str] = None
    ) -> Optional[str]:
        """Say why a Lab key from a URL names no Lab the viewer may reach, or None.

        The URL is the binding: a key that is not a visible alive Lab's is
        refused, and the key of a Lab whose process ended on its own is
        refused with the reason its process gave, so a stale page learns
        what happened to its Lab rather than seeing another one.  With no
        Lab visible and no record of this key, nothing is refused here: the
        registry answers "No lab running" as it always has.

        Args:
            key: The Lab key from the path.
            viewer: The login asking, or None for the server.
            role: Its role.

        Returns:
            The refusal, or None when the key may go through.
        """
        return self.lab_service.refuse_key(key, viewer, role)

    @property
    def lab_cap(self) -> int:
        """The most Labs alive at once: one in Single-Lab Mode, the setting in Multi."""
        return self.lab_service.cap

    @property
    def lab_example(self):
        return self.lab_service.lab_example

    @property
    def lab_started_at(self):
        return self.lab_service.lab_started_at

    @property
    def lab_lock(self):
        return self.lab_service.lab_lock

    def lab(self, key: str) -> RunningLab:
        """The running Lab under ``key``.

        Raises:
            ValueError: No Lab is running under it.
        """
        lab = self.lab_service.get(key)
        if lab is None:
            raise ValueError("No lab running")
        return lab

    def prompt(self, key: str) -> PromptService:
        """The Lab Prompt of the Lab under ``key``."""
        return self.lab(key).prompt

    def feed(self, key: str) -> TopologyBroadcastService:
        """The Topology and Service feed of the Lab under ``key``."""
        return self.lab(key).feed

    async def start_lab(
        self,
        lab_instance: Any,
        example_name: str,
        prompt_fd: Optional[int] = None,
        local_fd: Optional[int] = None,
        prompt_interrupt: Optional[Callable[[], None]] = None,
        group: Optional[str] = None,
    ) -> dict:
        """Attach a Lab running in this process, and its Lab Prompt if it has one."""
        KnotControlClient.clear_baselines()
        result = await self.lab_service.start_lab(lab_instance, example_name, group)
        if "error" not in result and prompt_fd is not None:
            self.prompt(result["key"]).attach(prompt_fd, local_fd, prompt_interrupt)
        return result

    async def start_lab_from_builder(
        self, example_id: str, lab_file: str, group: Optional[str] = None, clean: bool = False,
        timeout: Optional[float] = None, preview: bool = False,
        config_copy: Optional[str] = None,
        structure: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Start a Lab Example in a process of its own, for a Group.

        In Multi-Lab Mode the process is handed the Group's copy directory
        for the Lab Example, so a Router with Persistent Config seeds or
        resumes the Group's copy rather than writing the seed; ``clean``
        discards that copy first ("start clean"). A Preview always receives
        a fresh caller-supplied configuration directory and never a Group copy.

        Args:
            example_id: The Catalog id of the Lab Example.
            lab_file: The builder script's path.
            group: The Group that owns the Lab, or None for the shared Lab.
            clean: Discard the Group's saved copy before starting.
            timeout: Optional builder startup timeout in seconds.
            preview: Mark this as an Instructor Preview.
            config_copy: The isolated configuration directory for a Preview,
                or the Group copy for an ordinary Lab.
            structure: The published structure a Group's copy must match.
            cwd: The working directory of a Python-based Lab Example's
                process, its package folder; None keeps the server's.
        """
        KnotControlClient.clear_baselines()
        if preview and config_copy is None:
            raise ValueError("A Preview Lab needs its own isolated configuration directory")
        if config_copy is None and settings.lab_mode == "multi" and group is not None and not preview:
            config_copy = self.config_copies.copy_dir(group, example_id)
        result = await self.lab_service.start_lab_from_builder(
            example_id, lab_file, group, config_copy=config_copy, clean=clean,
            timeout=timeout, preview=preview,
            preview_config_copy=config_copy if preview else None,
            structure=structure, cwd=cwd,
        )
        if "error" not in result:
            lab = self.lab(result["key"])
            if lab.child is not None and lab.child.prompt_fd is not None:
                lab.prompt.attach(lab.child.prompt_fd)
        return result

    async def stop_lab(self, key: str) -> dict:
        """Stop one Lab, its Captures, its Terminals, its prompt and its feed."""
        # Captures go first: each is a process inside one of the Lab's Nodes
        # writing into its Lab directory, both of which the stop removes.
        await self.capture_service.stop_lab_captures(key, LAB_STOPPED_REASON)
        try:
            result = await self.lab_service.stop_lab(key, self.terminal_service)
        finally:
            KnotControlClient.clear_baselines()
        return result

    async def stop_all(self) -> List[dict]:
        """Stop every Lab this server launched, and every Capture, as its shutdown does."""
        # Every Capture, not only those of the Labs stopped below: a Lab
        # attached in-process is stopped by its own script, and without a
        # Private Namespace its Nodes' processes would outlive it.
        await self.capture_service.stop_all(LAB_STOPPED_REASON)
        results = []
        for lab in self.lab_service.labs():
            if lab.child is not None:
                results.append(await self.stop_lab(lab.key))
        return results

    async def _on_lab_exited(self, key: str, reason: str) -> None:
        """Close every Terminal of a Lab whose process ended, and tell the browsers.

        Args:
            key: The Lab's key.
            reason: Why its process ended.
        """
        await self.capture_service.stop_lab_captures(key, f"{LAB_STOPPED_REASON} ({reason})")
        await self.terminal_service.close_lab_terminals(key, f"{LAB_STOPPED_REASON} ({reason})")
        logger.info(f"Lab {key} ended: {reason}")
        await self.broadcast_status_update()

    async def get_status(self, group: Optional[str] = None, role: Optional[str] = None) -> dict:
        """The status of a login's own Lab; see ``LabLifecycleService.get_status``."""
        status = await self.lab_service.get_status(group, role)
        if status.get("running"):
            status["active_terminals"] = self.terminal_service.terminals_on(status["key"])
        return status

    async def status_of(self, lab: RunningLab) -> dict:
        """One running Lab's status row, its Terminals counted."""
        status = await self.lab_service.status_of(lab)
        status["active_terminals"] = self.terminal_service.terminals_on(lab.key)
        return status

    async def broadcast_topology_update(self, key: str) -> None:
        """Send a Lab's Topology to every browser on its feed.

        Args:
            key: The Lab's key.
        """
        topology = await self.get_topology(key)
        await self.feed(key).broadcast({"type": "topology_update", "data": topology})

    async def broadcast_status_update(self) -> None:
        """Tell every browser on the Labs feed about the Lab it may see."""
        async with self._broadcast_lock():
            # Inspect each audience once per update; later updates stay fresh.
            statuses: Dict[Tuple[Optional[str], Optional[str]], dict] = {}
            for websocket, (viewer, role) in list(self._status_subscribers.items()):
                audience = (viewer if settings.lab_mode == "multi" else None, role)
                if audience not in statuses:
                    statuses[audience] = await self.get_status(*audience)
                try:
                    await websocket.send_json(
                        {"type": "status_update", "data": statuses[audience]}
                    )
                except (
                    OSError, RuntimeError, ValueError, TypeError, WebSocketDisconnect
                ) as e:
                    logger.debug(f"Failed to send status to subscriber: {e}")
                    self._status_subscribers.pop(websocket, None)

    async def add_topology_subscriber(self, key: str, websocket: WebSocket) -> None:
        """Subscribe a browser to a Lab's feed.

        Args:
            key: The Lab's key.
            websocket: The browser connection.

        Raises:
            ValueError: No Lab is running under ``key``.
        """
        await self.feed(key).add_subscriber(websocket)

    async def remove_topology_subscriber(self, key: str, websocket: WebSocket) -> None:
        """Unsubscribe a browser from a Lab's feed; nothing to do once the Lab is gone.

        Args:
            key: The Lab's key.
            websocket: The browser connection.
        """
        lab = self.lab_service.get(key)
        if lab is not None:
            await lab.feed.remove_subscriber(websocket)

    async def add_status_subscriber(
        self, websocket: WebSocket, viewer: Optional[str] = None, role: Optional[str] = None,
        *, send_initial: bool = False,
    ) -> None:
        """Subscribe a browser to the Labs feed, as the login it belongs to.

        Args:
            websocket: The browser connection.
            viewer: The login behind it, or None for the server.
            role: Its role.
            send_initial: Send the first status before later broadcasts may reach it.
        """
        async with self._broadcast_lock():
            self._status_subscribers[websocket] = (viewer, role)
            if send_initial:
                status = await self.get_status(viewer, role)
                await websocket.send_json({"type": "status_update", "data": status})

    async def remove_status_subscriber(self, websocket: WebSocket) -> None:
        """Unsubscribe a browser from the Labs feed.

        Args:
            websocket: The browser connection.
        """
        self._status_subscribers.pop(websocket, None)

    async def get_topology(self, key: str) -> dict:
        """The Topology payload of one Lab, or an error when none runs under ``key``.

        Args:
            key: The Lab's key.
        """
        lab = self.lab_service.get(key)
        if lab is None:
            return {"error": "No lab running"}
        return await self._run(key, lambda h: h.topology())

    async def _run(
        self,
        key: str,
        fn: Callable[[LabHandle], T],
        *,
        broadcast_topology: bool = False,
    ) -> T:
        """Run one blocking Lab handle operation through the shared executor seam.

        Args:
            key: The Lab's key.
            fn: Callable receiving the Lab's :class:`LabHandle` and returning the result.
            broadcast_topology: Whether to broadcast the Topology afterwards.

        Returns:
            The operation's result.

        Raises:
            ValueError: No Lab is running under ``key``.
        """
        handle = self.lab(key).handle
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self.lab_service.executor,
            fn,
            handle,
        )
        if broadcast_topology:
            await self.broadcast_topology_update(key)
        return result

    async def spawn_terminal(
        self, key: str, client_id: str, node_name: str, username: str
    ) -> Terminal:
        """Open one Terminal on a node of a running Lab.

        Args:
            key: The Lab's key.
            client_id: The owning Session's token.
            node_name: The node to attach the shell to.
            username: The Learner the Terminal is opened for.

        Returns:
            The Terminal, already registered against its Session.

        Raises:
            ValueError: If either Terminal budget is spent, or no Lab is
                running, or the node does not exist.
        """
        lab = self.lab_service.get(key)
        return await self.terminal_service.spawn_terminal(
            lab.handle if lab is not None else None, client_id, node_name, username
        )

    async def close_terminal(self, terminal_id: str) -> None:
        """Close one Terminal, releasing its budget and reaping its shell.

        Args:
            terminal_id: The Terminal to close.
        """
        return await self.terminal_service.close_terminal(terminal_id)

    async def detach_terminal(self, terminal_id: str, websocket: Any) -> None:
        """Leave a Terminal running without its connection, for the grace window.

        Args:
            terminal_id: The Terminal whose connection closed.
            websocket: The connection that closed.
        """
        await self.terminal_service.detach(
            terminal_id, websocket, on_expired=self.broadcast_status_update
        )

    async def reattach_terminal(
        self,
        terminal_id: str,
        client_id: str,
        key: str,
        node_name: str,
        websocket: Any,
        greet: Callable[[str], Awaitable[None]],
    ) -> Terminal:
        """Give a detached Terminal back to the page that had it.

        Args:
            terminal_id: The Terminal the page names.
            client_id: The asking Session's token.
            key: The Lab key the page connected under.
            node_name: The Node the page asks for.
            websocket: The page's new connection.
            greet: Sends the page the replay before live output flows.

        Returns:
            The reattached Terminal.

        Raises:
            TerminalEnded: If the Terminal cannot be reattached.
        """
        return await self.terminal_service.reattach(
            terminal_id, client_id, key, node_name, websocket, greet
        )

    async def close_terminal_with_reason(
        self, terminal_id: str, reason: str
    ) -> None:
        """Close a Terminal whose shell exited, telling the browser why.

        Args:
            terminal_id: The Terminal whose shell exited.
            reason: The reason string sent to the browser.
        """
        await self.terminal_service.close_terminal_with_reason(terminal_id, reason)
        await self.broadcast_status_update()

    async def close_session_terminals(self, session_token: str) -> int:
        """Close every Terminal owned by one Session.

        Registered with the auth layer as its Terminal closer, so destroying a
        Session reaches the Terminal registry without auth knowing this class
        exists.

        Args:
            session_token: The Session whose Terminals are being closed.

        Returns:
            The number of Terminals closed.
        """
        return await self.terminal_service.close_session_terminals(session_token)

    async def update_activity(self, terminal_id: str) -> None:
        """Mark a Terminal as used just now, deferring its idle timeout.

        Args:
            terminal_id: The Terminal reporting activity.
        """
        return await self.terminal_service.update_activity(terminal_id)

    def terminal_count(self, session_token: str) -> int:
        """Return how many Terminals one Session currently holds.

        Registered with the auth layer as its Terminal counter, so the
        Session Cap can tell an in-use Session from an idle one without
        knowing this class exists.

        Args:
            session_token: The Session to check.

        Returns:
            The number of live Terminals owned by that Session.
        """
        return self.terminal_service.terminal_count(session_token)

    async def disconnect_link(self, key: str, node1: str, node2: str) -> dict:
        """Bring the link between two Nodes down and broadcast the Topology."""
        return await self._set_link_condition(key, node1, node2, 'down')

    async def reconnect_link(self, key: str, node1: str, node2: str) -> dict:
        """Bring the link between two Nodes up and broadcast the Topology."""
        return await self._set_link_condition(key, node1, node2, 'up')

    async def _set_link_condition(self, key: str, node1: str, node2: str, status: str) -> dict:
        """Set a link's condition over the handle and broadcast the Topology."""
        return await self._run(
            key,
            lambda h: h.set_link_status(node1, node2, status),
            broadcast_topology=True,
        )

    async def link_configure(self, key: str, node1: str, node2: str,
                             delay: Optional[str] = None,
                             jitter: Optional[str] = None,
                             loss: Optional[str] = None,
                             bandwidth: Optional[str] = None,
                             scheduler: str = 'hfsc') -> dict:
        """Apply Link Config to a link and broadcast the Topology."""
        return await self._run(
            key,
            lambda h: h.configure_link(
                node1,
                node2,
                delay=delay,
                jitter=jitter,
                loss=loss,
                bandwidth=bandwidth,
                scheduler=scheduler,
            ),
            broadcast_topology=True,
        )

    async def run_ping(self, key: str, source: str, destination: str, count: int = 4,
                       ipv6: bool = False) -> dict:
        """Ping ``destination`` from ``source`` inside the Lab."""
        return await self._run(
            key,
            lambda h: h.run_ping(source, destination, count=count, ipv6=ipv6),
        )

    async def run_traceroute(self, key: str, source: str, destination: str, ipv6: bool = False,
                             resolve: bool = False) -> dict:
        """Trace the route from ``source`` to ``destination`` inside the Lab."""
        return await self._run(
            key,
            lambda h: h.run_traceroute(source, destination, ipv6=ipv6, resolve=resolve),
        )

    async def run_iperf(self, key: str, server: str, client: str) -> dict:
        """Run a five-second iperf between two Nodes."""
        return await self._run(
            key,
            lambda h: h.run_iperf(server, client),
        )

    async def run_iperf3(self, key: str, server: str, client: str, seconds: int = 10,
                         protocol: str = 'tcp', json_output: bool = False) -> dict:
        """Run iperf3 between two Nodes with the given options."""
        return await self._run(
            key,
            lambda h: h.run_iperf3(
                server,
                client,
                seconds=seconds,
                protocol=protocol,
                json_output=json_output,
            ),
        )

    async def get_node_routes(self, key: str, node_name: str) -> dict:
        """A Node's routing table."""
        return await self._run(
            key,
            lambda h: h.get_node_routes(node_name),
        )

    async def get_node_ospf(self, key: str, node_name: str) -> dict:
        """A Node's OSPF neighbours and routes."""
        return await self._run(
            key,
            lambda h: h.get_node_ospf(node_name),
        )

    async def get_node_bgp(self, key: str, node_name: str) -> dict:
        """A Node's BGP summary."""
        return await self._run(
            key,
            lambda h: h.get_node_bgp(node_name),
        )

    async def get_node_leases(self, key: str, node_name: str) -> dict:
        """The Leases a Node's DHCP Service holds."""
        return await self._run(
            key,
            lambda h: h.get_node_leases(node_name),
        )

    async def get_node_relay_activity(self, key: str, node_name: str) -> dict:
        """Return the forwarded DHCP requests recorded by a Node's Relay.

        Args:
            key: The Lab's key.
            node_name: The relaying Node's name.

        Returns:
            The activity query result from the active Lab.
        """
        return await self._run(
            key,
            lambda h: h.get_node_relay_activity(node_name),
        )

    async def get_node_pools(self, key: str, node_name: str) -> dict:
        """Fetch configured DHCP address pools for a Node via PoolService.

        Args:
            key: The Lab's key.
            node_name: The name of the Node whose pools to query.

        Returns:
            Dict containing pools data with CIDR, start, end, and interface.
        """
        return await self._run(
            key,
            lambda h: h.get_node_pools(node_name),
        )

    async def set_node_pool(
        self,
        key: str,
        node_name: str,
        cidr: str,
        start: str,
        end: str,
        lease_time: Optional[int] = None,
        preferred_lifetime: Optional[int] = None,
        router: str = "",
        dns_server: str = "",
        reservations: Optional[List[dict]] = None,
    ) -> dict:
        """Update a DHCP Pool's editable fields for a Node and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The name of the Node whose pool to update.
            cidr: The Subnet CIDR identifying the pool.
            start: The new start address for the pool range.
            end: The new end address for the pool range.
            lease_time: New lease time in seconds, or ``None`` to leave Kea's
                own default in place.
            preferred_lifetime: New preferred lifetime in seconds (IPv6 only),
                or ``None`` to leave Kea's default in place.
            router: New router option, or ``""`` to omit it.
            dns_server: New DNS-server option, or ``""`` to omit it.
            reservations: New Reservations as raw ``{"mac", "ip", "hostname"}``
                dicts, replacing whatever was declared before.

        Returns:
            Dict containing updated pool and all pools data.

        Raises:
            ValueError: The Node or Service was not found, the Subnet is not
                served by this Service, any field is invalid, or Kea rejected
                the prospective configuration.
        """
        result = await self._run(
            key,
            lambda h: h.set_node_pool(
                node_name,
                cidr,
                start,
                end,
                lease_time=lease_time,
                preferred_lifetime=preferred_lifetime,
                router=router,
                dns_server=dns_server,
                reservations=reservations,
            ),
        )
        await self.feed(key).broadcast({
            "type": "dhcp_pool_update",
            "data": {
                "node": node_name,
                "pool": result["pool"],
                "pools": result["pools"],
            },
        })
        return result

    async def get_terminals(self) -> List[dict]:
        """Describe every live Terminal, for the Instructor's Terminal list.

        Returns:
            One row per Terminal: id, node, owning Session, username, uptime
            and idle time.
        """
        return self.terminal_service.get_terminals()

    def _dns_lock(self, key: str, node_name: str) -> asyncio.Lock:
        """Return the per-Node lock serializing Knot zone transactions.

        knotd refuses a second open zone transaction on the same server, so
        concurrent Learner edits from the Web UI must be serialized per Node
        to avoid spurious transaction-conflict rejections.

        Args:
            key: The Lab's key.
            node_name: The DNS-serving Node whose transactions to serialize.

        Returns:
            The asyncio.Lock guarding that Node's zone transactions.
        """
        return self.lab(key).dns_locks.setdefault(node_name, asyncio.Lock())

    async def list_dns_nodes(self, key: str) -> dict:
        """List every Node offering an Authoritative DNS Service and its Zones.

        Returns:
            Dict with a 'nodes' list of per-node DNS info dicts.
        """
        nodes = await self._run(key, lambda h: h.list_dns_nodes())
        return {"nodes": nodes}

    async def get_dns_records(self, key: str, node_name: str, zone: str) -> dict:
        """Return the active Resource Records of one hosted zone.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.

        Returns:
            Dict with the node, normalized zone apex, and record list.
        """
        records = await self._run(
            key,
            lambda h: h.get_dns_records(node_name, zone),
        )
        return {"node": node_name, "zone": zone, "records": records}

    async def get_dns_zone_status(self, key: str, node_name: str, zone: str) -> dict:
        """Return SOA serial and operational status for one hosted zone.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.

        Returns:
            Dict with the node, normalized zone apex, and status fields.
        """
        return await self._run(
            key,
            lambda h: h.get_dns_zone_status(node_name, zone),
        )

    async def get_dns_transfers(
        self,
        key: str,
        node_name: str,
        zone: Optional[str] = None,
        include_notify: bool = False,
    ) -> dict:
        """Return the Transfer Log and replication state of a Node's Zones.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: Optional zone apex to narrow the log to.
            include_notify: Whether to include Notify prompt events in the log.

        Returns:
            Dict with the node, zone, role, transfers and replication state.
        """
        return await self._run(
            key,
            lambda h: h.get_dns_transfers(node_name, zone=zone, include_notify=include_notify),
        )

    async def refresh_dns_zone(self, key: str, node_name: str, zone: str) -> dict:
        """Ask a Secondary whether its Primary's Serial has moved.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.

        Returns:
            Dict naming the node, zone, action and Primary.
        """
        return await self._run(
            key,
            lambda h: h.refresh_dns_zone(node_name, zone),
        )

    async def retransfer_dns_zone(self, key: str, node_name: str, zone: str) -> dict:
        """Ask a Secondary to take a full copy regardless of the Serial.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.

        Returns:
            Dict naming the node, zone, action and Primary.
        """
        return await self._run(
            key,
            lambda h: h.retransfer_dns_zone(node_name, zone),
        )

    async def get_dns_stats(self, key: str, node_name: str) -> dict:
        """Return live mod-stats query counters for a Node's DNS Service.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.

        Returns:
            Dict with the node and its parsed statistics counters.
        """
        stats = await self._run(key, lambda h: h.get_dns_stats(node_name))
        return {"node": node_name, "stats": stats}

    async def reset_dns_stats(self, key: str, node_name: str) -> dict:
        """Reset the query statistics baseline for a Node's DNS Service.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.

        Returns:
            Dict confirming the reset.
        """
        await self._run(key, lambda h: h.reset_dns_stats(node_name))
        return {"node": node_name, "reset": True}

    async def set_dns_record(
        self,
        key: str,
        node_name: str,
        zone: str,
        owner: str,
        rtype: str,
        data: str,
        ttl: Optional[int] = None,
        old_owner: Optional[str] = None,
        old_type: Optional[str] = None,
        old_data: Optional[str] = None,
    ) -> dict:
        """Add or update a zone record atomically and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.
            owner: Record owner name.
            rtype: DNS record type string.
            data: Record rdata payload.
            ttl: Optional TTL in seconds.
            old_owner: Optional previous owner name for atomic replacement.
            old_type: Optional previous type for atomic replacement.
            old_data: Optional previous data for atomic replacement.

        Returns:
            Dict describing the committed record.
        """
        async with self._dns_lock(key, node_name):
            result = await self._run(
                key,
                lambda h: h.set_dns_record(
                    node_name,
                    zone,
                    owner,
                    rtype,
                    data,
                    ttl=ttl,
                    old_owner=old_owner,
                    old_type=old_type,
                    old_data=old_data,
                ),
            )
        await self.feed(key).broadcast({
            "type": "DNS_RECORD_UPDATED",
            "data": {**result, "action": "replace" if old_owner else "set"},
        })
        return result

    async def unset_dns_record(
        self,
        key: str,
        node_name: str,
        zone: str,
        owner: str,
        rtype: Optional[str] = None,
        data: Optional[str] = None,
    ) -> dict:
        """Remove a zone record atomically and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.
            owner: Record owner name.
            rtype: Optional record type string.
            data: Optional record rdata payload.

        Returns:
            Dict describing the removed record.
        """
        async with self._dns_lock(key, node_name):
            result = await self._run(
                key,
                lambda h: h.unset_dns_record(
                    node_name, zone, owner, rtype=rtype, data=data
                ),
            )
        await self.feed(key).broadcast({
            "type": "DNS_RECORD_UPDATED",
            "data": {**result, "action": "delete"},
        })
        return result

    async def get_dnssec_details(self, key: str, node_name: str, zone: str) -> Dict[str, Any]:
        """Return the DNSSEC state, keys, and DS records for a hosted zone.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.

        Returns:
            Dict containing DNSSEC status, NSEC3 settings, key list, and DS records.
        """
        return await self._run(
            key,
            lambda h: h.get_dnssec_details(node_name, zone),
        )

    async def trigger_dnssec_rollover(
        self, key: str, node_name: str, zone: str, key_type: str = "zsk"
    ) -> Dict[str, Any]:
        """Trigger an on-demand DNSSEC key rollover for a hosted zone and broadcast.

        Args:
            key: The Lab's key.
            node_name: Serving Node's name.
            zone: The zone apex domain name.
            key_type: Either 'zsk' or 'ksk'.

        Returns:
            Dict describing rollover status.
        """
        async with self._dns_lock(key, node_name):
            result = await self._run(
                key,
                lambda h: h.trigger_dnssec_rollover(node_name, zone, key_type=key_type),
            )
        await self.feed(key).broadcast({
            "type": "DNSSEC_ROLLOVER_TRIGGERED",
            "data": {**result, "node": node_name, "zone": zone, "key_type": key_type},
        })
        return result

    def _rpki_lock(self, key: str, node_name: str) -> asyncio.Lock:
        """Return the per-Node lock serializing one RPKI CA's ROA and ASPA changes.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.

        Returns:
            The asyncio.Lock guarding that Node's object changes.
        """
        return self.lab(key).rpki_locks.setdefault(node_name, asyncio.Lock())

    async def _rpki_change(
        self,
        key: str,
        node_name: str,
        kind: str,
        action: str,
        change: Callable[[LabHandle], Dict[str, Any]],
    ) -> dict:
        """Apply one ROA or ASPA change under the Node's lock and broadcast it.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            kind: ``roa`` or ``aspa``.
            action: ``add``, ``replace`` or ``remove``.
            change: The Lab handle call that makes the change.

        Returns:
            The change's dictionary, as the Lab answered it.

        Raises:
            ValueError: If the change is refused; nothing is broadcast.
        """
        async with self._rpki_lock(key, node_name):
            result = await self._run(key, change)
        await self.feed(key).broadcast({
            "type": "RPKI_OBJECTS_UPDATED",
            "data": {"node": node_name, "asn": result["asn"], "kind": kind, "action": action},
        })
        return result

    async def list_rpki_cas(self, key: str, node_name: str) -> dict:
        """Return each Hosted CA of an RPKI CA with its ROAs and ASPAs.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.

        Returns:
            Dict with the node and its Hosted CAs.
        """
        cas = await self._run(key, lambda h: h.list_rpki_cas(node_name))
        return {"node": node_name, "cas": cas}

    async def get_rpki_hosted_ca(self, key: str, node_name: str, asn: Any) -> dict:
        """Return one Hosted CA of an RPKI CA with its ROAs and ASPAs.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.

        Returns:
            Dict with the node, and the Hosted CA's ``asn``, ``roas`` and ``aspas``.
        """
        hosted = await self._run(key, lambda h: h.get_rpki_hosted_ca(node_name, asn))
        return {"node": node_name, **hosted}

    async def add_rpki_roa(self, key: str, node_name: str, asn: Any, roa: Dict[str, Any]) -> dict:
        """Sign a ROA at a Hosted CA and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN.
            roa: The ROA's ``prefix``, ``max_length`` and ``origin``.

        Returns:
            The Lab's answer: the ROA signed.
        """
        return await self._rpki_change(
            key, node_name, "roa", "add", lambda h: h.add_rpki_roa(node_name, asn, roa),
        )

    async def replace_rpki_roa(
        self, key: str, node_name: str, asn: Any, old: Dict[str, Any], new: Dict[str, Any],
    ) -> dict:
        """Change a ROA at a Hosted CA, such as its maxLength, and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN.
            old: The ROA being replaced.
            new: The ROA replacing it.

        Returns:
            The Lab's answer: the old and new ROAs.
        """
        return await self._rpki_change(
            key, node_name, "roa", "replace", lambda h: h.replace_rpki_roa(node_name, asn, old, new),
        )

    async def remove_rpki_roa(self, key: str, node_name: str, asn: Any, roa: Dict[str, Any]) -> dict:
        """Withdraw a ROA from a Hosted CA and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN.
            roa: The ROA to withdraw.

        Returns:
            The Lab's answer: the ROA withdrawn.
        """
        return await self._rpki_change(
            key, node_name, "roa", "remove", lambda h: h.remove_rpki_roa(node_name, asn, roa),
        )

    async def add_rpki_aspa(self, key: str, node_name: str, asn: Any, aspa: Dict[str, Any]) -> dict:
        """Sign an ASPA at a Hosted CA and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN.
            aspa: The ASPA's ``customer`` and ``providers``.

        Returns:
            The Lab's answer: the ASPA signed.
        """
        return await self._rpki_change(
            key, node_name, "aspa", "add", lambda h: h.add_rpki_aspa(node_name, asn, aspa),
        )

    async def remove_rpki_aspa(self, key: str, node_name: str, asn: Any, customer: Any) -> dict:
        """Withdraw a customer's ASPA from a Hosted CA and broadcast the change.

        Args:
            key: The Lab's key.
            node_name: The RPKI CA's Node.
            asn: The Hosted CA's ASN.
            customer: The customer whose ASPA is withdrawn.

        Returns:
            The Lab's answer: the customer withdrawn.
        """
        return await self._rpki_change(
            key, node_name, "aspa", "remove", lambda h: h.remove_rpki_aspa(node_name, asn, customer),
        )

    async def get_rpki_vrps(self, key: str, node_name: str) -> dict:
        """Return what a Validator serves: its VRPs, ASPAs, refresh times and connected Routers.

        Args:
            key: The Lab's key.
            node_name: The Validator's Node.

        Returns:
            Dict with the node, its ``vrps``, ``aspas``, refresh times and ``routers``.
        """
        return await self._run(key, lambda h: h.get_rpki_vrps(node_name))

    def _resolver_lock(self, key: str, node_name: str) -> asyncio.Lock:
        """Return the per-Node lock serializing Access Control rewrites.

        Applying rules rewrites ``unbound.conf`` and then reloads the daemon;
        two Learners saving at once would interleave one Learner's write with
        the other's reload, leaving the file describing a policy the daemon is
        not enforcing.

        Args:
            key: The Lab's key.
            node_name: The resolving Node whose rewrites to serialize.

        Returns:
            The asyncio.Lock guarding that Node's Access Control rewrites.
        """
        return self.lab(key).resolver_locks.setdefault(node_name, asyncio.Lock())

    async def get_resolver_cache(self, key: str, node_name: str) -> dict:
        """Return a Resolver's cached entries alongside its query counters.

        Both come from the same control socket and the Cache Inspector shows
        them side by side, so one request fetches both: polling them
        separately would show a hit ratio computed a round trip away from the
        entries that produced it.

        Args:
            key: The Lab's key.
            node_name: The resolving Node's name.

        Returns:
            Dict with the node, its cache entries in stable order, and its
            metrics.
        """
        cache = await self._run(key, lambda h: h.get_resolver_cache(node_name))
        return {"node": node_name, **cache}

    async def flush_resolver_cache(
        self,
        key: str,
        node_name: str,
        target: Optional[str] = None,
        scope: str = "name",
    ) -> dict:
        """Evict cached entries so the next query walks the hierarchy again.

        Args:
            key: The Lab's key.
            node_name: The resolving Node's name.
            target: The name or zone to evict; omit to clear everything.
            scope: ``'name'`` for exactly one name, ``'zone'`` for the subtree.

        Returns:
            Dict describing what was flushed and Unbound's confirmation.
        """
        return await self._run(
            key,
            lambda h: h.flush_resolver_cache(node_name, target=target, scope=scope),
        )

    async def get_resolver_access(self, key: str, node_name: str) -> dict:
        """Return the Access Control rules a Resolver is enforcing.

        Args:
            key: The Lab's key.
            node_name: The resolving Node's name.

        Returns:
            Dict with the node and its rules, in declaration order.
        """
        rules = await self._run(
            key,
            lambda h: h.get_resolver_access(node_name),
        )
        return {"node": node_name, "rules": rules}

    async def set_resolver_access(
        self, key: str, node_name: str, rules: List[dict]
    ) -> dict:
        """Replace a Resolver's Access Control rules, hot, and broadcast them.

        The whole policy is replaced in one reload rather than one reload per
        edited rule, so a Learner retyping a subnet never briefly enforces a
        policy that neither the old rule nor the new one describes.

        Args:
            key: The Lab's key.
            node_name: The resolving Node's name.
            rules: The rules to enforce, as ``{"subnet", "action"}`` dicts, in
                declaration order.

        Returns:
            Dict with the node and the rules now in force.

        Raises:
            ValueError: If a subnet or action is invalid, the Node or Service
                is absent, or the daemon rejected the reload.
        """
        # Parsed before the lock is taken so an invalid subnet is rejected
        # without first queueing behind another Learner's reload. AccessRule
        # owns the vocabulary, so this is where an unknown action is named.
        parsed = [AccessRule(rule["subnet"], rule["action"]) for rule in rules]
        async with self._resolver_lock(key, node_name):
            applied = await self._run(
                key,
                lambda h: h.set_resolver_access(
                    node_name, [rule.to_dict() for rule in parsed]
                ),
            )
        result = {"node": node_name, "rules": applied}
        await self.feed(key).broadcast({
            "type": "RESOLVER_ACCESS_UPDATED",
            "data": result,
        })
        return result

    async def get_speaker(self, key: str, node_name: str) -> dict:
        """Return a Speaker's Neighbors with what each was sent and sent back.

        One read serves the whole panel: the Neighbor listing and both RIBs
        come from the same three commands under the Speaker's own lock.

        Args:
            key: The Lab's key.
            node_name: The Speaker's name.

        Returns:
            The SpeakerQueryService's dictionary.
        """
        return await self._run(
            key,
            lambda h: h.get_speaker(node_name),
        )

    async def announce_route(
        self, key: str, node_name: str, fields: Dict[str, Any], neighbor: Optional[str] = None
    ) -> dict:
        """Announce a route from a Speaker to one Neighbor, or to all of its family.

        The body's fields become the same route object the CLI's line does;
        the service validates it before anything is written, so a bad field
        raises the ValueError the API maps to 400.

        Args:
            key: The Lab's key.
            node_name: The Speaker's name.
            fields: The route's fields, as ``BGPRoute`` takes them.
            neighbor: The Neighbor's address, or None for all.

        Returns:
            The SpeakerRouteService's dictionary.
        """
        return await self._run(
            key,
            lambda h: h.announce_route(node_name, fields, neighbor=neighbor),
        )

    async def generate_routes(
        self, key: str, node_name: str, neighbor: Optional[str], count: Any,
        sub_prefixes: Any = None, prefix_seed: Any = None, as_path_seed: Any = None,
    ) -> dict:
        """Announce a burst of generated routes from a Speaker to one Neighbor.

        Args:
            key: The Lab's key.
            node_name: The Speaker's name.
            neighbor: The Neighbor's address, or None when the Speaker has one.
            count: Base prefixes to announce.
            sub_prefixes: Sub-prefixes per base prefix, or None.
            prefix_seed: Seed for the prefixes, or None.
            as_path_seed: Seed for the AS-PATHs, or None.

        Returns:
            The SpeakerRouteService's dictionary, ``announced`` included.
        """
        return await self._run(
            key,
            lambda h: h.generate_routes(
                node_name,
                neighbor,
                count,
                sub_prefixes=sub_prefixes,
                prefix_seed=prefix_seed,
                as_path_seed=as_path_seed,
            ),
        )

    async def withdraw_route(
        self, key: str, node_name: str, prefix: Optional[str], withdraw_all: bool,
        neighbor: Optional[str] = None,
    ) -> dict:
        """Withdraw a prefix from a Speaker, or everything a Neighbor was sent.

        Args:
            key: The Lab's key.
            node_name: The Speaker's name.
            prefix: The prefix to withdraw, or None with ``withdraw_all``.
            withdraw_all: Clear the Neighbor's whole outgoing RIB instead.
            neighbor: The Neighbor's address, or None for all.

        Returns:
            The SpeakerRouteService's dictionary.

        Raises:
            ValueError: Neither a prefix nor ``all`` was given.
        """
        if withdraw_all:
            return await self._run(
                key,
                lambda h: h.withdraw_all_routes(node_name, neighbor=neighbor),
            )
        if not prefix:
            raise ValueError("a withdraw needs a prefix, or all: true")
        return await self._run(
            key,
            lambda h: h.withdraw_route(node_name, prefix, neighbor=neighbor),
        )

    async def get_switch_vlans(self, key: str, switch_name: str) -> dict:
        """Return switch port VLAN configurations."""
        return await self._run(key, lambda h: h.get_switch_vlans(switch_name))

    async def set_switch_port_vlan(
        self,
        key: str,
        switch_name: str,
        port_name: str,
        mode: str,
        vlan: Optional[Any] = None,
        trunks: Optional[Any] = None,
        native: Optional[Any] = None,
    ) -> dict:
        """Configure a switch port's VLAN mode and membership, broadcasting topology update."""
        return await self._run(
            key,
            lambda h: h.set_switch_port_vlan(
                switch_name,
                port_name,
                mode,
                vlan=vlan,
                trunks=trunks,
                native=native,
            ),
            broadcast_topology=True,
        )


    async def reset_config(self, example_id: str, groups: List[str]) -> List[str]:
        """Reset the named Groups' copies of a Lab Example's Persistent Config.

        Checked and done under the Lab lock, so a start cannot slip in
        between: either the reset is refused because a Group's Lab on that
        Lab Example is alive, or the next start seeds afresh.

        Args:
            example_id: The Lab Example whose copies are reset.
            groups: The Groups whose copies are reset.

        Returns:
            The Groups that had a copy to discard.

        Raises:
            ValueError: A named Group runs that Lab Example now; nothing was
                reset.
        """
        async with self.lab_lock:
            for group in groups:
                refusal = self.lab_service.refuse_reset(group, example_id)
                if refusal is not None:
                    raise ValueError(refusal)
            return [group for group in groups if self.config_copies.reset(group, example_id)]

    async def remove_example(self, example_id: str, remove: Callable[[], None]) -> List[str]:
        """Take a Lab Example out of the Catalog and discard every Config Copy of it.

        Checked and done under the Lab lock, so no Lab of it can start
        in between.

        Args:
            example_id: The Lab Example to remove.
            remove: Removes its Catalog entry and published versions.

        Returns:
            The Groups whose Config Copy was discarded.

        Raises:
            ValueError: A Lab of it runs, or a Lab is starting; nothing was removed.
        """
        async with self.lab_lock:
            refusal = self.lab_service.refuse_removal(example_id)
            if refusal is not None:
                raise ValueError(refusal)
            remove()
            return [group for group in self.config_copies.groups_of(example_id)
                    if self.config_copies.reset(group, example_id)]

    async def open_capture(
        self,
        key: str,
        node_name: str,
        interface: str,
        capture_filter: str = "",
        viewer: Optional[str] = None,
    ) -> CaptureSubscriber:
        """Watch one Interface of a running Lab, starting its Capture if needed.

        The Interface is checked against the Lab first: only an Interface
        of a Router, Host or Speaker may be captured — drawn or not — which
        is what keeps a Capture to its own Lab's traffic.

        Args:
            key: The Lab's key.
            node_name: The Node the Interface belongs to.
            interface: The Interface to watch.
            capture_filter: The BPF expression to capture through.
            viewer: Who is watching, held to two Captures at once.

        Returns:
            The viewer's subscription to the Capture.

        Raises:
            ValueError: No Lab is running under ``key``, the Interface may
                not be captured, the viewer already holds two Captures, or
                the Capture could not start.
        """
        attachment, root_interface = await self._capture_target(key, node_name, interface)
        return await self.capture_service.subscribe(
            key, lab_dir_for(key), attachment, interface,
            capture_filter=capture_filter, viewer=viewer, root_interface=root_interface,
        )

    async def capture_interfaces(self, key: str, node_name: str) -> List[Dict[str, Any]]:
        """Every Interface of a Node that may be captured, drawn or not.

        Args:
            key: The Lab's key.
            node_name: The Node whose Interfaces are listed.

        Returns:
            One entry per Interface, as ``LabHandle.list_interfaces`` gives it.

        Raises:
            ValueError: No Lab is running under ``key``, or the Node's
                Interfaces may not be captured.
        """
        refusal = await self._run(key, lambda h: h.capture_refusal(node_name))
        if refusal is not None:
            raise ValueError(refusal)
        topology = await self._run(key, lambda h: h.topology())
        refusal = capture_node_error(topology, node_name)
        if refusal is not None:
            raise ValueError(refusal)
        if is_switch(topology, node_name):
            ports = await self._run(key, lambda h: h.switch_ports(node_name))
            return [{k: v for k, v in port.items() if k != "interface"} for port in ports]
        return await self._run(key, lambda h: h.list_interfaces(node_name))

    async def capture_pcap(
        self, key: str, node_name: str, interface: str, capture_filter: str
    ) -> List[str]:
        """The files holding one Capture's pcap, oldest first.

        Found the way the Capture wrote them, from the Interface and filter
        the pane knows, and only for an Interface that may be captured.

        Args:
            key: The Lab's key.
            node_name: The Node the Interface belongs to.
            interface: The captured Interface.
            capture_filter: The BPF expression it was captured through.

        Returns:
            The ring's files, possibly none.

        Raises:
            ValueError: No Lab is running under ``key``, or the Interface
                may not be captured.
        """
        attachment, root_interface = await self._capture_target(key, node_name, interface)
        command = build_capture_command(
            attachment, interface, lab_dir_for(key), capture_filter, root_interface=root_interface
        )
        return ring_files(command.pcap_path)

    async def _capture_target(
        self, key: str, node_name: str, interface: str
    ) -> Tuple[NodeAttachment, Optional[str]]:
        """Resolve ``interface`` of ``node_name`` to what a Capture runs on.

        A Switch's port is matched by exact name against the ports its own
        Lab lists, and the keyed root-namespace interface comes from that
        list, never from the request; every other Node is entered by its
        Namespace Anchor.

        Returns:
            The Node's attachment and, for a Switch's port only, the keyed
            interface to capture in the root namespace.

        Raises:
            ValueError: ``interface`` of ``node_name`` may not be captured.
        """
        refusal = await self._run(key, lambda h: h.capture_refusal(node_name))
        if refusal is not None:
            raise ValueError(refusal)
        topology = await self._run(key, lambda h: h.topology())
        refusal = capture_node_error(topology, node_name)
        if refusal is None and is_switch(topology, node_name):
            ports = await self._run(key, lambda h: h.switch_ports(node_name))
            refusal = capture_target_error(
                topology, node_name, interface, [p["name"] for p in ports]
            )
            if refusal is None:
                keyed = next(p["interface"] for p in ports if p["name"] == interface)
                return NodeAttachment(node_name, 0, lab_dir_for(key)), keyed
        elif refusal is None:
            interfaces = await self._run(key, lambda h: h.list_interfaces(node_name))
            refusal = capture_target_error(
                topology, node_name, interface, [i["name"] for i in interfaces]
            )
        if refusal is not None:
            raise ValueError(refusal)
        return await self._run(key, lambda h: h.node_attachment(node_name)), None

    async def close_capture(self, subscriber: CaptureSubscriber, linger: bool = True) -> None:
        """Stop watching a Capture; see ``CaptureService.unsubscribe``."""
        await self.capture_service.unsubscribe(subscriber, linger=linger)

    async def nat_table(self, key: str) -> List[Dict[str, Any]]:
        """Every translating Node's NAT Table, one per Node.

        Which Nodes translate is the Lab's to say, through its Topology;
        each table is read inside its Node by the Namespace Anchor, so it is
        that Node's and this Lab's alone.  Never the NAT Gateway's: it is
        not a translator.

        A Router with several outside Interfaces is listed once per
        Topology translator row, so it is read once, under its first.

        Args:
            key: The Lab's key.

        Returns:
            ``{"node", "interface", "entries", "rules"}`` per translating
            Node, in the Topology's order; empty when nothing in the Lab
            translates. ``rules`` are the Node's NAT rules as they run now.

        Raises:
            ValueError: No Lab is running under ``key``, or a table or its
                rules could not be read.
        """
        topology = await self._run(key, lambda h: h.topology())
        loop = asyncio.get_running_loop()
        tables: List[Dict[str, Any]] = []
        read: Set[str] = set()
        for translator in topology.get("translators", []):
            node = translator["node"]
            if node in read:
                continue
            read.add(node)
            attachment = await self._run(key, lambda h: h.node_attachment(node))
            text = await loop.run_in_executor(None, self.nat_table_reader.read, attachment)
            rules = await loop.run_in_executor(None, self.nat_table_reader.read_rules, attachment)
            tables.append({
                "node": node,
                "interface": translator["interface"],
                "entries": [e.as_dict() for e in parse_nat_table(text)],
                "rules": [r.as_dict() for r in parse_nat_rules(rules)],
            })
        return tables

    async def get_switch_mac_table(self, key: str, switch_name: str) -> dict:
        """Return a Switch's learned MAC addresses, ports, VLANs, and ages."""
        return await self._run(key, lambda h: h.get_switch_mac_table(switch_name))

    async def flush_switch_mac_table(self, key: str, switch_name: str) -> dict:
        """Flush learned MAC addresses and return the Switch's current table."""
        return await self._run(key, lambda h: h.flush_switch_mac_table(switch_name))

    async def get_switch_spanning_tree(self, key: str, switch_name: str) -> dict:
        """Return the Switch's spanning-tree protocol, root, and port states."""
        return await self._run(key, lambda h: h.get_switch_spanning_tree(switch_name))

    def _broadcast_lock(self) -> asyncio.Lock:
        """Return the status-broadcast lock for the running event loop.

        An ``asyncio.Lock`` binds to the loop that first waits on it, and this
        registry outlives any one server's loop, so a lock left over from an
        earlier loop is replaced rather than awaited.
        """
        loop = asyncio.get_running_loop()
        if self._status_broadcast_lock is None or self._status_broadcast_lock_loop is not loop:
            self._status_broadcast_lock = asyncio.Lock()
            self._status_broadcast_lock_loop = loop
        return self._status_broadcast_lock

    async def remove_group(self, group: str) -> None:
        """Stop a removed login's Labs, drop its Assignment and tell the browsers.

        Its Config Copies stay, so a login added again under the same name
        resumes the Learners' work.

        Args:
            group: The Group whose login was removed.
        """
        for lab in self.lab_service.labs():
            if lab.group == group:
                await self.stop_lab(lab.key)
        self.groups.unassign(group)
        await self.broadcast_status_update()

    async def network_tool_argv(
        self,
        key: str,
        tool: str,
        source: str,
        destination: str,
        *,
        count: int = 4,
        ipv6: bool = False,
        resolve: bool = False,
    ) -> List[str]:
        """Build the command a streamed ping or traceroute runs.

        Only the two short Lab reads it needs go through the Lab: the test
        itself runs outside, so it never holds the Lab's operation lock.

        Args:
            key: The Lab's key.
            tool: ``"ping"`` or ``"traceroute"``.
            source: The Node the test runs on.
            destination: A Node's name, an address, or a domain name.
            count: Echo requests a ping sends.
            ipv6: Test over IPv6.
            resolve: Let traceroute look up each hop's name.

        Returns:
            The command to start.

        Raises:
            ValueError: No Lab is running under ``key``, or the test cannot
                run as asked.
        """
        target = await self._run(key, lambda h: h.resolve_test_target(source, destination, ipv6=ipv6))
        attachment = await self._run(key, lambda h: h.node_attachment(source))
        return build_tool_argv(
            attachment, tool, target, count=count, ipv6=ipv6, resolve=resolve
        )

    async def bandwidth_test_argv(
        self,
        key: str,
        tool: str,
        server: str,
        client: str,
        *,
        seconds: int = 10,
        protocol: str = "tcp",
        json_output: bool = False,
        bandwidth: Optional[str] = None,
    ) -> Tuple[List[str], List[str]]:
        """Build the two commands a streamed bandwidth test runs.

        Only the three short Lab reads it needs go through the Lab: the tests
        themselves run outside, so they never hold the Lab's operation lock.

        Args:
            key: The Lab's key.
            tool: ``"iperf3"``.
            server: The Node that serves the test; the client connects to its
                first address.
            client: The Node the test runs on.
            seconds: The test's length in seconds.
            protocol: ``"tcp"`` or ``"udp"``.
            json_output: Print iperf3's JSON rather than its lines.
            bandwidth: The rate, such as ``"10M"``, or ``None`` for the tool's
                default; see :func:`build_bandwidth_argv`.

        Returns:
            The server's command and the client's, to start in that order.

        Raises:
            ValueError: No Lab is running under ``key``, the server has no
                address, or the test cannot run as asked.
        """
        server_attachment = await self._run(key, lambda h: h.node_attachment(server))
        client_attachment = await self._run(key, lambda h: h.node_attachment(client))
        interfaces = await self._run(key, lambda h: h.list_interfaces(server))
        address = next((a for intf in interfaces for a in intf["addresses"]), None)
        if address is None:
            raise ValueError(f"Server '{server}' has no IP address")
        return build_bandwidth_argv(
            server_attachment, client_attachment, tool, address,
            seconds=seconds, protocol=protocol, json_output=json_output,
            bandwidth=bandwidth,
        )


# Global registry instance (backward-compatible export)
registry = SessionRegistry()
