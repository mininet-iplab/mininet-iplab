"""Test doubles for exercising command-issuing code without privileges.

Most of mnIPLab's node behaviour ends in a shell command — ``ip -6 addr add``,
``tc qdisc change``, ``iptables -D``.  Running those for real needs root, so the
fakes here stand in for a Mininet node's :meth:`cmd` and record what would have
been executed.  A test can then assert on the commands themselves, and force any
of them to fail, which is what makes failure handling testable in the
unprivileged suite.
"""

from __future__ import annotations

import asyncio
import os
import select
import shutil
import socket
import tempfile
import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

from mniplab.cli import mnIPLabCLI
from mniplab.lab_path import LabPathResolverMixin
from mniplab.switch import SubinterfaceMixin
from mniplab.webserver import auth


class CommandRecorder:
    """Records commands issued to a node and optionally fails them.

    Stands in for :meth:`mininet.node.Node.cmd`.  Mininet reports command
    failures as text on stdout rather than by raising, so a forced failure here
    returns error text the same way a real failed command would.

    Attributes:
        commands: Every command issued, in order.
    """

    def __init__(self) -> None:
        """Initialize an empty recorder."""
        self.commands: List[str] = []
        self._failures: List[Tuple[str, str]] = []
        self._responses: List[Tuple[str, str]] = []

    def fail_on(self, match: str, response: str = 'RTNETLINK answers: Operation not permitted') -> None:
        """Make any command containing ``match`` return error text.

        Args:
            match: Substring identifying the command(s) to fail.
            response: Error text to return, mimicking a real failure.
        """
        self._failures.append((match, response))

    def respond_to(self, match: str, response: str) -> None:
        """Make any command containing ``match`` return ``response``.

        Args:
            match: Substring identifying the command(s) to answer.
            response: Text to return.
        """
        self._responses.append((match, response))

    def issued(self, match: str) -> List[str]:
        """Return every recorded command containing ``match``.

        Args:
            match: Substring to search for.

        Returns:
            Matching commands, in the order they were issued.
        """
        return [cmd for cmd in self.commands if match in cmd]

    def issued_any(self, match: str) -> bool:
        """Return True if any recorded command contains ``match``.

        Args:
            match: Substring to search for.

        Returns:
            True if at least one command matched.
        """
        return bool(self.issued(match))

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        """Record a command and return its configured response.

        Args:
            *args: Command parts, joined with spaces as Mininet does.
            **kwargs: Ignored; accepted for signature compatibility.

        Returns:
            Configured response text, or an empty string by default.
        """
        del kwargs
        command = ' '.join(str(arg) for arg in args)
        self.commands.append(command)

        for match, response in self._failures:
            if match in command:
                return response

        for match, response in self._responses:
            if match in command:
                return response

        return ''


