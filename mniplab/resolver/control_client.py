"""UnboundControlClient: UNIX control-channel adapter for Unbound.

Unbound's remote control speaks a line protocol over a UNIX domain socket: the
client sends ``UBCT1 <command>\\n``, the daemon streams its answer and closes
the connection.  Over a UNIX socket Unbound disables TLS on its own, so this
adapter needs neither certificates nor an ``unbound-control`` subprocess — a
plain ``AF_UNIX`` stream socket from the root namespace reaches the daemon
running inside the Node's namespaces through the bind-mounted socket path.
"""

from __future__ import annotations

import socket
from typing import Any, Callable, Dict, Optional

from mniplab.errors import ResolverControlError

PROTOCOL_BANNER = "UBCT1"
RECV_CHUNK_BYTES = 65536


class UnboundControlClient:
    """Adapter for one Resolver's Unbound control socket.

    Each command is a self-contained exchange — Unbound closes the connection
    once it has streamed its answer — so this client holds no connection
    between calls and is safe to construct per request.

    Attributes:
        socket_path: Host-side path of the Resolver's control socket
            (``labs/<key>/nodes/<node>/run/unbound/unbound.sock`` under the
            runtime root).
        timeout: Socket operation timeout in seconds.
    """

    def __init__(
        self,
        socket_path: str,
        timeout: float = 5.0,
        socket_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Initialize the client against one Unbound control socket.

        Args:
            socket_path: Host-side path of Unbound's control UNIX socket.
            timeout: Control socket timeout in seconds.
            socket_factory: Optional factory returning a connected-capable
                socket object (defaults to an ``AF_UNIX`` stream socket).
        """
        self.socket_path = socket_path
        self.timeout = timeout
        self._socket_factory = socket_factory or (
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        )

    def execute(self, command: str, *args: str) -> str:
        """Send one control command and return Unbound's full response text.

        Args:
            command: The Unbound control command (e.g. ``'status'``).
            *args: Command arguments appended after the command name.

        Returns:
            The response body Unbound streamed before closing the connection.

        Raises:
            ResolverControlError: If an argument contains a newline, the socket
                is unreachable or times out, or Unbound answers with an error.
        """
        line = " ".join([command, *args])
        if "\n" in line or "\r" in line:
            raise ResolverControlError(
                f"Control command {command!r} contains a line break, which would "
                f"inject a second command into the control channel.",
                command=command,
                socket_path=self.socket_path,
            )

        response = self._exchange(line, command)
        if response.startswith("error"):
            raise ResolverControlError(
                f"Unbound rejected '{line}': {response.strip()}",
                command=command,
                socket_path=self.socket_path,
            )
        return response

    def _exchange(self, line: str, command: str) -> str:
        """Run one connect/send/read-to-EOF exchange over the control socket.

        Args:
            line: The full command line to send, without its trailing newline.
            command: The command name, for error reporting.

        Returns:
            The decoded response body.

        Raises:
            ResolverControlError: If the socket is unreachable or times out.
        """
        sock = self._socket_factory()
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall(f"{PROTOCOL_BANNER} {line}\n".encode())
            chunks = []
            while True:
                chunk = sock.recv(RECV_CHUNK_BYTES)
                if not chunk:
                    break
                chunks.append(chunk)
        except (OSError, socket.timeout) as exc:
            raise ResolverControlError(
                f"Failed to communicate with the Unbound control socket at "
                f"{self.socket_path}: {exc}",
                command=command,
                socket_path=self.socket_path,
            ) from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass

        return b"".join(chunks).decode("utf-8", errors="replace")

    def is_running(self) -> bool:
        """Report whether the Resolver's daemon answers on its control socket.

        Returns:
            True if a ``status`` command succeeded, False otherwise.
        """
        try:
            self.status()
        except ResolverControlError:
            return False
        return True

    def status(self) -> str:
        """Return Unbound's status block (version, uptime, loaded modules).

        Returns:
            The raw status text.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        return self.execute("status")

    def stats_noreset(self) -> Dict[str, float]:
        """Return live query counters without resetting them.

        Returns:
            Counter names mapped to numbers, integers where Unbound reported
            one. Unparseable lines are skipped.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        stats: Dict[str, float] = {}
        for raw_line in self.execute("stats_noreset").splitlines():
            key, sep, value = raw_line.partition("=")
            if not sep:
                continue
            try:
                number = float(value)
            except ValueError:
                continue
            stats[key.strip()] = int(number) if number.is_integer() else number
        return stats

    def dump_cache(self) -> str:
        """Return Unbound's raw RRset and message cache dump.

        Returns:
            The raw dump text, for a Cache Entry parser to consume.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        return self.execute("dump_cache")

    def reload_keep_cache(self) -> None:
        """Re-read the configuration without invalidating cached entries.

        Returns only once the daemon is serving again: the reload command
        itself answers before the workers have restarted, and a query in
        that window is dropped, so a ``status`` round-trip — which the daemon
        answers only after the reload — closes the gap.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        self.execute("reload_keep_cache")
        self.execute("status")

    def flush_zone(self, zone: str) -> str:
        """Evict every cached entry at or below one domain.

        Args:
            zone: The domain whose subtree should be evicted.

        Returns:
            Unbound's confirmation line, naming how much was removed.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        return self.execute("flush_zone", zone)

    def flush_name(self, name: str) -> str:
        """Evict every cached entry for exactly one domain name.

        Args:
            name: The domain name to evict.

        Returns:
            Unbound's confirmation line.

        Raises:
            ResolverControlError: If the control exchange fails.
        """
        return self.execute("flush", name)
