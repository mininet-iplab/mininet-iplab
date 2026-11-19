"""KeaControlClient: the only code in the project that talks to a Kea
server's control socket.

"The Control Agent" is Kea's control-*channel*
capability, not the separate ``kea-ctrl-agent`` HTTP process: ``kea-dhcp4``
itself opens a unix-domain ``control-socket`` (the same path
:class:`mniplab.dhcp.config.KeaConfigBuilder` renders into its
``Dhcp4.control-socket``), and everything this client needs goes straight to
that socket. Isolating the socket I/O here, mirroring
:class:`mniplab.docker_client.DockerClient`, means nothing else in the
codebase needs to know the wire format or open the socket itself.

One client serves both families: the framing, incremental JSON parse and
error mapping below are identical for ``kea-dhcp4`` and ``kea-dhcp6`` — only
the command name (``lease4-*``/``lease6-*``) and the response's fields
differ — so the family is passed at construction rather than duplicated into
two classes.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Callable, Dict, List, Optional

from mniplab.errors import ConfigurationError

# Kea's control-channel result codes (kea.readthedocs.io/en/kea-2.2.0/arm/ctrl-channel.html).
_RESULT_SUCCESS = 0
_RESULT_EMPTY = 3


class KeaControlClient:
    """Adapter for one Kea server's unix control-channel socket.

    A fresh connection is opened per command: Kea's control channel does not
    require a session, and a short-lived connection means a stale or
    not-yet-started Kea fails the single command in flight rather than
    poisoning a client held open across calls.

    Attributes:
        socket_path: Host-side path of the control socket, reached through
            the same bind-mount :meth:`mniplab.provisioners.DHCPProvisioner
            .bind_service_directories` sets up for the serving Node.
        family: The address family served: 4 talks to kea-dhcp4 (``lease4-*``
            commands), 6 to kea-dhcp6 (``lease6-*``).
    """

    RECV_CHUNK_BYTES = 4096
    TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        socket_path: str,
        family: int = 4,
        socket_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Initialize the client against one Service's control socket.

        Args:
            socket_path: Host-side path of the Kea server's control socket.
            family: The address family the server serves (4 or 6).
            socket_factory: Optional factory returning a connectable socket
                object (defaults to an ``AF_UNIX`` stream socket), the same
                seam :class:`~mniplab.resolver.control_client.UnboundControlClient`
                offers its tests.
        """
        self.socket_path = socket_path
        self.family = family
        self._socket_factory = socket_factory or (
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        )

    @property
    def server_name(self) -> str:
        """The Kea server process this client talks to (``kea-dhcp4``/``kea-dhcp6``)."""
        return f"kea-dhcp{self.family}"

    def _connect(self) -> socket.socket:
        """Open a unix-domain connection to the control socket.

        Returns:
            A connected, timeout-configured socket from ``socket_factory``.
        """
        sock = self._socket_factory()
        sock.settimeout(self.TIMEOUT_SECONDS)
        sock.connect(self.socket_path)
        return sock

    def send_command(
        self,
        command: str,
        arguments: Optional[Dict[str, Any]] = None,
        service: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Send one command to kea-dhcp4's control channel.

        Args:
            command: The control-channel command name, e.g. ``lease4-get-all``.
            arguments: The command's ``arguments`` payload, if any.
            service: The ``service`` list Kea's control channel accepts;
                omitted unless a caller needs it.

        Returns:
            Kea's parsed JSON response: at least a ``result`` code, usually
            with ``text`` and, on success, ``arguments``.

        Raises:
            ConfigurationError: kea-dhcp4 is not reachable at
                ``socket_path`` — not running, or its socket does not exist
                yet — or its response could not be read or parsed as JSON.
        """
        request: Dict[str, Any] = {"command": command}
        if arguments is not None:
            request["arguments"] = arguments
        if service is not None:
            request["service"] = service

        try:
            sock = self._connect()
        except OSError as exc:
            raise ConfigurationError(
                f"Could not reach Kea's control socket at {self.socket_path}: "
                f"{exc}. Is {self.server_name} running for this Service?"
            ) from exc

        try:
            sock.sendall(json.dumps(request).encode('utf-8'))
            return self._read_response(sock)
        except OSError as exc:
            raise ConfigurationError(
                f"Lost connection to Kea's control socket at {self.socket_path}: {exc}"
            ) from exc
        finally:
            sock.close()

    def _read_response(self, sock: socket.socket) -> Dict[str, Any]:
        """Read and parse one JSON response from an open control connection.

        Kea parses a command as soon as the bytes received so far form a
        complete JSON document, without waiting for the connection to close
        (kea.readthedocs.io/en/kea-2.2.0/arm/ctrl-channel.html) — the client
        side mirrors that by attempting to parse after every chunk, rather
        than reading until end-of-stream.

        Args:
            sock: The connected, already-sent-to socket to read from.

        Returns:
            The parsed JSON response.

        Raises:
            ConfigurationError: The socket timed out or closed before a
                complete, parseable JSON response arrived.
        """
        buffer = b''
        while True:
            try:
                chunk = sock.recv(self.RECV_CHUNK_BYTES)
            except socket.timeout as exc:
                raise ConfigurationError(
                    f"Timed out waiting for Kea's response on {self.socket_path}"
                ) from exc

            if not chunk:
                break

            buffer += chunk
            parsed = self._try_parse(buffer)
            if parsed is not None:
                return parsed

        if not buffer:
            raise ConfigurationError(
                f"Kea closed its control socket at {self.socket_path} "
                "without sending a response"
            )

        parsed = self._try_parse(buffer)
        if parsed is None:
            raise ConfigurationError(
                f"Kea's response on {self.socket_path} was not valid JSON"
            )
        return parsed

    @staticmethod
    def _try_parse(buffer: bytes) -> Optional[Dict[str, Any]]:
        """Attempt to parse a byte buffer as one complete JSON object.

        Args:
            buffer: Bytes accumulated from the control socket so far.

        Returns:
            The parsed object, or ``None`` if ``buffer`` is not (yet, or
            ever) valid JSON.
        """
        try:
            return json.loads(buffer.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def get_all_leases(self) -> List[Dict[str, Any]]:
        """Return every Lease this Service's Kea server currently holds.

        Requires the ``libdhcp_lease_cmds.so`` hook
        (:func:`mniplab.dhcp.config.resolve_lease_cmds_hook`) to already be
        loaded in the running Kea's configuration — every configuration
        :class:`mniplab.dhcp.config.KeaConfigBuilder` renders includes it.

        Returns:
            One dict per Lease, exactly as ``lease4-get-all`` (family 4) or
            ``lease6-get-all`` (family 6) reports it: family 4 identifies a
            Lease by ``hw-address``, family 6 by ``duid``. Empty when the
            Service holds no Leases yet.

        Raises:
            ConfigurationError: The server is unreachable, or it rejected
                the command — most notably ``'leaseX-get-all' command not
                supported`` if the lease-commands hook is missing.
        """
        response = self.send_command(f'lease{self.family}-get-all')
        result = response.get('result')

        if result == _RESULT_EMPTY:
            return []

        if result != _RESULT_SUCCESS:
            text = response.get('text', 'unknown error')
            raise ConfigurationError(f"Kea rejected lease{self.family}-get-all: {text}")

        return response.get('arguments', {}).get('leases', [])

    def _send_config_command(
        self, command: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send a configuration command to Kea and return the response if successful.

        Args:
            command: Kea control command name (e.g. ``'config-test'``, ``'config-set'``).
            config: The configuration dictionary.

        Returns:
            Kea's parsed JSON response.

        Raises:
            ConfigurationError: If kea-dhcp4 is unreachable or rejected the command.
        """
        response = self.send_command(command, arguments=config)
        result = response.get('result')
        if result != _RESULT_SUCCESS:
            text = response.get('text', 'unknown error')
            raise ConfigurationError(f"Kea rejected configuration: {text}")
        return response

    def test_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Test a prospective configuration with Kea without applying it.

        Args:
            config: The configuration dictionary (e.g. ``{"Dhcp4": {...}}``).

        Returns:
            Kea's parsed JSON response.

        Raises:
            ConfigurationError: If kea-dhcp4 is unreachable or rejected the
                configuration.
        """
        return self._send_config_command('config-test', config)

    def set_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a new configuration to the running Kea without restarting it.

        Args:
            config: The configuration dictionary (e.g. ``{"Dhcp4": {...}}``).

        Returns:
            Kea's parsed JSON response.

        Raises:
            ConfigurationError: If kea-dhcp4 is unreachable or rejected the
                configuration.
        """
        return self._send_config_command('config-set', config)


    def is_running(self) -> bool:
        """Report whether this Service's Kea server answers on its control socket.

        A ``status-get`` round-trip is the probe: it succeeds only once the
        server has opened its socket and is processing commands, which is
        what "ready" means to every other caller of this client.

        Returns:
            True if ``status-get`` answered with result 0, False otherwise.
        """
        try:
            response = self.send_command('status-get')
        except ConfigurationError:
            return False
        return response.get('result') == 0