class FakeNode(SubinterfaceMixin):
    """A stand-in for a Mininet node backed by a CommandRecorder.

    Carries what the code that reads a Node reads: a name, a ``cmd`` method,
    an interface registry, and the attributes mnIPLab adds — attached
    Services and Relays, the display type, and the hidden flag.  One fake
    serves address and traffic-control code, the provisioners, and
    ``TopologyBuilder`` alike, so a test describes a Node once, in the
    domain's words, rather than in whichever attributes one reader happens
    to touch.

    Attributes:
        name: Node name used in error messages.
        recorder: The CommandRecorder capturing issued commands.
        services: The Services attached to this Node.
        relays: The Relays this Node forwards for.
        display_type: How the Web UI draws the Node.
        hidden: True for infrastructure Nodes kept out of listings.
        net: The Lab the Node belongs to, set when a FakeLab takes it.
    """

    # Declared, not assigned: a test that needs these attaches them itself.
    pid: int
    nat: List[Any]

    def __init__(
        self,
        name: str = 'n1',
        recorder: Optional[CommandRecorder] = None,
        *,
        intfs: Optional[List['FakeIntf']] = None,
        services: Optional[List[Any]] = None,
        relays: Optional[List[Any]] = None,
        display_type: str = 'host',
        hidden: bool = False,
    ) -> None:
        """Initialize a fake node.

        Args:
            name: Node name.
            recorder: Recorder to use; a fresh one is created when omitted.
            intfs: Interfaces to register, in port order.
            services: Services attached to the Node.
            relays: Relays the Node forwards for.
            display_type: How the Web UI draws the Node.
            hidden: True to keep the Node out of listings.
        """
        self.name = name
        self.recorder = recorder if recorder is not None else CommandRecorder()
        self.intfs: dict = {}
        self.params: dict = {}
        self.shell = True
        self.services: List[Any] = list(services or [])
        self.relays: List[Any] = list(relays or [])
        self.display_type = display_type
        self.hidden = hidden
        self.net: Any = None
        for intf in intfs or []:
            self.addIntf(intf)

    @classmethod
    def with_addresses(cls, name: str, *addresses: Any, **kwargs: Any) -> 'FakeNode':
        """Build a Node with one interface per address, named ``<name>-eth<n>``.

        Args:
            name: Node name.
            *addresses: One entry per interface: an IPv4 address string, or
                a ``(ipv4, ipv6)`` pair where either half may be None.
            **kwargs: Passed to the constructor (services, relays, ...).

        Returns:
            The Node, its interfaces registered in the order given.
        """
        intfs = []
        for port, address in enumerate(addresses):
            ip, ip6 = address if isinstance(address, tuple) else (address, None)
            intfs.append(FakeIntf(f'{name}-eth{port}', ip=ip, ip6=ip6))
        return cls(name, intfs=intfs, **kwargs)

    def cmd(self, *args: Any, **kwargs: Any) -> str:
        """Issue a command through the recorder.

        Args:
            *args: Command parts.
            **kwargs: Ignored.

        Returns:
            The recorder's response.
        """
        return self.recorder(*args, **kwargs)

    def addIntf(self, intf: Any, port: Optional[int] = None, moveIntfFn: Any = None) -> None:
        """Register an interface on this node, as Mininet's Node.addIntf does.

        Args:
            intf: The interface to register.
            port: Port number; the next free one when omitted.
            moveIntfFn: Ignored; accepted for signature compatibility.
        """
        del moveIntfFn
        if port is None:
            port = len(self.intfs)
        self.intfs[port] = intf
        intf.node = self

    def newPort(self) -> int:
        """Return the next free port number, as Mininet's Node.newPort does.

        Returns:
            One past the highest registered port, or 0 when there is none.
        """
        if not self.intfs:
            return 0
        return max(self.intfs) + 1

    def defaultIntf(self) -> Any:
        """Return the node's first interface, or None when it has none.

        Returns:
            The interface registered at the lowest port number, or None.
        """
        if not self.intfs:
            return None
        return self.intfs[min(self.intfs)]

    def intfList(self) -> List[Any]:
        """Return the node's interfaces in port order, as Mininet does.

        Returns:
            The registered interfaces, lowest port first.
        """
        return [self.intfs[port] for port in sorted(self.intfs)]

    def IP(self, intf: Any = None) -> Optional[str]:
        """Return an interface's IPv4 address, the default interface's by default.

        Args:
            intf: The interface to read; the default one when omitted.

        Returns:
            The address, or None when the node has no such interface.
        """
        target = intf if intf is not None else self.defaultIntf()
        return target.ip if target is not None else None

    def terminate(self, *args: Any, **kwargs: Any) -> None:
        """Terminate the node (compatible with Mininet Node.terminate)."""

    def stop(self, *args: Any, **kwargs: Any) -> None:
        """Stop the node (compatible with Mininet Node.stop)."""



class FakeIntf:
    """A stand-in for a Mininet interface carrying its declared addresses.

    Attributes:
        name: Interface name.
        node: The node this interface belongs to.
        ip: The IPv4 address, or None.
        ip6: The IPv6 address, or None.
        prefixLen: The IPv4 prefix length, as Mininet spells it.
        prefixLen6: The IPv6 prefix length, as Mininet spells it.
    """

    # Declared, not assigned: Mininet's Link sets it, and a test that wires an
    # interface by hand attaches it.
    link: Optional['FakeLink']

    def __init__(
        self,
        name: str = 'n1-eth0',
        node: Optional[FakeNode] = None,
        *,
        ip: Optional[str] = None,
        ip6: Optional[str] = None,
        prefix_len: str = '24',
        prefix_len6: str = '64',
    ) -> None:
        """Initialize a fake interface.

        Args:
            name: Interface name.
            node: Owning node; a fresh FakeNode is created when omitted.
            ip: IPv4 address, if the interface has one.
            ip6: IPv6 address, if the interface has one.
            prefix_len: IPv4 prefix length.
            prefix_len6: IPv6 prefix length.
        """
        self.name = name
        self.node = node if node is not None else FakeNode()
        self.params: dict = {}
        self.ip = ip
        self.ip6 = ip6
        self.prefixLen = prefix_len
        self.prefixLen6 = prefix_len6

    def IP(self) -> Optional[str]:
        """Return the IPv4 address, as Mininet's Intf.IP does.

        Returns:
            The address, or None.
        """
        return self.ip

    def IP6(self) -> Optional[str]:
        """Return the IPv6 address, as IPv6Intf.IP6 does.

        Returns:
            The address, or None.
        """
        return self.ip6


class FakeLink:
    """A stand-in for a Mininet link: two interfaces, each knowing its node.

    Attributes:
        intf1: One end of the link.
        intf2: The other end.
    """

    def __init__(self, intf1: FakeIntf, intf2: FakeIntf) -> None:
        """Initialize a link between two interfaces.

        Args:
            intf1: One end of the link.
            intf2: The other end.
        """
        self.intf1 = intf1
        self.intf2 = intf2


#: The key every FakeLab carries unless told otherwise, and the prefix the
#: web tests reach its routes under.
FAKE_LAB_KEY = "fake"
LAB_API = f"/api/labs/{FAKE_LAB_KEY}"


class FakeLab(LabPathResolverMixin):
    """A stand-in for a running Lab: its Nodes, switches, and links.

    Enough of mnIPLab for the readers that walk a Lab — the provisioners,
    the query services, and ``TopologyBuilder`` — without starting one.
    Its Lab directory sits under a runtime root of its own, made when the
    fake is and removed with it, so no test writes into the host's runtime
    root and no two fakes share a tree.

    Attributes:
        hosts: The Lab's host Nodes, routers included; each has this Lab
            as its ``net``, as a Node added through mnIPLab does.
        switches: The Lab's switches.
        links: The Lab's links.
        lab_key: The Lab key, ``FAKE_LAB_KEY`` unless given; the web tests
            address the fake Lab's routes under it.
    """

    def __init__(
        self,
        *hosts: Any,
        switches: Optional[List[Any]] = None,
        links: Optional[List[Any]] = None,
        lab_key: str = FAKE_LAB_KEY,
    ) -> None:
        """Initialize a Lab holding these Nodes.

        Args:
            *hosts: The host Nodes.
            switches: The switches, if any.
            links: The links, if any.
            lab_key: The Lab key.
        """
        self.lab_key = lab_key
        self.hosts: List[Any] = list(hosts)
        self.switches: List[Any] = list(switches or [])
        self.links: List[Any] = list(links or [])
        for host in self.hosts:
            host.net = self
        self._runtime_root = tempfile.mkdtemp(prefix='mniplab-fakelab-')
        weakref.finalize(self, shutil.rmtree, self._runtime_root, ignore_errors=True)

    @property
    def lab_dir(self) -> str:
        """Return this fake's Lab directory, under its own runtime root."""
        return os.path.join(self._runtime_root, 'labs', self.lab_key)

    def stop(self) -> None:
        """Stop the Lab: a fake one has nothing running, so nothing happens."""

    def get(self, name: str) -> Any:
        """Return the Node or switch with this name, as ``mnIPLab.get`` does.

        Args:
            name: The name to look up.

        Returns:
            The matching Node.

        Raises:
            KeyError: If nothing in the Lab has that name.
        """
        for node in self.hosts + self.switches:
            if node.name == name:
                return node
        raise KeyError(name)

    def values(self) -> List[Any]:
        """Return all nodes in the Lab."""
        return self.hosts + self.switches

    def get_link_condition(self, node1: str, node2: str) -> str:
        """Report every link as up; the fake records no Link Conditions.

        Args:
            node1: One end of the link.
            node2: The other end.

        Returns:
            ``'up'``.
        """
        del node1, node2
        return 'up'


class FakeUnixSocket:
    """A stand-in for one ``AF_UNIX`` stream socket handed out by a factory.

    Both control-channel adapters — Kea's JSON channel and Unbound's
    ``UBCT1`` protocol — take a ``socket_factory`` and then ``settimeout``,
    ``connect``, ``sendall``, ``recv`` until done, and ``close``. This fake
    records each of those and plays ``response`` back in ``chunk_size``
    pieces, so a test can exercise an adapter's read loop, its connection
    failures and its timeouts without a real socket or daemon.
    """

    def __init__(
        self,
        response: bytes = b"",
        connect_error: Optional[Exception] = None,
        chunk_size: int = 4,
        timeout_on_recv: bool = False,
    ) -> None:
        """Script the socket's behaviour.

        Args:
            response: What ``recv`` plays back, in order.
            connect_error: Raised from ``connect`` when set.
            chunk_size: How many bytes each ``recv`` returns.
            timeout_on_recv: Raise ``socket.timeout`` once ``response`` is
                exhausted instead of signalling end-of-stream.
        """
        self.response = response
        self.connect_error = connect_error
        self.chunk_size = chunk_size
        self.timeout_on_recv = timeout_on_recv
        self.sent: bytes = b""
        self.connected_to: Optional[str] = None
        self.timeout: Optional[float] = None
        self.closed = False
        self._offset = 0

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_to = path

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def recv(self, _size: int) -> bytes:
        chunk = self.response[self._offset:self._offset + self.chunk_size]
        if not chunk and self.timeout_on_recv:
            raise socket.timeout("timed out")
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeClock:
    """A callable clock a test can move forward by hand.

    Stands in for :func:`time.time` wherever elapsed time is a behaviour under
    test.  Driving the clock explicitly keeps those tests deterministic and free
    of :func:`time.sleep`.

    Attributes:
        now: The current time, in seconds.
    """

    def __init__(self, now: float = 1000.0) -> None:
        """Initialize a fake clock.

        Args:
            now: The time the clock starts at, in seconds.
        """
        self.now = now

    def __call__(self) -> float:
        """Return the current time without advancing it.

        Returns:
            The clock's current time, in seconds.
        """
        return self.now

    def advance(self, seconds: float) -> float:
        """Move the clock forward.

        Args:
            seconds: How far to move the clock forward.

        Returns:
            The clock's new time, in seconds.
        """
        self.now += seconds
        return self.now


class FrozenSessionClockMixin:
    """Test-case mixin that puts Session bookkeeping on a :class:`FakeClock`.

    Installs the clock as ``auth.session_clock`` for the duration of one test,
    empties the Session store either side of it, and restores the real clock on
    cleanup.  Mix it into a ``TestCase`` whose subject is Session ageing.

    Attributes:
        clock: The fake clock driving ``created_at`` and ``last_seen``.
    """

    clock: FakeClock

    def setUp(self) -> None:
        """Freeze the Session clock and start from an empty Session store."""
        super().setUp()  # type: ignore[misc]
        auth.active_sessions.clear()
        self.clock = FakeClock(1000.0)
        original_clock = auth.session_clock
        auth.session_clock = self.clock
        self.addCleanup(setattr, auth, "session_clock", original_clock)  # type: ignore[attr-defined]
        self.addCleanup(auth.active_sessions.clear)  # type: ignore[attr-defined]

    @staticmethod
    def last_seen(session_id: str) -> float:
        """Return a Session's recorded last-seen time.

        Reads ``active_sessions`` directly because every public accessor
        refreshes ``last_seen`` as a side effect and so cannot observe it.

        Args:
            session_id: The Session to inspect.

        Returns:
            The Session's last-seen time, in seconds.
        """
        return auth.active_sessions[session_id]["last_seen"]


class FakeTerminalRegistry:
    """Records which Sessions were asked to give up their Terminals.

    Stands in for the Terminal registry wherever the subject is Session
    teardown rather than a real shell's.  Closing a Terminal for real needs
    a running Lab and a live child process, so a test that only cares *that* a
    destroyed Session releases its Terminals registers one of these as the
    Session layer's Terminal closer.

    Attributes:
        closed: Session tokens passed to close_session_terminals, in order.
        terminals: How many Terminals each Session token owns; a token that is
            absent owns none.
    """

    def __init__(self, terminals: Optional[dict] = None) -> None:
        """Initialize a fake registry.

        Args:
            terminals: How many Terminals each Session token owns.
        """
        self.closed: List[str] = []
        self.terminals: dict = dict(terminals or {})

    async def close_session_terminals(self, session_token: str) -> int:
        """Record the request and report how many Terminals it closed.

        Args:
            session_token: The Session whose Terminals are being closed.

        Returns:
            The number of Terminals the Session owned.
        """
        self.closed.append(session_token)
        return self.terminals.pop(session_token, 0)

    def terminal_count(self, session_token: str) -> int:
        """Report how many Terminals a Session holds, without closing them.

        Args:
            session_token: The Session to check.

        Returns:
            The Session's live Terminal count.
        """
        return self.terminals.get(session_token, 0)

    async def close_lab_terminals(self, key: str, reason: Optional[str] = None) -> None:
        """Ignore a request to close a Lab's Terminals: none are real here.

        Args:
            key: The Lab's key.
            reason: The close reason for the browsers, if any.
        """
        del key, reason

    def terminals_on(self, key: str) -> int:
        """Report that no Terminal is open on any Lab.

        Args:
            key: The Lab's key.
        """
        del key
        return 0


class RecordingWebSocket:
    """A stand-in for a Terminal's browser connection that records its close.

    Attributes:
        closes: Code and reason of each close, in order.
    """

    def __init__(self) -> None:
        """Initialize a connection that has not been closed."""
        self.closes: List[Tuple[int, str]] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Record that the connection was closed.

        Args:
            code: WebSocket close code.
            reason: Human-readable close reason.
        """
        self.closes.append((code, reason))


class SessionCapMixin:
    """Test-case mixin for setting the Session Cap during one test.

    ``session_per_user_cap`` defaults to 0 (off); every cap test overrides it
    explicitly to say which number it is exercising, and this mixin folds the
    patch-and-cleanup ritual shared by every class that does so.
    """

    def cap(self, sessions: int) -> None:
        """Set the Session Cap for the duration of one test.

        Args:
            sessions: The most Sessions one account may hold; 0 disables it.
        """
        from unittest.mock import patch

        patcher = patch.object(auth.settings, "session_per_user_cap", sessions)
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]

class FakeTerminalCloserMixin:
    """Test-case mixin that stands a fake registry in as auth's Terminal closer.

    Destroying a Session reaches the Terminal registry through an injected callback,
    so any test whose subject is Session teardown needs one registered.  Mix
    this in rather than repeating the registration; compose it alongside
    :class:`FrozenSessionClockMixin` when the test also drives Session ageing.

    Attributes:
        terminals: The fake registry recording which Sessions were asked to
            give up their Terminals.
    """

    terminals: FakeTerminalRegistry

    def setUp(self) -> None:
        """Register a fake Terminal closer for the duration of one test."""
        super().setUp()  # type: ignore[misc]
        self.terminals = FakeTerminalRegistry()
        self.install_terminal_closer()

    def install_terminal_closer(self) -> None:
        """Point auth's Terminal closer and counter at this test's fake registry.

        Call again after ``create_app()``: the composition root registers the
        real registry for both, which would otherwise displace the fake.
        """
        auth.set_terminal_closer(self.terminals.close_session_terminals)
        self.addCleanup(auth.set_terminal_closer, None)  # type: ignore[attr-defined]
        auth.set_terminal_counter(self.terminals.terminal_count)
        self.addCleanup(auth.set_terminal_counter, None)  # type: ignore[attr-defined]


class FakeLabAliveMixin:
    """Test-case mixin keeping an empty fake Lab alive under ``FAKE_LAB_KEY``.

    Every Lab route is addressed by key and reaches the registry only for a
    Lab that is alive, so a route test that patches what the Lab answers
    still needs one on the table.  Mix in to the right of
    :class:`LoginClientMixin`.
    """

    def setUp(self) -> None:
        """Attach an empty fake Lab for one test."""
        super().setUp()  # type: ignore[misc]
        from mniplab.webserver import registry

        registry.lab_service.lab = FakeLab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)  # type: ignore[attr-defined]


class ServicesReadyMixin:
    """Test-case mixin under which every launched Service reports itself ready.

    A Provisioner's ``start_*`` returns only once the server it launched
    answers on its control socket. Against a :class:`FakeNode` nothing is
    launched, so the probe would wait out :data:`SERVICE_READY_TIMEOUT` and
    fail the start. This mixin answers the probe for Kea, Knot and Unbound
    alike, so a test about *what* a Provisioner launches never waits on a
    server that was never going to come.
    """

    def setUp(self) -> None:
        """Make every control client's ``is_running`` answer True."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import patch
        from mniplab.dhcp.control_client import KeaControlClient
        from mniplab.dns.control_client import KnotControlClient
        from mniplab.resolver.control_client import UnboundControlClient

        for client_cls in (KeaControlClient, KnotControlClient, UnboundControlClient):
            patcher = patch.object(client_cls, "is_running", return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)  # type: ignore[attr-defined]


class KnotClientStubMixin:
    """Test-case mixin standing a scripted client in for a Node's Knot socket.

    ``DNSQueryService.get_client`` is the seam: it resolves the Node and its
    Service for real and hands back a client on the control socket.  This
    mixin keeps the lookup and replaces only the client, so a test proves
    what the query and record services say to Knot — and what they refuse
    before saying anything — without a socket on disk.

    Attributes:
        client: The stand-in every query and record method will open with
            ``with``; assert on its verbs.
    """

    client: Any

    def setUp(self) -> None:
        """Route every control-socket client through the stub for one test."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import MagicMock, patch
        from mniplab.dns.query import DNSQueryService

        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        stub = self.client

        def get_client(cls: Any, net: Any, node_name: str) -> Tuple[Any, Any, Any]:
            node, service = cls.find_service(net, node_name)
            return node, service, stub

        patcher = patch.object(DNSQueryService, "get_client", classmethod(get_client))
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]


class KeaClientStubMixin:
    """Test-case mixin standing scripted clients in for a Node's Kea sockets.

    ``DHCPQueryService.get_client`` is the seam: it resolves the Node and
    its Service for real, refuses a family the Service does not serve, and
    hands back a client on that family's control socket.  This mixin keeps
    all of that and replaces only the client, so a test proves what the
    lease and Pool services say to Kea — and what they refuse before saying
    anything — without a socket on disk.  A dual-stack Service talks to
    kea-dhcp4 and kea-dhcp6 separately, so there is one stand-in per family.

    Attributes:
        client: The stand-in for kea-dhcp4; assert on its verbs.
        client6: The stand-in for kea-dhcp6.
    """

    client: Any
    client6: Any

    def setUp(self) -> None:
        """Route every Kea control client through the stubs for one test."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import Mock, patch
        from mniplab.dhcp.query import DHCPQueryService

        self.client = Mock()
        self.client6 = Mock()
        stubs = {4: self.client, 6: self.client6}
        real_get_client = DHCPQueryService.get_client

        def get_client(
            cls: Any, net: Any, node_name: str, family: int = 4
        ) -> Tuple[Any, Any, Any]:
            del cls
            node, service, _ = real_get_client(net, node_name, family)
            return node, service, stubs[family]

        patcher = patch.object(DHCPQueryService, "get_client", classmethod(get_client))
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]


class KrillClientStubMixin:
    """Test-case mixin standing a scripted client in for an RPKI CA's Krill socket.

    ``RPKIQueryService.get_client`` is the seam: it resolves the Node and its
    RPKI CA Service for real and hands back a client on the Krill socket.
    This mixin keeps the lookup and replaces only the client, so a test proves
    what the query and object services say to Krill, and what they refuse
    before saying anything, without a socket on disk.

    Attributes:
        client: The stand-in every query and object method will use; assert on
            its verbs, and script its answers or refusals.
        krill: The same stand-in under a name a web test keeps, since
            :class:`LoginClientMixin` takes ``client`` for its HTTP client.
    """

    client: Any
    krill: Any

    def setUp(self) -> None:
        """Route every Krill client through the stub for one test."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import Mock, patch
        from mniplab.rpki.query import RPKIQueryService

        stub = Mock()
        self.client = stub
        self.krill = stub

        def get_client(cls: Any, net: Any, node_name: Optional[str] = None) -> Tuple[Any, Any, Any]:
            node, service = cls.find_ca(net, node_name)
            return node, service, stub

        patcher = patch.object(RPKIQueryService, "get_client", classmethod(get_client))
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]



class RoutinatorClientStubMixin:
    """Test-case mixin standing a scripted client in for a Validator's Routinator.

    ``RPKIQueryService.get_routinator`` is the seam: it resolves the Node and
    its Validator Service for real and hands back a client on the Node's HTTP
    endpoint. This mixin keeps the lookup and replaces only the client, so a
    test proves what the query service makes of Routinator's answers, and what
    it reports when Routinator does not answer, without a Node on the path.

    Attributes:
        client: The stand-in every query method will use; script its ``vrps``,
            ``aspas`` and ``status``, or make one raise ``RoutinatorError``.
        routinator: The same stand-in under a name a web test keeps, since
            :class:`LoginClientMixin` takes ``client`` for its HTTP client.
    """

    client: Any
    routinator: Any

    def setUp(self) -> None:
        """Route every Routinator client through the stub for one test."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import Mock, patch
        from mniplab.rpki.query import RPKIQueryService

        self.client = Mock()
        stub = self.client
        self.routinator = stub

        def get_routinator(cls: Any, net: Any, node_name: Optional[str] = None) -> Tuple[Any, Any, Any]:
            node, service = cls.find_validator(net, node_name)
            return node, service, stub

        patcher = patch.object(RPKIQueryService, "get_routinator", classmethod(get_routinator))
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]


class SpeakerClientStubMixin:
    """Test-case mixin standing a scripted client in for a Speaker's FIFO pair.

    ``SpeakerQueryService.get_client`` is the seam: it resolves the Node for
    real — refusing a Node that is not a Speaker — and hands back the
    Speaker's control client.  This mixin keeps the lookup and replaces only
    the client, so a test proves what the query service makes of ExaBGP's
    answers, and what it reports when the daemon does not answer, without
    a FIFO on disk.

    Attributes:
        pipe: The stand-in; :meth:`answer` scripts it, or set
            ``pipe.execute.side_effect`` to a callable that raises.
    """

    pipe: Any

    def setUp(self) -> None:
        """Route every Speaker control client through the stub for one test."""
        super().setUp()  # type: ignore[misc]
        from unittest.mock import Mock, patch
        from mniplab.exabgp.query import SpeakerQueryService

        self.pipe = Mock()
        stub = self.pipe

        def get_client(cls: Any, net: Any, node_name: str) -> Tuple[Any, Any]:
            return cls.find_speaker(net, node_name), stub

        patcher = patch.object(SpeakerQueryService, "get_client", classmethod(get_client))
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]

    def answer(self, answers: Dict[str, List[str]]) -> None:
        """Script the stub: each command answers with its lines.

        Args:
            answers: Command line → the lines ExaBGP would answer before
                ``done``.
        """
        self.pipe.execute.side_effect = lambda command: list(answers[command])

    def refuse(self, reason: str) -> None:
        """Script the stub to fail every command the way the real client does.

        Args:
            reason: The message, with ``{command}`` standing for the command
                that failed, as ``ExaBGPControlError`` names it.
        """
        from mniplab.errors import ExaBGPControlError

        def fail(command: str) -> None:
            raise ExaBGPControlError(reason.format(command=command), command=command)

        self.pipe.execute.side_effect = fail


class LoginClientMixin:
    """Test-case mixin giving a TestClient over the real app and known accounts.

    Builds the app, registers the accounts in ``ACCOUNTS`` through the real
    registry parser so tests sign in the way a Learner does, and clears the
    username-keyed login rate limiter, which is module-level and would
    otherwise carry attempts between tests.

    Compose it to the left of :class:`FakeTerminalCloserMixin`, which it
    requires: ``create_app`` registers the real Terminal registry as auth's
    closer, so the fake has to be put back afterwards.

    Attributes:
        client: A TestClient over a freshly built app.
    """

    ACCOUNTS: Tuple[Tuple[str, str, str], ...] = (
        ("learner1", "lab12345", "learner"),
        ("learner2", "lab54321", "learner"),
        ("instructor1", "lab12345", "instructor"),
    )

    client: Any

    def setUp(self) -> None:
        """Build the app, register the accounts, and reset the rate limiter."""
        super().setUp()  # type: ignore[misc]
        from fastapi.testclient import TestClient
        from mniplab.webserver import create_app

        self.client = TestClient(create_app())
        self.install_terminal_closer()  # type: ignore[attr-defined]

        original_users = auth.settings.users
        auth.settings.users = ",".join(
            f"{username}:{password}:{role}" for username, password, role in self.ACCOUNTS
        )
        self.addCleanup(  # type: ignore[attr-defined]
            setattr, auth.settings, "users", original_users
        )

        for username, _password, _role in self.ACCOUNTS:
            auth.login_limiter.reset(username)
            self.addCleanup(auth.login_limiter.reset, username)  # type: ignore[attr-defined]

    def log_in(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cookie: Optional[str] = None,
    ) -> Any:
        """Sign in over HTTP, optionally presenting a session cookie.

        Args:
            username: Account to sign in as; the first of ``ACCOUNTS`` by default.
            password: That account's password; the first of ``ACCOUNTS`` by default.
            cookie: A session cookie to present with the request, as a browser
                that already holds a Session would.

        Returns:
            The login response.
        """
        default_username, default_password, _role = self.ACCOUNTS[0]
        # The client keeps a cookie jar across requests; clear it so each login
        # presents exactly the cookie the test asked for, and nothing else.
        self.client.cookies.clear()
        if cookie is not None:
            self.client.cookies.set("session", cookie)
        return self.client.post(
            "/api/login",
            auth=(username or default_username, password or default_password),
        )

    def sign_in(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cookie: Optional[str] = None,
    ) -> str:
        """Sign in, assert the login succeeded, and return the Session's token.

        The same request as :meth:`log_in`, for the test whose subject is what
        the login did to other Sessions rather than the response itself.

        Args:
            username: Account to sign in as; the first of ``ACCOUNTS`` by default.
            password: That account's password; the first of ``ACCOUNTS`` by default.
            cookie: A session cookie to present with the request.

        Returns:
            The token of the Session the login issued.

        Raises:
            AssertionError: If the login was refused.
        """
        response = self.log_in(username=username, password=password, cookie=cookie)
        if response.status_code != 200:
            raise AssertionError(f"login failed: {response.status_code}")
        return response.cookies["session"]

    def open_session(self, role: str = "learner") -> str:
        """Sign in as the first account holding a role and keep its Session.

        The client's cookie jar keeps the Session the login issued, so every
        later request on ``self.client`` is made as that account.

        Args:
            role: The role to sign in with; the account is the first of
                ``ACCOUNTS`` that holds it.

        Returns:
            The Session token the login issued.

        Raises:
            AssertionError: If no account holds the role or the login was
                refused.
        """
        for username, password, account_role in self.ACCOUNTS:
            if account_role == role:
                break
        else:
            raise AssertionError(f"no {role} account in ACCOUNTS")
        response = self.log_in(username=username, password=password)
        if response.status_code != 200:
            raise AssertionError(
                f"login as {role} {username!r} failed: {response.status_code}"
            )
        return response.cookies["session"]

    def authenticates(self, token: str) -> bool:
        """Report whether a token still names a live Session.

        Args:
            token: The Session token to present.

        Returns:
            True if the token was accepted by an authenticated endpoint.
        """
        self.client.cookies.clear()
        self.client.cookies.set("session", token)
        return self.client.get("/api/me").status_code == 200


def cli_output(command: str, line: str = "", net: Optional[Any] = None) -> str:
    """Run one ``mininet-iplab>`` command and return what it printed.

    Builds a real ``mnIPLabCLI`` — Mininet's constructor starts the command
    loop unless handed a script, and an empty one returns at once — then runs
    ``do_<command>`` with everything the CLI writes through ``info`` and
    ``output`` collected into one string. Capture starts after construction,
    so the ``*** Starting CLI:`` banner is not part of the result. The CLI
    reads a null stdin because Mininet polls the descriptor, and pytest's
    captured ``sys.stdin`` has none.

    Args:
        command: The command name without its ``do_`` prefix.
        line: The argument line, as typed after the command.
        net: The Lab the CLI drives; an empty :class:`FakeLab` when omitted.

    Returns:
        Everything the command printed, in order.
    """
    written: List[str] = []
    with open(os.devnull, encoding="utf-8") as stdin:
        with patch("mniplab.cli.info"):
            cli = mnIPLabCLI(
                net if net is not None else FakeLab(), stdin=stdin, script=os.devnull
            )
        with patch("mniplab.cli.info", written.append), patch(
            "mniplab.cli.output", written.append
        ):
            getattr(cli, f"do_{command}")(line)
    return "".join(written)


class FakeCaptureProcess:
    """A stand-in for a Packet Capture's tshark: a pipe a test writes PDML into.

    Attributes:
        stdout: The read end, as the CaptureService reads a real tshark's.
        pid: A pid no process has, so nothing real is ever signalled.
    """

    def __init__(self) -> None:
        """Open the pipe standing in for tshark's stdout."""
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.pid = -1
        self._writers: List[threading.Thread] = []

    def emit(self, data: bytes) -> None:
        """Write PDML as tshark would, without blocking the caller.

        Written from a thread: a pipe holds 64 KiB and the service reads on
        the test's own event loop, so a blocking write would wait on a read
        that cannot happen until the write returns.

        Args:
            data: The bytes tshark would have written.
        """
        fd = self._write_fd

        def write() -> None:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]

        writer = threading.Thread(target=write)
        writer.start()
        self._writers.append(writer)

    def exit(self) -> None:
        """End the stream, as tshark exiting does."""
        for writer in self._writers:
            writer.join()
        if self._write_fd >= 0:
            os.close(self._write_fd)
            self._write_fd = -1


class FakeCaptureSpawner:
    """Records the Captures it was asked to start and stop; starts nothing.

    Attributes:
        commands: Every CaptureCommand spawned, in order.
        processes: The fake process handed back for each.
        stopped: The processes stopped, in order.
        kept: For each spawn, the pcap paths it was told to leave alone.
        loop: The event loop the service spawned on, so a test driving a
            live server from another thread can stop Captures on it.
    """

    def __init__(self) -> None:
        """Initialize with nothing spawned."""
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.commands: List[Any] = []
        self.processes: List[FakeCaptureProcess] = []
        self.stopped: List[FakeCaptureProcess] = []
        self.kept: List[List[str]] = []

    def spawn(self, command: Any, keep: Any = ()) -> FakeCaptureProcess:
        """Record a command and hand back a fake process for it."""
        self.loop = asyncio.get_running_loop()
        self.commands.append(command)
        self.kept.append(list(keep))
        process = FakeCaptureProcess()
        self.processes.append(process)
        return process

    def stop(self, process: FakeCaptureProcess) -> None:
        """Record a stop and end the fake process's stream."""
        self.stopped.append(process)
        process.exit()


# Bytes in a paste far larger than a pty(7) holds for a program not reading.
LARGE_PASTE = 64 * 1024

# Seconds before the program in a Terminal starts reading its input.
PROGRAM_BUSY_FOR = 1.0


def drain_after(fd: int, delay: float, wanted: int, into: bytearray) -> threading.Thread:
    """Start a thread that reads ``wanted`` bytes from ``fd`` after ``delay``.

    It stands in for a full-screen program that is busy at first and then
    reads everything typed into it.

    Args:
        fd: The pty(7) slave the program reads.
        delay: Seconds to wait before the first read.
        wanted: Bytes to read before stopping.
        into: Buffer the bytes read are appended to.

    Returns:
        The started thread.
    """
    def drain() -> None:
        time.sleep(delay)
        while len(into) < wanted:
            ready, _, _ = select.select([fd], [], [], 5.0)
            if not ready:
                return
            into.extend(os.read(fd, 65536))

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return thread


async def longest_stall(until: Callable[[], bool], limit: float) -> float:
    """Measure the longest gap between event-loop turns while ``until`` is false.

    Args:
        until: Returns True once the work being watched has finished.
        limit: Seconds to watch at most.

    Returns:
        The longest gap, in seconds, between two turns of the loop.
    """
    loop = asyncio.get_running_loop()
    start = last = loop.time()
    worst = 0.0
    while not until() and last - start < limit:
        await asyncio.sleep(0.01)
        now = loop.time()
        worst = max(worst, now - last)
        last = now
    return worst
