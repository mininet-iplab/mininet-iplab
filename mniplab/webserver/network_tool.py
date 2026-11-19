"""Runs a Network Tools ping, traceroute or iperf3 test whose output streams as it comes.

The command runs in the web server process and enters the source Node by
its Namespace Anchor pid through :func:`mniplab.namespace.build_nsenter_argv`
— the recipe a Terminal and a Packet Capture use — so it leaves the Lab's
operation lock free for as long as it runs, and a Learner can stop it.

Entering the Node's PID namespace also ties the command's life to the
Node's: when the Lab stops and the Namespace Anchor exits, the kernel ends
every process left in that namespace, this one included.

A bandwidth test is two such commands: an iperf3 server in one Node, started
first and quietly, and its client in another.  The server is ended by the
caller once the client is done, since ``iperf3 -s -1`` only exits after a
client it served.

:func:`build_tool_argv` and :func:`build_bandwidth_argv` are pure and hold
every decision about what is run; :func:`start_tool` and :func:`signal_tool`
are the only code here that touches a process.
"""

from __future__ import annotations

import asyncio
import os
import re
from decimal import Decimal
from typing import List, Optional, Tuple

from mniplab.labhandle import NodeAttachment
from mniplab.namespace import build_nsenter_argv

TOOLS = ("ping", "traceroute")

# The bandwidth tests, each a server in one Node and a client in another.
BANDWIDTH_TOOLS = ("iperf3",)

MAX_PING_COUNT = 100

MAX_IPERF3_SECONDS = 60

# A bandwidth is a number, with a decimal part, then K, M or G (upper case).
MAX_BANDWIDTH_BITS = 1_000_000_000
BANDWIDTH_FACTORS = {"K": 10**3, "M": 10**6, "G": 10**9}
BANDWIDTH_MESSAGE = "Bandwidth must be a number with K, M or G, up to 1G, such as 10M"
_BANDWIDTH = re.compile(r"([0-9]+(?:\.[0-9]+)?)([KMG])")

# Seconds a stopped tool is given to print its summary and exit on SIGINT
# before its group is killed.
STOP_GRACE_PERIOD = 2.0

# Seconds a bandwidth test's server is given to listen before its client
# connects.
SERVER_START_DELAY = 0.5

# An address or a host name, as one argument that cannot be read as an
# option: letters, digits and the punctuation addresses and names use,
# including ``%`` for an IPv6 zone.
_SAFE_TARGET = re.compile(r"^[A-Za-z0-9_.:%][A-Za-z0-9_.:%-]{0,252}$")


def build_tool_argv(
    attachment: NodeAttachment,
    tool: str,
    target: str,
    *,
    count: int = 4,
    ipv6: bool = False,
    resolve: bool = False,
) -> List[str]:
    """Build the command that runs one Network Tools test on a Node.

    Args:
        attachment: The source Node's name and Namespace Anchor pid.
        tool: ``"ping"`` or ``"traceroute"``.
        target: The address or name to test, as the Lab resolved it.
        count: Echo requests a ping sends, 1 to :data:`MAX_PING_COUNT`.
        ipv6: Test over IPv6.
        resolve: Let traceroute look up each hop's name; numeric otherwise.

    Returns:
        The full command, starting with ``nsenter``.

    Raises:
        ValueError: The tool is unknown, the count out of range, or the
            target not one plain address or name.
    """
    if tool not in TOOLS:
        raise ValueError(f"Unknown Network Tools test: {tool!r}")
    if not _SAFE_TARGET.match(target):
        raise ValueError(f"Not a valid address or host name: {target!r}")
    if tool == "ping":
        if not 1 <= count <= MAX_PING_COUNT:
            raise ValueError(f"Packet Count must be between 1 and {MAX_PING_COUNT}")
        command = ["ping"] + (["-6"] if ipv6 else []) + ["-c", str(count), target]
    else:
        command = ["traceroute6" if ipv6 else "traceroute"] + ([] if resolve else ["-n"]) + [target]
    return build_nsenter_argv(attachment.pid, command)


def build_bandwidth_argv(
    server: NodeAttachment,
    client: NodeAttachment,
    tool: str,
    server_address: str,
    *,
    seconds: int = 10,
    protocol: str = "tcp",
    json_output: bool = False,
    bandwidth: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Build the two commands that run one bandwidth test.

    The server runs ``iperf3 -s -1``: it serves one client and exits. The
    client flushes each line as iperf3 prints it, unless JSON is asked for,
    when its one summary comes at the end.

    Args:
        server: The server Node's name and Namespace Anchor pid.
        client: The client Node's name and Namespace Anchor pid.
        tool: ``"iperf3"``.
        server_address: The address the client connects to, as the Lab gave it.
        seconds: The test's length, 1 to :data:`MAX_IPERF3_SECONDS`.
        protocol: ``"tcp"`` or ``"udp"``.
        json_output: Print iperf3's JSON rather than its lines.
        bandwidth: The rate, a number with K, M or G, above zero and at most
            1G, such as ``"10M"``; ``None`` for iperf3's default. Passed to
            the client as ``-b``, for TCP and UDP.

    Returns:
        The server's command and the client's, each starting with ``nsenter``.

    Raises:
        ValueError: The tool is unknown, the duration or protocol out of
            range, the bandwidth not one of the above, or the address not one
            plain address or name.
    """
    if tool not in BANDWIDTH_TOOLS:
        raise ValueError(f"Unknown Network Tools test: {tool!r}")
    if not _SAFE_TARGET.match(server_address):
        raise ValueError(f"Not a valid address or host name: {server_address!r}")
    if protocol not in ("tcp", "udp"):
        raise ValueError("Protocol must be 'tcp' or 'udp'")
    if bandwidth is not None:
        match = _BANDWIDTH.fullmatch(bandwidth)
        if match is None:
            raise ValueError(BANDWIDTH_MESSAGE)
        bits = Decimal(match.group(1)) * BANDWIDTH_FACTORS[match.group(2)]
        if not 0 < bits <= MAX_BANDWIDTH_BITS:
            raise ValueError(BANDWIDTH_MESSAGE)
    if not 1 <= seconds <= MAX_IPERF3_SECONDS:
        raise ValueError(f"Duration must be between 1 and {MAX_IPERF3_SECONDS} seconds")
    command = ["iperf3", "-c", server_address, "-t", str(seconds)]
    if protocol == "udp":
        command.append("-u")
    command.append("-J" if json_output else "--forceflush")
    if bandwidth is not None:
        command += ["-b", bandwidth]
    return (
        build_nsenter_argv(server.pid, ["iperf3", "-s", "-1"]),
        build_nsenter_argv(client.pid, command),
    )


async def start_tool(argv: List[str], *, quiet: bool = False) -> "asyncio.subprocess.Process":
    """Start a test with its output, errors included, on one pipe.

    The process leads its own group: ``nsenter`` forks into the Node's PID
    namespace, so stopping a test signals the group, never one pid.

    Args:
        argv: What :func:`build_tool_argv` or :func:`build_bandwidth_argv` built.
        quiet: Discard the output rather than pipe it, for a process nobody
            reads, such as an iperf3 server: an unread pipe could fill.

    Returns:
        The running process; its stdout carries the tool's output, unless quiet.
    """
    if quiet:
        stdout = stderr = asyncio.subprocess.DEVNULL
    else:
        stdout, stderr = asyncio.subprocess.PIPE, asyncio.subprocess.STDOUT
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )


def signal_tool(process: "asyncio.subprocess.Process", sig: int) -> None:
    """Send ``sig`` to a test's process group, if it is still there.

    Args:
        process: What :func:`start_tool` returned.
        sig: ``signal.SIGINT`` to stop it as Ctrl+C would, so ping still
            prints its summary; ``signal.SIGKILL`` to end it outright.
    """
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass

